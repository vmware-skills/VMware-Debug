"""The 2026-08-03 incident: four hosts died and the classifier could not read it.

A tester ran 3818 real vCenter events through the correlator. 3050 of them —
80% — landed in ``uncategorized``, and the top-line advice was to widen a window
that was already 40 days wide. The events it could not read were the incident:
four ESXi hosts went EnteringMaintenanceMode → EnteredMaintenanceMode →
HostShutdown → HostConnectionLost → HostSyncFailed inside 32 seconds.

The taxonomy had seven categories and no notion of a host changing its own
availability state. ``power_lifecycle``'s vocabulary is VM-centric — power
on/off, vmx, ovf, clone, snapshot — and "Shut down of esxi05" does not contain
"power off".

Three properties are pinned here:

* host-lifecycle vocabulary classifies, and the categories that already worked
  still work (a fix that answers "platform" to everything passes the first half
  and fails the second);
* the same incident described five ways routes the same way, so an incidental
  noun in a summary no longer decides;
* what could NOT be classified is counted and reported, and the remedy offered
  for it is reachable — telling someone to widen a 40-day window is a loop.
"""

from __future__ import annotations

import pytest

from vmware_debug.envelope import Event
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.plan import plan_next
from vmware_debug.ops.cases.store import create_case
from vmware_debug.ops.timeline import (
    classify_symptom,
    classify_symptom_matches,
    incident_timeline,
    known_categories,
    rank_hypotheses,
)

#: The messages vCenter actually emitted, in the forms the tester probed and the
#: forms the named event types carry. Every one of these came back
#: UNCATEGORIZED.
HOST_LIFECYCLE_MESSAGES = (
    "Host esxi05.knight.com in cluster c1 has started to enter maintenance mode",
    "esxi05.knight.com has entered maintenance mode",
    "Shut down of esxi05.knight.com: operator initiated",
    "Lost connection to esxi05.knight.com",
    "Cannot synchronize host esxi05.knight.com",
)


def _ev(text: str, ts: float = 1000.0, severity: str = "error") -> Event:
    return Event(ts=ts, source="monitor", severity=severity, entity="esxi05", text=text)


class TestHostLifecycleIsReadable:
    def test_every_message_from_the_incident_classifies(self):
        for message in HOST_LIFECYCLE_MESSAGES:
            assert classify_symptom(message), f"{message!r} matched no category"

    def test_they_classify_as_host_lifecycle_specifically(self):
        """Not merely "something matched". A host that took itself out of
        service is a different investigation from a VM that would not boot, and
        it routes to different tools."""
        for message in HOST_LIFECYCLE_MESSAGES:
            assert classify_symptom(message)[0] == "host_lifecycle", message

    def test_the_category_is_in_the_published_catalogue(self):
        assert "host_lifecycle" in known_categories()


class TestControlTheCategoriesThatAlreadyWorked:
    """The degenerate fix — answer host_lifecycle, or platform, to everything —
    passes every test above. These are what it fails."""

    def test_a_vm_power_failure_is_still_power_lifecycle_not_host_lifecycle(self):
        cats = classify_symptom("Power On virtual machine web01 failed: vmx file missing")
        assert cats[0] == "power_lifecycle"
        assert "host_lifecycle" not in cats

    def test_a_storage_symptom_is_still_storage(self):
        assert classify_symptom("datastore ds1 APD, scsi latency 4000ms")[0] == "storage"

    def test_a_network_symptom_is_still_network(self):
        assert classify_symptom("DFW rule dropping traffic to segment web")[0] == "network"

    def test_vocabulary_nobody_taught_it_still_matches_nothing(self):
        """An empty result has to stay possible. A classifier that always
        answers is a classifier that has stopped being asked."""
        assert classify_symptom("the flux capacitor is misaligned") == []


class TestOneIncidentRoutesOneWay:
    """Five descriptions of the 08-03 incident, each carrying one incidental
    noun from a different category. They routed to storage / network / auth /
    compute / None; the most accurate of them got None. What decided the answer
    was which noun happened to be in the sentence."""

    DESCRIPTIONS = (
        "Four ESXi hosts entered maintenance mode and shut down at 05:49 and never came back",
        (
            "esxi01-04 lost connection to vCenter after an unplanned outage; "
            "the datastore on them went inaccessible"
        ),
        (
            "Cannot synchronize host esxi05 — repeated host sync failures, "
            "and a snapshot task on it also failed"
        ),
        (
            "All four hosts went into maintenance mode within 32 seconds, "
            "then the uplink to their management network dropped"
        ),
        (
            "The hosts took themselves out of service after entering maintenance mode; "
            "a vpxd service restart did not bring them back"
        ),
    )

    def test_all_five_descriptions_reach_the_same_category(self):
        assert {classify_symptom(d)[0] for d in self.DESCRIPTIONS} == {"host_lifecycle"}

    def test_an_incidental_noun_no_longer_outranks_the_subject(self):
        """'datastore' appears once, as a consequence. 'lost connection to'
        names what happened."""
        matches = classify_symptom_matches(self.DESCRIPTIONS[1])
        assert matches[0]["category"] == "host_lifecycle"
        assert "storage" in {m["category"] for m in matches}

    def test_the_words_that_decided_it_are_reported(self):
        """A category handed over with no evidence for it cannot be argued with,
        and this is the step where a wrong turn costs the whole investigation."""
        top = classify_symptom_matches(self.DESCRIPTIONS[0])[0]
        assert top["matched_keywords"]
        assert all(kw in self.DESCRIPTIONS[0].lower() for kw in top["matched_keywords"])

    def test_control_a_summary_that_is_only_storage_still_routes_to_storage(self):
        """The ranking must not have been tuned to make host_lifecycle win."""
        assert classify_symptom_matches("datastore ds1 latency spike")[0]["category"] == "storage"


