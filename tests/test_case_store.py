"""The case directory: what gets written, what gets refused.

Two properties matter more than the rest and are asserted directly rather than
implied:

* **A case that is not there is an error, not an empty case.** The family's most
  expensive recurring defect is an empty result read as "no problem"; a store
  that answers "no evidence" for a case id that was never opened would put that
  shape at the base of the investigation layer.
* **Opening a case never overwrites one.** The ledger's whole value is that it
  records what actually happened, which it cannot do if a second open silently
  replaces the first.
"""

from __future__ import annotations

import json
import stat

import pytest

from vmware_debug.ops.cases.ids import CaseIdError
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import (
    CaseExists,
    CaseNotFound,
    case_dir,
    create_case,
    list_cases,
    load_case,
)

AT = "2026-08-28T09:15:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))
    return tmp_path


def a_scope(summary="vSAN latency on cluster-01", **kw):
    return Scope(
        summary=summary,
        objects=kw.get("objects", ("cluster-01", "esxi-03.lab")),
        window_start=kw.get("window_start", "2026-08-28T08:00:00Z"),
        window_end=kw.get("window_end", "2026-08-28T09:00:00Z"),
        product_versions=kw.get("product_versions", {"vsphere": "8.0.3"}),
        determined_by=kw.get("determined_by", "user report + vCenter alarm ID 42"),
    )


class TestCreate:
    def test_returns_a_usable_case_id(self):
        case = create_case(a_scope(), at=AT)
        assert case.case_id.startswith("20260828-091500-vsan-latency")

    def test_writes_the_whole_skeleton(self):
        case = create_case(a_scope(), at=AT)
        d = case_dir(case.case_id)
        for name in (
            "scope.json",
            "plan.jsonl",
            "gaps.json",
            "case.json",
            "timeline.md",
            "hypotheses.md",
            "conclusion.md",
        ):
            assert (d / name).exists(), f"{name} missing from the skeleton"
        assert (d / "evidence").is_dir()

    def test_scope_round_trips(self):
        case = create_case(a_scope(), at=AT)
        assert load_case(case.case_id).scope == a_scope()

    def test_starts_in_the_open_state(self):
        assert load_case(create_case(a_scope(), at=AT).case_id).state == "open"

    def test_the_markdown_files_explain_themselves_when_empty(self):
        """An empty file and a file that says 'nothing here yet' read very
        differently to whoever opens the folder."""
        d = case_dir(create_case(a_scope(), at=AT).case_id)
        for name in ("timeline.md", "hypotheses.md", "conclusion.md"):
            body = (d / name).read_text(encoding="utf-8")
            assert body.strip(), f"{name} is blank"
            assert body.lstrip().startswith("#"), f"{name} has no heading"

    def test_jsonl_and_json_start_valid_not_blank(self):
        d = case_dir(create_case(a_scope(), at=AT).case_id)
        assert (d / "plan.jsonl").read_text(encoding="utf-8") == ""
        assert json.loads((d / "gaps.json").read_text(encoding="utf-8")) == {"gaps": []}

    def test_refuses_to_overwrite_an_existing_case(self):
        case = create_case(a_scope(), at=AT)
        (case_dir(case.case_id) / "evidence" / "E001.json").write_text("{}", encoding="utf-8")
        with pytest.raises(CaseExists) as e:
            create_case(a_scope(), at=AT)
        assert case.case_id in str(e.value)
        assert (case_dir(case.case_id) / "evidence" / "E001.json").exists()

    def test_the_directory_is_not_world_readable(self):
        """Cases hold customer topology, hostnames and log excerpts."""
        d = case_dir(create_case(a_scope(), at=AT).case_id)
        assert stat.S_IMODE(d.stat().st_mode) == 0o700

    def test_rejects_a_scope_with_no_summary(self):
        with pytest.raises(ValueError, match="summary"):
            create_case(a_scope(summary="   "), at=AT)

    def test_rejects_a_scope_that_records_no_basis(self):
        """'How was this scope determined' is the first of the eight steps.
        A case that cannot answer it is not a case."""
        with pytest.raises(ValueError, match="determined_by"):
            create_case(a_scope(determined_by=""), at=AT)


class TestLoad:
    def test_a_missing_case_raises_rather_than_returning_an_empty_one(self):
        with pytest.raises(CaseNotFound):
            load_case("20260828-091500-does-not-exist")

    def test_the_error_says_how_to_find_the_real_id(self):
        with pytest.raises(CaseNotFound) as e:
            load_case("20260828-091500-does-not-exist")
        msg = str(e.value)
        assert "20260828-091500-does-not-exist" in msg
        assert "case_list" in msg

    def test_a_traversing_id_is_refused_before_it_touches_the_disk(self):
        with pytest.raises(CaseIdError):
            load_case("../../../etc")

    def test_a_case_whose_scope_is_unreadable_is_an_error_not_an_empty_scope(self):
        case = create_case(a_scope(), at=AT)
        (case_dir(case.case_id) / "scope.json").write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(ValueError, match="scope.json"):
            load_case(case.case_id)


class TestList:
    def test_empty_when_nothing_has_been_opened(self):
        assert list_cases() == ()

    def test_newest_first(self):
        old = create_case(a_scope("older"), at="2026-08-27T09:00:00Z")
        new = create_case(a_scope("newer"), at="2026-08-28T09:00:00Z")
        assert [c.case_id for c in list_cases()] == [new.case_id, old.case_id]

    def test_a_stray_directory_does_not_break_the_listing(self):
        """Someone will drop a folder in here. One unreadable entry must not
        take the listing down, and must not vanish silently either."""
        good = create_case(a_scope(), at=AT)
        (case_dir(good.case_id).parent / "not-a-case").mkdir()
        listed = list_cases()
        assert [c.case_id for c in listed if c.state != "unreadable"] == [good.case_id]
        assert any(c.case_id == "not-a-case" and c.state == "unreadable" for c in listed)


def test_the_grading_rules_file_lives_inside_the_package():
    """Non-.py files vanish from wheels quietly (踩坑 #16, where mcp_server/ was
    dropped from four packages and the MCP entry point was dead on install).
    The rules file is included because it sits under the package directory that
    hatchling ships; this asserts it stays there."""
    from vmware_debug.ops.cases.grading import PACKAGED_RULES

    import vmware_debug

    pkg = __import__("pathlib").Path(vmware_debug.__file__).parent
    assert PACKAGED_RULES.is_file()
    assert pkg in PACKAGED_RULES.parents, (
        f"{PACKAGED_RULES} is outside {pkg}; it will not be packaged."
    )
