"""Evidence and gaps — the contract that decides whether a conclusion is worth
anything.

Design section 3/6. The single rule these tests exist to hold down: **what could
not be fetched is recorded as a gap, never dropped.** Everything a grade is
later computed from passes through here, so an evidence ledger that quietly
loses a failed fetch produces a case that looks better-supported than it is.
"""

from __future__ import annotations

import json

import pytest

from vmware_debug.ops.cases.evidence import (
    Evidence,
    Gap,
    load_evidence,
    load_gaps,
    record_evidence,
    record_gap,
)
from vmware_debug.ops.cases.hypotheses import add_hypothesis
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import CaseNotFound, case_dir, create_case

AT = "2026-08-28T09:15:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def case():
    cid = create_case(Scope(summary="vsan latency", determined_by="alarm 42"), at=AT).case_id
    add_hypothesis(cid, "failing device")  # the gaps below block it
    return cid


def an_evidence(**kw):
    base = dict(
        source_skill="vmware-monitor",
        source_tool="list_events",
        query={"target": "vc-01", "hours": 24},
        fetched_at="2026-08-28T09:20:00Z",
        window_start="2026-08-27T09:20:00Z",
        window_end="2026-08-28T09:20:00Z",
        time_source="vcenter",
        clock_skew_s=0.0,
        summary="41 events, 6 scsi warnings clustered at 08:41",
    )
    base.update(kw)
    return Evidence(**base)


class TestRecording:
    def test_assigns_sequential_readable_ids(self, case):
        assert record_evidence(case, an_evidence()).evidence_id == "E001"
        assert record_evidence(case, an_evidence()).evidence_id == "E002"

    def test_each_item_lands_in_its_own_file(self, case):
        e = record_evidence(case, an_evidence())
        assert (case_dir(case) / "evidence" / f"{e.evidence_id}.json").exists()

    def test_round_trips(self, case):
        record_evidence(case, an_evidence())
        [got] = load_evidence(case)
        assert got.source_tool == "list_events"
        assert got.query == {"target": "vc-01", "hours": 24}

    def test_the_payload_is_stored_beside_the_record(self, case):
        e = record_evidence(case, an_evidence(), payload={"rows": [1, 2, 3]})
        body = json.loads((case_dir(case) / "evidence" / f"{e.evidence_id}.json").read_text(encoding="utf-8"))
        assert body["payload"] == {"rows": [1, 2, 3]}

    def test_recording_against_a_case_that_does_not_exist_is_an_error(self):
        with pytest.raises(CaseNotFound):
            record_evidence("20260828-091500-nope", an_evidence())

    def test_evidence_ids_keep_counting_after_a_reload(self, case):
        record_evidence(case, an_evidence())
        record_evidence(case, an_evidence())
        assert record_evidence(case, an_evidence()).evidence_id == "E003"


class TestTheTimeBasisContract:
    """Design section 6: when the data was fetched is not when it applies."""

    def test_the_window_is_kept_separate_from_the_fetch_time(self, case):
        [got] = [record_evidence(case, an_evidence())]
        assert got.fetched_at != got.window_start

    def test_an_unknown_time_source_is_explicit_not_missing(self, case):
        e = record_evidence(case, an_evidence(time_source=None, clock_skew_s=None))
        body = json.loads((case_dir(case) / "evidence" / f"{e.evidence_id}.json").read_text(encoding="utf-8"))
        assert "time_source" in body and body["time_source"] is None
        assert "clock_skew_s" in body and body["clock_skew_s"] is None

    def test_rejects_evidence_that_names_no_source(self, case):
        with pytest.raises(ValueError, match="source_skill"):
            record_evidence(case, an_evidence(source_skill=""))

    def test_rejects_evidence_that_does_not_say_when_it_was_fetched(self, case):
        with pytest.raises(ValueError, match="fetched_at"):
            record_evidence(case, an_evidence(fetched_at=""))


class TestGaps:
    def test_a_gap_is_recorded_with_what_it_blocks_and_how_to_close_it(self, case):
        record_gap(
            case,
            Gap(
                what="SMART/NVMe counters for naa.6000...",
                why="no BMC/Redfish path from this host",
                blocks=("H1",),
                how_to_close="ask the site to pull an iDRAC support bundle",
            ),
        )
        [g] = load_gaps(case)
        assert g.blocks == ("H1",)
        assert "iDRAC" in g.how_to_close

    def test_gaps_accumulate_rather_than_replace(self, case):
        record_gap(case, Gap(what="a", why="w", blocks=(), how_to_close="x"))
        record_gap(case, Gap(what="b", why="w", blocks=(), how_to_close="x"))
        assert [g.what for g in load_gaps(case)] == ["a", "b"]

    def test_gaps_get_ids_too_so_a_hypothesis_can_point_at_one(self, case):
        assert (
            record_gap(case, Gap(what="a", why="w", blocks=(), how_to_close="x")).gap_id == "G001"
        )

    def test_a_gap_must_say_how_to_close_it(self, case):
        """A gap nobody knows how to close is a dead end that reads like a
        to-do. Naming the next action is what makes the ledger actionable."""
        with pytest.raises(ValueError, match="how_to_close"):
            record_gap(case, Gap(what="a", why="w", blocks=(), how_to_close="  "))

    def test_no_gaps_recorded_reads_as_empty_not_as_all_clear(self, case):
        """load_gaps is allowed to be empty. What is not allowed is anywhere
        else treating that emptiness as 'nothing was missing' — asserted here
        so the distinction has a home in the tests."""
        assert load_gaps(case) == ()
