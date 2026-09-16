"""Excluded is a verdict about a hypothesis, not about the whole case.

2026-09-15, a real investigation of a vCenter app-health alert: two sources
ruled out H2 (host memory reclaim) while the leading H1 stayed open behind three
gaps. ``case_grade`` graded the whole case Excluded, which reads as "the answer
was ruled out". The investigator had to explain in prose that only H2 was.

The first version of the fix over-corrected, and an independent review caught
it: the observations that ruled H2 out were then counted as corroboration for
H1, and a hypothesis added with no evidence at all inherited Probable. Evidence
records only what it falsifies, never what it supports, so an item that rules a
hypothesis out must not count for the ones left open.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.api import grade
from vmware_debug.ops.cases.api import hypotheses as api_hypotheses
from vmware_debug.ops.cases.evidence import Evidence, Gap, record_evidence, record_gap
from vmware_debug.ops.cases.grading import grade_case, site_rules_path
from vmware_debug.ops.cases.hypotheses import add_hypothesis, hypothesis_ledger, load_hypotheses
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import case_dir, create_case

AT = "2026-09-15T13:30:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def two_hypotheses():
    cid = create_case(
        Scope(
            summary="vcenter app health",
            determined_by="aria alert",
            product_versions={"vsphere": "8.0.3"},
        ),
        at=AT,
    ).case_id
    add_hypothesis(cid, "appliance memory pressure")  # H1
    add_hypothesis(cid, "host reclaims appliance memory")  # H2
    return cid


def _evidence(skill: str, falsifies: tuple[str, ...] = ()) -> Evidence:
    return Evidence(
        source_skill=skill,
        source_tool="t",
        query={},
        fetched_at="2026-09-15T13:31:00Z",
        summary="s",
        falsifies=falsifies,
    )


def _gap(blocks: tuple[str, ...], could_falsify: bool = True) -> Gap:
    return Gap(
        what="swap history",
        why="not collected",
        blocks=blocks,
        could_falsify=could_falsify,
        how_to_close="vami",
    )


def test_ruling_out_one_hypothesis_does_not_exclude_the_case(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H2",)))
    r = grade_case(two_hypotheses)
    assert r.grade == "candidate"
    assert r.hypotheses == (("H1", "open"), ("H2", "excluded"))
    assert any("Still open: H1" in reason for reason in r.reasons)


def test_the_case_is_excluded_when_every_hypothesis_is_ruled_out(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H1", "H2")))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H1", "H2")))
    assert grade_case(two_hypotheses).grade == "excluded"


def test_evidence_that_rules_one_out_is_not_corroboration_for_the_rest(two_hypotheses):
    """aria rules H2 out; monitor says something unrelated. Nothing is about H1."""
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    assert grade_case(two_hypotheses).grade == "candidate"


def test_a_new_hypothesis_does_not_inherit_probable(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H1", "H2")))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H1", "H2")))
    assert grade_case(two_hypotheses).grade == "excluded"
    add_hypothesis(two_hypotheses, "something else")  # H3, no evidence at all
    r = grade_case(two_hypotheses)
    assert r.grade == "candidate"
    assert ("H3", "open") in r.hypotheses


def test_a_gap_that_blocks_only_a_ruled_out_hypothesis_holds_nothing_back(two_hypotheses):
    """Otherwise a question nobody needs answered any more pins H1 at Candidate."""
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    record_evidence(two_hypotheses, _evidence("vmware-log-insight"))
    record_gap(two_hypotheses, _gap(blocks=("H2",)))
    r = grade_case(two_hypotheses)
    assert r.grade == "probable"
    assert not any(reason.startswith("Blocking gap(s)") for reason in r.reasons)
    assert any("No longer blocking" in reason for reason in r.reasons)


def test_a_gap_that_still_blocks_an_open_hypothesis_still_holds(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    record_evidence(two_hypotheses, _evidence("vmware-log-insight"))
    record_gap(two_hypotheses, _gap(blocks=("H1", "H2")))
    assert grade_case(two_hypotheses).grade == "candidate"


def test_the_tool_result_lists_each_hypothesis(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H2",)))
    out = grade(two_hypotheses, at=AT)
    assert out["hypotheses"] == [
        {"id": "H1", "status": "open"},
        {"id": "H2", "status": "excluded"},
    ]


# Second independent review, 2026-09-15: the two sources an exclusion needs were
# counted across the whole case, so one observation against H2 plus anything
# unrelated ruled H2 out — and retired the gap that was holding the case back.


def test_one_falsifier_plus_an_unrelated_source_does_not_exclude(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    r = grade_case(two_hypotheses)
    assert r.hypotheses == (("H1", "open"), ("H2", "open"))
    assert r.grade == "candidate"


def test_one_falsifier_cannot_retire_a_gap_and_promote_the_case(two_hypotheses):
    record_gap(two_hypotheses, _gap(blocks=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    record_evidence(two_hypotheses, _evidence("vmware-log-insight"))
    assert grade_case(two_hypotheses).grade == "candidate"
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    r = grade_case(two_hypotheses)
    assert r.grade == "candidate"
    assert any(reason.startswith("Blocking gap(s)") for reason in r.reasons)


def test_falsifiers_of_different_hypotheses_are_not_pooled(two_hypotheses):
    """aria against H1 and monitor against H2 are one source each, not two for both."""
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H1",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H2",)))
    r = grade_case(two_hypotheses)
    assert r.grade == "candidate"
    assert r.hypotheses == (("H1", "open"), ("H2", "open"))


def test_a_missing_ledger_never_reads_as_everything_ruled_out(two_hypotheses):
    """No registered hypotheses means nothing is known to be left open — not that
    nothing is. "Every hypothesis ruled out" over an empty list is a verdict
    about nothing."""
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H1", "H2")))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H1", "H2")))
    assert grade_case(two_hypotheses).grade == "excluded"
    (case_dir(two_hypotheses) / "hypotheses.md").unlink()
    r = grade_case(two_hypotheses)
    assert r.grade == "candidate"
    assert r.hypotheses == ()
    assert any("unreadable" in reason for reason in r.reasons)


def test_a_corrupt_ledger_is_graded_with_a_reason_instead_of_failing(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    ledger = case_dir(two_hypotheses) / "hypotheses.md"
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + "\n<!-- hypothesis {broken -->\n", encoding="utf-8"
    )
    r = grade_case(two_hypotheses)
    assert r.grade == "candidate"
    assert any("corrupt" in reason for reason in r.reasons)


def _site_rules(text: str) -> None:
    rules = site_rules_path()
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text(text, encoding="utf-8")


def test_falsifiers_never_count_as_support_even_when_nothing_is_ruled_out(two_hypotheses):
    """A site that asks for three sources to exclude: two falsifiers of H2 rule
    nothing out, and still say nothing for H1. The ledger follows the site rule."""
    _site_rules(
        "grades:\n  excluded:\n    requires_falsifying_evidence: true\n"
        "    min_independent_sources: 3\n"
    )
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H2",)))
    r = grade_case(two_hypotheses)
    assert r.hypotheses == (("H1", "open"), ("H2", "open"))
    assert r.grade == "candidate"
    assert hypothesis_ledger(two_hypotheses)[1]["status"] != "refuted"


def test_case_hypotheses_agrees_with_case_grade(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H2",)))
    assert hypothesis_ledger(two_hypotheses)[1]["status"] != "refuted"
    assert dict(grade_case(two_hypotheses).hypotheses)["H2"] == "open"
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H2",)))
    assert hypothesis_ledger(two_hypotheses)[1]["status"] == "refuted"
    assert dict(grade_case(two_hypotheses).hypotheses)["H2"] == "excluded"


# Third independent review, 2026-09-15: once one falsifier no longer excluded a
# hypothesis, it counted for nothing at all — the case kept Probable, and even
# Confirmed, with an observation against a hypothesis still open.


def test_a_contradicted_hypothesis_holds_the_case_at_candidate(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("vmware-aria"))
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    assert grade_case(two_hypotheses).grade == "probable"
    record_evidence(two_hypotheses, _evidence("vmware-log-insight", falsifies=("H2",)))
    r = grade_case(two_hypotheses)
    assert r.grade == "candidate"
    assert any(reason.startswith("Held at Candidate") and "H2" in reason for reason in r.reasons)


def test_a_contradicted_hypothesis_cannot_be_confirmed(two_hypotheses, tmp_path):
    kb = tmp_path / "vmware" / "knowledge" / "kb" / "KB-fixture.md"
    kb.parent.mkdir(parents=True, exist_ok=True)
    kb.write_text("---\nid: KB-fixture\napplies_to:\n  product: vsphere\n---\nbody\n", encoding="utf-8")
    record_evidence(two_hypotheses, _evidence("vmware-aria"))
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    kb_item = Evidence(
        source_skill="knowledge-kb",
        source_tool="kb",
        query={},
        fetched_at="2026-09-15T13:31:00Z",
        summary="s",
        knowledge_entry_id="KB-fixture",
    )
    record_evidence(two_hypotheses, kb_item)
    assert grade_case(two_hypotheses).grade == "confirmed"
    record_evidence(two_hypotheses, _evidence("vmware-log-insight", falsifies=("H1",)))
    assert grade_case(two_hypotheses).grade == "candidate"


@pytest.mark.parametrize("entry", ["[]", '"x"', "1", "null", "{}", '{"id": ""}'])
def test_a_ledger_entry_that_is_not_a_hypothesis_is_corrupt(two_hypotheses, entry):
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    ledger = case_dir(two_hypotheses) / "hypotheses.md"
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + f"\n<!-- hypothesis {entry} -->\n", encoding="utf-8"
    )
    r = grade_case(two_hypotheses)
    assert r.grade == "candidate"
    assert any("corrupt" in reason for reason in r.reasons)


def test_a_broken_rules_file_refuses_before_adding_a_hypothesis(two_hypotheses):
    """Failing after the write left the hypothesis on disk, so a retry added it twice."""
    _site_rules("grades: [unclosed\n")
    with pytest.raises(ValueError):
        api_hypotheses(two_hypotheses, statement="a third idea", at=AT)
    assert len(load_hypotheses(two_hypotheses)) == 2


def test_two_spellings_of_one_skill_do_not_rule_a_hypothesis_out(two_hypotheses):
    record_evidence(two_hypotheses, _evidence("monitor", falsifies=("H2",)))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H2",)))
    assert dict(grade_case(two_hypotheses).hypotheses)["H2"] == "open"
    assert hypothesis_ledger(two_hypotheses)[1]["status"] != "refuted"


def test_a_site_that_turns_exclusion_off_never_excludes(two_hypotheses):
    _site_rules("grades:\n  excluded:\n    requires_falsifying_evidence: false\n")
    record_evidence(two_hypotheses, _evidence("vmware-aria", falsifies=("H1", "H2")))
    record_evidence(two_hypotheses, _evidence("vmware-monitor", falsifies=("H1", "H2")))
    r = grade_case(two_hypotheses)
    assert r.grade != "excluded"
    assert r.hypotheses == (("H1", "open"), ("H2", "open"))
    assert all(h["status"] != "refuted" for h in hypothesis_ledger(two_hypotheses))


def test_no_falsifier_never_excludes_even_at_a_zero_threshold(two_hypotheses):
    """"We looked and found nothing" must not read as ruled out, whatever the threshold."""
    _site_rules(
        "grades:\n  excluded:\n    requires_falsifying_evidence: true\n"
        "    min_independent_sources: 0\n"
    )
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    r = grade_case(two_hypotheses)
    assert r.grade != "excluded"
    assert r.hypotheses == (("H1", "open"), ("H2", "open"))
