"""The MCP surface over the case ledger.

These tools are what an agent actually sees, so the properties asserted here are
about the shape of what comes back, not about the ledger logic underneath
(that is tested against the ops layer directly).

Two of them matter most:

* **A failed call never comes back looking like an empty success.** A tool that
  returns ``{"items": []}`` when it actually failed is the family's costliest
  shape, wearing an envelope.
* **``grade_case`` still cannot be told an answer.** The ops function refuses
  one; so must the tool wrapping it, which is the surface a model can reach.
"""

from __future__ import annotations

import inspect

import pytest

from vmware_debug.mcp import tools as t

AT = "2026-08-28T09:15:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def case():
    cid = t.open_case(summary="vsan latency on cluster-01", determined_by="alarm 42", at=AT)[
        "case_id"
    ]
    t.record_case_hypothesis(case_id=cid, statement="failing device")
    return cid


def submit(case, skill="vmware-monitor", **kw):
    return t.submit_case_evidence(
        case_id=case,
        source_skill=skill,
        source_tool="list_events",
        query={"target": "vc-01"},
        summary="6 scsi warnings",
        fetched_at="2026-08-28T09:20:00Z",
        **kw,
    )


class TestOpen:
    def test_returns_the_id_and_where_the_folder_is(self, case):
        assert case.startswith("20260828-091500-vsan-latency")

    def test_tells_the_agent_what_to_do_next(self):
        r = t.open_case(summary="x", determined_by="y", at=AT)
        assert "submit_case_evidence" in r["next"]

    def test_states_the_reachable_ceiling_up_front(self):
        """Design section 5: say what grade this environment can reach before
        the investigation starts, not after it stalls."""
        r = t.open_case(summary="x", determined_by="y", at=AT)
        assert r["ceiling"] == "probable"

    def test_a_scope_with_no_basis_is_refused_with_a_usable_message(self):
        with pytest.raises(ValueError, match="determined_by"):
            t.open_case(summary="x", determined_by="", at=AT)


class TestListAndGet:
    def test_list_uses_the_family_envelope(self, case):
        r = t.list_cases()
        assert set(r) >= {"items", "returned", "limit", "total", "truncated", "hint"}
        assert r["total"] == 1

    def test_list_is_empty_before_anything_is_opened(self):
        assert t.list_cases()["items"] == []

    def test_get_returns_the_ledger_counts_not_the_whole_ledger(self, case):
        submit(case)
        r = t.get_case(case_id=case)
        assert r["evidence_count"] == 1
        assert r["scope"]["summary"] == "vsan latency on cluster-01"

    def test_get_on_a_missing_case_raises_a_teaching_error(self):
        with pytest.raises(Exception, match="list_cases"):
            t.get_case(case_id="20260828-091500-nope")


class TestSubmitEvidence:
    def test_returns_the_assigned_id(self, case):
        assert submit(case)["evidence_id"] == "E001"

    def test_reports_the_grade_after_each_submission(self, case):
        """The agent should not have to ask separately whether that helped."""
        assert submit(case)["grade"] == "candidate"
        assert submit(case, skill="vmware-log-insight")["grade"] == "probable"

    def test_an_unknown_time_source_is_accepted_and_kept_explicit(self, case):
        r = submit(case, time_source=None)
        assert r["evidence_id"] == "E001"

    def test_evidence_with_no_source_is_refused(self, case):
        with pytest.raises(ValueError, match="source_skill"):
            t.submit_case_evidence(
                case_id=case,
                source_skill="",
                source_tool="x",
                query={},
                summary="s",
                fetched_at=AT,
            )


class TestRecordGap:
    def test_returns_the_assigned_id_and_the_resulting_grade(self, case):
        submit(case)
        submit(case, skill="vmware-log-insight")
        r = t.record_case_gap(
            case_id=case,
            what="SMART counters",
            why="no BMC path",
            blocks=["H1"],
            how_to_close="pull an iDRAC bundle",
        )
        assert r["gap_id"] == "G001"
        assert r["grade"] == "probable"

    def test_a_gap_that_could_overturn_the_hypothesis_lowers_the_grade(self, case):
        submit(case)
        submit(case, skill="vmware-log-insight")
        r = t.record_case_gap(
            case_id=case,
            what="firmware change log",
            why="no access",
            blocks=["H1"],
            how_to_close="ask the site",
            could_falsify=True,
        )
        assert r["grade"] == "candidate"


class TestGrade:
    def test_the_tool_cannot_be_told_the_answer(self):
        params = set(inspect.signature(t.grade_case).parameters)
        assert not params & {"grade", "level", "conclusion", "verdict", "rca"}

    def test_returns_the_grade_the_ceiling_and_the_reasoning(self, case):
        r = t.grade_case(case_id=case, at="2026-08-28T09:30:00Z")
        assert r["grade"] == "candidate"
        assert r["ceiling"] == "probable"
        assert r["reasons"] and r["ceiling_reasons"]

    def test_grading_is_recorded_so_the_history_survives(self, case):
        t.grade_case(case_id=case, at="2026-08-28T09:30:00Z")
        submit(case)
        submit(case, skill="vmware-log-insight")
        t.grade_case(case_id=case, at="2026-08-28T10:00:00Z")
        assert [h["grade"] for h in t.get_case(case_id=case)["grade_history"]] == [
            "candidate",
            "probable",
        ]

    def test_a_demotion_comes_back_labelled_as_one(self, case):
        submit(case)
        submit(case, skill="vmware-log-insight")
        t.grade_case(case_id=case, at="2026-08-28T10:00:00Z")
        t.record_case_gap(
            case_id=case,
            what="firmware log",
            why="no access",
            blocks=["H1"],
            how_to_close="ask",
            could_falsify=True,
        )
        r = t.grade_case(case_id=case, at="2026-08-28T10:30:00Z")
        assert r["direction"] == "down"
        assert r["previous"] == "probable"
