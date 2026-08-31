"""The last two steps: build the timeline from the ledger, and close the case.

`case_timeline` (steps 04/05) reuses the correlation engine this skill already
had — the difference is that it reads the evidence a case has collected instead
of taking a payload the caller assembled. `case_close` (step 08) archives.

The properties that matter are about honesty at the boundaries:

* **A timeline built from nothing says so.** An empty timeline and a quiet
  incident render identically unless the answer distinguishes them.
* **A case cannot be closed while it is still blocked** without saying that is
  what happened. Closing is the act that turns a folder into a record other
  people rely on.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.evidence import Evidence, Gap, record_evidence, record_gap
from vmware_debug.ops.cases.hypotheses import add_hypothesis
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.timeline import build_case_timeline, close_case
from vmware_debug.ops.cases.store import case_dir, create_case, load_case

AT = "2026-08-30T09:00:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def case():
    cid = create_case(Scope(summary="vsan latency", determined_by="alarm 42"), at=AT).case_id
    add_hypothesis(cid, "failing device")
    return cid


def submit(case, skill="vmware-monitor", payload=None):
    return record_evidence(
        case,
        Evidence(
            source_skill=skill,
            source_tool="get_events",
            query={},
            fetched_at=AT,
            summary="s",
        ),
        payload=payload,
    )


EVENTS = {
    "items": [
        {
            "ts": "2026-08-30T08:41:00Z",
            "severity": "warning",
            "entity": "esxi-03",
            "text": "scsi device latency high",
        },
        {
            "ts": "2026-08-30T08:41:07Z",
            "severity": "error",
            "entity": "esxi-03",
            "text": "NMP abort on naa.6000",
        },
    ]
}


class TestTimeline:
    def test_events_are_pulled_out_of_submitted_payloads(self, case):
        submit(case, payload=EVENTS)
        out = build_case_timeline(case)
        assert out["event_count"] == 2

    def test_a_case_with_no_events_says_so_rather_than_rendering_empty(self, case):
        submit(case, payload={"items": []})
        out = build_case_timeline(case)
        assert out["event_count"] == 0
        assert "no events" in out["note"].lower()

    def test_a_case_with_no_evidence_at_all_is_distinguished_from_a_quiet_one(self, case):
        out = build_case_timeline(case)
        assert out["event_count"] == 0
        assert "no evidence" in out["note"].lower()

    def test_payloads_that_carry_no_events_are_counted_not_ignored(self, case):
        """Most evidence is not an event stream. Saying how many items were
        skipped is what keeps '0 events' from reading as 'nothing happened'."""
        submit(case, payload={"clusters": [{"name": "c1"}]})
        out = build_case_timeline(case)
        assert out["evidence_without_events"] == 1

    def test_it_writes_timeline_md(self, case):
        submit(case, payload=EVENTS)
        build_case_timeline(case)
        body = (case_dir(case) / "timeline.md").read_text(encoding="utf-8")
        assert "esxi-03" in body
        assert "_Empty." not in body

    def test_a_malformed_event_names_the_offender_rather_than_dropping_it(self, case):
        submit(case, payload={"items": [{"ts": "not-a-time"}]})
        out = build_case_timeline(case)
        assert out["rejected"], "a malformed event vanished silently"
        assert "E001" in str(out["rejected"])


class TestClose:
    def test_closing_records_the_state_and_the_grade(self, case):
        submit(case)
        submit(case, skill="vmware-log-insight")
        out = close_case(case, at=AT)
        assert out["state"] == "closed"
        assert out["grade"] == "probable"
        assert load_case(case).state == "closed"

    def test_closing_a_blocked_case_says_what_was_left_open(self, case):
        submit(case)
        submit(case, skill="vmware-log-insight")
        record_gap(
            case,
            Gap(what="SMART", why="no BMC", blocks=("H1",), how_to_close="pull an iDRAC bundle"),
        )
        out = close_case(case, at=AT)
        assert out["state"] == "closed"
        assert out["open_gaps"] == ["G001"], "closing hid an unresolved gap"
        assert "G001" in out["note"]

    def test_the_grade_is_recorded_at_close_not_assumed(self, case):
        out = close_case(case, at=AT)
        assert out["grade"] == "candidate"
        assert "candidate" in (case_dir(case) / "conclusion.md").read_text(encoding="utf-8").lower()

    def test_closing_twice_is_refused_rather_than_rewriting_the_record(self, case):
        close_case(case, at=AT)
        with pytest.raises(ValueError, match="already closed"):
            close_case(case, at="2026-08-30T10:00:00Z")

    def test_a_closed_case_still_lists(self, case):
        from vmware_debug.ops.cases.store import list_cases

        close_case(case, at=AT)
        assert [c.state for c in list_cases()] == ["closed"]