class TestWhatCouldNotBeClassifiedIsReported:
    def test_the_uncategorized_share_is_stated_not_absorbed(self):
        events = [_ev(f"routine chatter {i}", ts=1000 + i) for i in range(8)]
        events += [_ev("datastore ds1 APD", ts=1100), _ev("scsi latency high", ts=1101)]
        out = incident_timeline(events)
        c = out["classification"]
        assert c["uncategorized"] == 8
        assert c["categorized"] == 2
        assert c["uncategorized_share"] == 0.8

    def test_the_unreadable_texts_are_sampled_so_they_can_be_looked_at(self):
        out = incident_timeline([_ev("mysterious subsystem xyz failed", ts=1000 + i)
                                 for i in range(4)])
        assert any(
            "mysterious subsystem xyz" in s for s in out["classification"]["unmatched_samples"]
        )

    def test_control_a_fully_classified_stream_reports_a_zero_share(self):
        """The counter has to be able to say 'none', or it is decoration."""
        out = incident_timeline([_ev("datastore ds1 APD", ts=1000 + i) for i in range(4)])
        assert out["classification"]["uncategorized"] == 0
        assert out["classification"]["uncategorized_share"] == 0.0

    def test_a_high_unread_share_reaches_the_top_line(self):
        """Buried in a sub-key it is the same defect one level down: the
        conclusion still reads as if the whole stream had been understood."""
        events = [_ev(f"routine chatter {i}", ts=1000 + i) for i in range(8)]
        events += [_ev("datastore ds1 APD", ts=1100), _ev("scsi latency high", ts=1101)]
        out = incident_timeline(events)
        assert any("80" in c or "uncategorized" in c for c in out["next_checks"])


class TestTheRemedyIsReachable:
    """'Widen the search window' was the advice given for a 40-day window. An
    instruction the user has already followed to its limit is a loop."""

    def test_the_unclassified_hypothesis_does_not_send_the_user_back_round(self):
        h = rank_hypotheses([_ev("something totally novel")])[0]
        assert h.category == "uncategorized"
        assert "widen" not in h.suggested_check.lower()

    def test_it_names_something_that_can_actually_be_done_next(self):
        h = rank_hypotheses([_ev("something totally novel")])[0]
        assert "sample_text" in h.suggested_check or "bin_seconds" in h.suggested_check

    def test_the_top_level_advice_does_not_send_the_user_back_round(self):
        out = incident_timeline([_ev("something totally novel", ts=1000 + i) for i in range(4)])
        assert not any("widen" in c.lower() for c in out["next_checks"])

    def test_control_the_no_events_case_still_says_where_to_start(self):
        """With nothing in hand, "go and fetch some" is the correct advice and
        must survive: the loop being closed is 'widen what you already have'."""
        out = incident_timeline([])
        assert out["next_checks"] and "vmware-monitor" in out["next_checks"][0]


class TestThePlanRoutesTheIncidentAndShowsItsWorking:
    """Everything downstream of the category runs off the category. A plan that
    states one without stating what produced it cannot be corrected by the
    person best placed to notice it is wrong."""

    @pytest.fixture(autouse=True)
    def isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))

    def _case(self, summary: str) -> str:
        return create_case(
            Scope(summary=summary, determined_by="user report"), at="2026-08-30T09:00:00Z"
        ).case_id

    def test_the_incident_scope_now_reaches_a_plan(self):
        summary = "Four ESXi hosts entered maintenance mode and shut down, and never came back"
        assert plan_next(self._case(summary))["category"] == "host_lifecycle"

    def test_the_plan_reaches_the_drift_skill(self):
        """A host that left service on cue was usually told to, so 'what changed
        against the baseline' is a first move here rather than a follow-up."""
        summary = "Four ESXi hosts entered maintenance mode and shut down, and never came back"
        skills = {s["skill"] for s in plan_next(self._case(summary))["steps"]}
        assert "vmware-harden" in skills

    def test_the_words_that_chose_the_category_travel_with_the_plan(self):
        summary = "Four ESXi hosts entered maintenance mode and shut down at 05:49"
        r = plan_next(self._case(summary))
        assert r["category_signals"][0]["category"] == "host_lifecycle"
        assert r["category_signals"][0]["matched_keywords"]
        assert "maintenance mode" in r["note"]

    def test_a_category_that_also_matched_is_named(self):
        summary = "esxi01-04 lost connection to vCenter; the datastore went inaccessible"
        r = plan_next(self._case(summary))
        assert r["category"] == "host_lifecycle"
        assert "storage" in {s["category"] for s in r["category_signals"]}
        assert "storage" in r["note"]

    def test_control_a_single_match_does_not_manufacture_a_competitor(self):
        r = plan_next(self._case("vSAN datastore latency on cluster-01"))
        assert [s["category"] for s in r["category_signals"]] == ["storage"]

    def test_control_an_unmatched_scope_still_reports_no_signals_and_says_so(self):
        r = plan_next(self._case("something is weird"))
        assert r["category"] is None
        assert r["category_signals"] == []
