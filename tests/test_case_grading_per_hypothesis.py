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
from vmware_debug.ops.cases.evidence import Evidence, Gap, record_evidence, record_gap
from vmware_debug.ops.cases.grading import grade_case
from vmware_debug.ops.cases.hypotheses import add_hypothesis
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import create_case

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
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
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
    record_evidence(two_hypotheses, _evidence("vmware-monitor"))
    out = grade(two_hypotheses, at=AT)
    assert out["hypotheses"] == [
        {"id": "H1", "status": "open"},
        {"id": "H2", "status": "excluded"},
    ]
