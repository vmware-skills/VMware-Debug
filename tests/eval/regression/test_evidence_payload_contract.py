"""What `payload` has to be for case_timeline to see it, said at the time.

The tester submitted the summary dict from a get_events call — ``{"total": 3818,
"by_type": {...}}`` — as the payload. It was accepted, recorded, and reported
back a grade, and nothing anywhere said the timeline would never see it.
case_timeline then answered zero events with the hint "submit the get_events
result with `payload`", which is what they had just done.

Two things were missing and both are pinned here. The contract was undocumented
— ``payload: Any`` with a docstring that said "the raw result" — and, worse, the
one moment where the mismatch is knowable is the submission itself, where
nothing looked.

The controls stop the degenerate fix, which is to complain about every payload:
a real event envelope has to come back clean, evidence that is not an event
stream at all has to be accepted without being scolded, and the timeline still
has to build.
"""

from __future__ import annotations

import asyncio

import pytest

from vmware_debug.mcp import tools as t
from vmware_debug.mcp_server.server import build_server

AT = "2026-08-30T09:00:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def case():
    return t.case_open(summary="four hosts down", determined_by="alarm 42", at=AT)["case_id"]


def submit(case, payload, skill="vmware-monitor"):
    return t.case_submit_evidence(
        case_id=case,
        source_skill=skill,
        source_tool="get_events",
        query={"hours": 960},
        summary="3818 events over 40 days",
        fetched_at=AT,
        payload=payload,
    )


EVENT = {"ts": "2026-08-03T05:49:00Z", "severity": "critical", "text": "Shut down of esxi05"}


class TestTheMismatchIsReportedWhereItHappens:
    def test_a_summary_dict_is_not_silently_accepted_as_events(self, case):
        r = submit(case, {"total": 3818, "by_type": {"HostShutdown": 4}})
        assert r["payload_events"] == 0
        assert r["payload_note"]

    def test_the_note_names_the_keys_that_were_actually_there(self, case):
        """"Submit the get_events result" is unusable advice for someone who
        did. Naming what arrived is what makes the mismatch visible."""
        note = submit(case, {"total": 3818, "by_type": {}})["payload_note"]
        assert "total" in note and "by_type" in note

    def test_the_note_names_the_keys_it_reads(self, case):
        note = submit(case, {"total": 3818})["payload_note"]
        assert "items" in note and "events" in note and "rows" in note


class TestTheAcceptedShapes:
    def test_a_bare_list_of_events_is_counted(self, case):
        assert submit(case, [EVENT, EVENT])["payload_events"] == 2

    def test_the_family_list_envelope_is_counted(self, case):
        assert submit(case, {"items": [EVENT], "total": 1})["payload_events"] == 1

    def test_a_bare_list_survives_the_mcp_boundary(self):
        """The documented shape has to be reachable through the tool schema.
        Typed ``Optional[dict]``, an array of events was refused by validation
        before any of this code ran."""
        server = build_server()
        schema = {tool.name: tool.inputSchema for tool in asyncio.run(server.list_tools())}
        payload = schema["case_submit_evidence"]["properties"]["payload"]
        allowed = {
            entry.get("type") for entry in payload.get("anyOf", [payload]) if isinstance(entry, dict)
        }
        assert "array" in allowed, f"payload schema does not accept a list: {payload}"


class TestControls:
    def test_a_real_envelope_is_not_complained_about(self, case):
        """The fix must not be 'warn on everything'. Banning one phrase is not
        enough — the note has to positively report what it read, or a
        complain-about-everything version passes by rewording."""
        note = submit(case, {"items": [EVENT]})["payload_note"]
        assert "no event rows" not in note
        assert "1 event row" in note and "items" in note

    def test_evidence_that_is_not_an_event_stream_is_still_accepted(self, case):
        """A knowledge citation or a config dump carries no events by nature and
        is not a mistake. It is recorded, and it still counts as a source."""
        r = submit(case, None, skill="knowledge-kb")
        assert r["evidence_id"]
        assert r["payload_events"] == 0

    def test_the_timeline_still_builds_from_a_real_payload(self, case):
        submit(case, {"items": [EVENT]})
        assert t.case_timeline(case_id=case)["event_count"] == 1


class TestTheTimelineSaysWhichItemsCarriedNothing:
    def test_it_names_the_items_and_what_they_held(self, case):
        submit(case, {"total": 3818, "by_type": {}})
        note = t.case_timeline(case_id=case)["note"]
        assert "E001" in note
        assert "total" in note

    def test_control_a_populated_timeline_does_not_carry_the_complaint(self, case):
        submit(case, {"items": [EVENT]})
        assert "E001" not in t.case_timeline(case_id=case)["note"]
