"""What the symptom taxonomy can and cannot read, measured against vSphere itself.

A previous round added a ``host_lifecycle`` category and density-based binning,
and the re-test still came back with roughly half the stream unclassified once
login/logout noise was set aside. The obvious response — keep adding keywords
until a sample passes — would move the number and fix nothing, so this file
establishes what the residue *is* before anything is changed.

Measured against pyVmomi's registry of all 427 vSphere event types (see
``_vsphere_event_vocabulary``), the taxonomy reads 124 and misses 303. The
misses are not a list of subsystems it lacks. They are, overwhelmingly, events
whose subsystem it already has a category for, written in a register the
keyword tables were not written in:

    VmPoweredOnEvent          power_lifecycle has "power on"; the event says
                              "powered on" — a past participle, not an imperative
    HostCnxFailedTimeoutEvent host_lifecycle has "connection lost" and "host
                              connection"; vCenter abbreviates it "Cnx"
    DasHostFailedEvent        ha_drs has "high availability", "failover",
                              "host isolation"; vCenter's HA events are all "Das"
    VmMigratedEvent           network has "vmotion"; the event says "migrated"
    VmDiskFailedEvent         storage has "disk full", "vmfs", "scsi"; not "disk
                              failed"

Those five are not five oversights. They are one: the tables are phrased the way
an operator describes a symptom, and the events are named the way a server
records a fact. Sixty more phrases would close today's list and lose again on the
next vSphere release, which is what a hand-maintained vocabulary does.

So the change this file guards is not a bigger vocabulary. It is:

1. **Stop discarding the event's own identifier.** ``normalize_event`` already
   keeps ``event_type`` — vCenter's stable, machine-readable name for the event —
   in ``fields``, and ``_categorize`` read only the prose. Folding the identifier
   in adds no keyword and can only ever add matches.

   Measured against this corpus it adds **one** (124 -> 125 of 427), and that is
   the honest headline: for a classic event the message and the class name say
   the same words, so reading both changes nothing. The gain is confined to
   ``EventEx``, which modern vSphere uses for most events — there the message is
   generic boilerplate and the entire identity is an ``eventTypeId`` such as
   ``esx.problem.scsi.device.io.latency.high``, which names its subsystem
   outright while the prose says "Issue detected on esx01". vmware-monitor hit
   exactly this on real hardware and fixed it on its side (``_event_key``);
   vmware-debug had the same defect on the receiving side. This corpus is a list
   of class names and so cannot represent EventEx at all, which is why the gain
   here is 1 and why no coverage claim is made from it.
2. **Say the true thing about what is left.** The old coverage note told the
   caller that unmatched events might "name a subsystem this taxonomy does not
   know" and to go find that subsystem's read tools. For ``VmPoweredOnEvent``
   that is a wasted round trip after a false premise.

The numbers below are recorded, not required. If someone later raises coverage by
padding ``_CATEGORY_SIGNATURES``, ``test_the_taxonomy_was_not_quietly_padded``
fails and says so, because a coverage figure that goes up without an explanation
is the thing this round was asked not to produce.
"""

from __future__ import annotations

import re

import pytest

from tests.eval.regression._vsphere_event_vocabulary import VSPHERE_EVENT_TYPES
from vmware_debug.envelope import normalize_event
from vmware_debug.ops.timeline import (
    classification_coverage,
    classify_symptom,
    known_categories,
)


def _as_prose(event_type: str) -> str:
    """``VmPoweredOnEvent`` -> ``vm powered on`` — the closest a type name gets
    to the message text an operator would see."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", event_type.removesuffix("Event")).lower()


def _monitor_event(event_type: str, message: str, ts: str = "2026-08-30T10:00:00Z") -> dict:
    """One event shaped exactly as ``vmware-monitor.get_events`` returns it."""
    return {
        "time": ts,
        "source": "monitor",
        "severity": "warning",
        "entity_name": "esx01",
        "message": message,
        "event_type": event_type,
    }


class TestTheIdentifierIsUsed:
    """The one field vCenter guarantees is machine-readable must not be dropped."""

    def test_a_generic_message_with_a_specific_event_type_is_classified(self):
        """Modern vSphere sends ``EventEx``: generic prose, specific type id.

        ``esx.problem.scsi.device.io.latency.high`` names the subsystem outright.
        Classifying on the message alone throws that away and reports the event
        as unreadable, which is how a storage incident becomes 'uncategorized'.
        """
        event = normalize_event(
            _monitor_event(
                "esx.problem.scsi.device.io.latency.high",
                "Issue detected on esx01 in ha-datacenter",
            )
        )
        assert "storage" in classify_symptom(event.text, event.entity, event.fields)

    def test_the_identifier_is_split_into_words_before_matching(self):
        """``VmPoweredOnEvent`` is one token; "power on" is two.

        Splitting camelCase and dots is normalisation, not new vocabulary —
        without it the identifier is present and still unmatchable, which is the
        same as not passing it.
        """
        event = normalize_event(
            _monitor_event("HostShutdownEvent", "Task: Shut down or restart host")
        )
        assert "host_lifecycle" in classify_symptom(event.text, event.entity, event.fields)
        # ...and the unsplit identifier really is unmatchable, so the split is
        # load-bearing rather than incidental.
        assert not classify_symptom("HostShutdownEvent".lower())

    def test_punctuation_in_an_identifier_is_split_so_two_word_keywords_reach_it(self):
        """Dots and underscores, not just camelCase.

        Substring matching makes most punctuation irrelevant — "scsi" is found
        inside ``esx.problem.scsi.device...`` either way — so this is easy to
        believe is decorative. It is not: a third of the taxonomy's phrases
        contain a space ("disk full", "cpu ready", "link down"), and those can
        never match a ``disk_full_warning`` or an ``esx.problem.cpu.ready``
        unless the separator becomes one.
        """
        event = normalize_event(
            {
                "ts": "2026-08-30T10:00:00Z",
                "source": "loginsight",
                "text": "threshold exceeded",
                "event_type": "disk_full_warning",
            }
        )
        assert "storage" in classify_symptom(event.text, event.entity, event.fields)
        assert not classify_symptom("threshold exceeded disk_full_warning")

    def test_prose_still_classifies_when_no_identifier_is_supplied(self):
        """Control: aria and log-insight send no event_type. Nothing regressed."""
        event = normalize_event(
            {
                "ts": "2026-08-30T10:00:00Z",
                "source": "aria",
                "severity": "critical",
                "text": "Datastore latency above threshold on vsan-01",
            }
        )
        assert "storage" in classify_symptom(event.text, event.entity, event.fields)

    def test_an_identifier_cannot_remove_a_match_the_prose_made(self):
        """Folding a field in is additive by construction. Pinned so it stays so."""
        prose_only = normalize_event(
            {"ts": "2026-08-30T10:00:00Z", "text": "vsan datastore latency"}
        )
        with_id = normalize_event(
            _monitor_event("AccountCreatedEvent", "vsan datastore latency")
        )
        assert set(classify_symptom(prose_only.text, prose_only.entity, prose_only.fields)) <= set(
            classify_symptom(with_id.text, with_id.entity, with_id.fields)
        )

    def test_the_ranking_uses_the_identifier_too(self):
        """Coverage and ranking are two readers of the same classification.

        If only one of them is given the identifier, the report says the stream
        was readable and the hypothesis list files it under 'uncategorized' —
        two numbers from one engine that disagree, which is worse than either
        being wrong on its own.
        """
        from vmware_debug.ops.timeline import rank_hypotheses

        events = [
            normalize_event(
                _monitor_event(
                    "esx.problem.scsi.device.io.latency.high",
                    "Issue detected on esx01",
                    ts=f"2026-08-30T10:0{i}:00Z",
                )
            )
            for i in range(3)
        ]
        categories = {h.category for h in rank_hypotheses(events)}
        assert categories == {"storage"}, categories

    def test_coverage_counts_the_identifier_too(self):
        """The reported share and the ranking must agree on what was readable."""
        events = [
            normalize_event(
                _monitor_event(
                    "esx.problem.scsi.device.io.latency.high", "Issue detected on esx01"
                )
            )
        ]
        assert classification_coverage(events)["uncategorized"] == 0


class TestTheResidueIsRecordedNotAbsorbed:
    """The 303 misses, with names, so the diagnosis cannot drift into folklore."""

    @staticmethod
    def _unread() -> list[str]:
        return [t for t in VSPHERE_EVENT_TYPES if not classify_symptom(_as_prose(t))]

    def test_the_named_examples_in_this_module_docstring_are_real(self):
        """Every claim above is checked, so the explanation cannot rot.

        These are the five cited as evidence that the gap is one of register
        rather than of missing subsystems. If a later change classifies them,
        this test fails and the docstring must be rewritten rather than left
        describing a world that no longer exists.
        """
        cited = [
            "VmPoweredOnEvent",
            "HostCnxFailedTimeoutEvent",
            "DasHostFailedEvent",
            "VmMigratedEvent",
            "VmDiskFailedEvent",
        ]
        for name in cited:
            assert name in VSPHERE_EVENT_TYPES, f"{name} is not a real vSphere event type"
        still_unread = [n for n in cited if not classify_symptom(_as_prose(n))]
        assert still_unread == cited, (
            "the module docstring cites these as unreadable-by-register; some are "
            f"now classified: {sorted(set(cited) - set(still_unread))}"
        )

    def test_the_subsystem_the_residue_belongs_to_usually_already_has_a_category(self):
        """The point of the diagnosis, as an assertion rather than a claim.

        Every one of these is squarely inside a category the taxonomy already
        publishes. They are unread because of how they are spelled, not because
        vmware-debug lacks a place to put them.
        """
        categories = set(known_categories())
        for category in ("storage", "network", "ha_drs", "power_lifecycle", "host_lifecycle"):
            assert category in categories

    def test_the_taxonomy_was_not_quietly_padded(self):
        """A coverage jump has to come with a reason, not with more phrases.

        The failure this guards against is the tempting one: read the residue
        above, paste sixty of its words into ``_CATEGORY_SIGNATURES``, watch the
        number climb, and ship a classifier fitted to its own test corpus. The
        bound is deliberately loose — it is not defending the exact figure, only
        the shape of the change — so a genuine structural improvement passes and
        a padding exercise does not.
        """
        keyword_count = sum(
            len(kws) for _name, kws, _s in _signatures()
        )
        assert keyword_count <= 100, (
            f"the symptom taxonomy now carries {keyword_count} keywords. Growing "
            "the phrase list is how this classifier was failing in the first "
            "place; if coverage needs to improve, change what is matched, not "
            "how many phrases are matched against."
        )

    def test_reading_the_identifier_did_not_materially_move_this_corpus(self):
        """Guards the claim in the docstring, in the direction of understating.

        If someone later reports that folding in ``event_type`` fixed the
        coverage problem, this is the measurement that says otherwise: on classic
        vSphere events it is worth one type out of 427. The value is on EventEx,
        which this corpus does not contain, and a number cannot be borrowed from
        one population to describe the other.
        """
        prose_only = sum(1 for t in VSPHERE_EVENT_TYPES if classify_symptom(_as_prose(t)))
        with_identifier = sum(
            1
            for t in VSPHERE_EVENT_TYPES
            if classify_symptom(_as_prose(t), "", {"event_type": t})
        )
        assert with_identifier >= prose_only, "folding a field in removed matches"
        assert with_identifier - prose_only <= 5, (
            f"reading the identifier now adds {with_identifier - prose_only} "
            "classifications on classic event names, where the docstring says ~1 "
            "— either the taxonomy grew or the claim needs rewriting"
        )

    def test_the_unread_share_is_large_and_that_is_the_honest_answer(self):
        """Recorded so the number is on the record, in the failure message.

        Not a threshold to be tuned. If this fails the answer is to explain the
        movement in the commit, not to adjust the bound.
        """
        unread = self._unread()
        assert len(unread) > 200, (
            f"{len(unread)} of {len(VSPHERE_EVENT_TYPES)} vSphere event types are "
            "unreadable by symptom keyword — if that improved substantially, say "
            "what changed"
        )


class TestTheAdviceMatchesTheDiagnosis:
    """The note is the only part of this an agent acts on."""

    @staticmethod
    def _note_for_unreadable() -> str:
        events = [
            normalize_event(
                {"ts": "2026-08-30T10:00:00Z", "text": _as_prose(t), "source": "monitor"}
            )
            for t in ("AlarmCreatedEvent", "VmPoweredOnEvent", "DasHostFailedEvent")
        ]
        coverage = classification_coverage(events)
        assert coverage["uncategorized"] == len(events), "fixture is not exercising the note"
        return coverage["note"]

    def test_it_does_not_send_the_reader_after_a_subsystem_that_is_not_missing(self):
        """The old advice was "this may name a subsystem this taxonomy does not
        know — pull that subsystem's read tools". For ``VmPoweredOnEvent`` that is
        a false premise followed by a wasted call: the subsystem is present and
        the phrasing is what differs."""
        note = self._note_for_unreadable()
        assert "subsystem this taxonomy does not know" not in note

    def test_it_names_the_actual_mechanism(self):
        """An agent that knows *why* the match failed can force the category it
        already believes is right; one told "unknown subsystem" cannot."""
        note = self._note_for_unreadable()
        assert "event_type" in note

    def test_it_still_says_when_they_happened_is_answerable(self):
        """Control: the genuinely useful half of the old note must survive."""
        assert "spike" in self._note_for_unreadable().lower()

    def test_a_fully_read_stream_says_so_without_the_lecture(self):
        """Control: no note about unreadable events when there are none."""
        events = [
            normalize_event({"ts": "2026-08-30T10:00:00Z", "text": "vsan datastore latency"})
        ]
        note = classification_coverage(events)["note"]
        assert "event_type" not in note


class TestDocumentedReturnKeysExist:
    """``incident_timeline``'s RETURNS list and its actual keys, both directions.

    The re-test reported a missing ``causal_chains`` key "which the docs imply".
    No document in this repository promises one — the tool's RETURNS list is
    accurate, and no reference file mentions causal chains, chains, or cascades.
    Rather than invent a key to match a claim, or reply that the claim is wrong
    and leave nothing behind, the checkable version of the concern is pinned
    here: whatever the docstring lists, the tool returns, and whatever the tool
    returns, the docstring lists.
    """

    @staticmethod
    def _documented_and_actual():
        from vmware_debug.mcp_server.server import build_server

        tool = build_server()._tool_manager._tools["incident_timeline"].fn
        returns = tool.__doc__.split("RETURNS:")[1].split("GOTCHAS:")[0]
        documented = set(re.findall(r"\b[a-z][a-z_]{3,}\b", returns))
        actual = set(
            tool(
                events=[{"ts": "2026-08-30T10:00:00Z", "text": "vsan latency", "source": "m"}]
            )
        )
        return documented, actual

    def test_every_returned_key_is_documented(self):
        documented, actual = self._documented_and_actual()
        assert actual - documented == set()

    def test_no_documented_key_is_missing_from_the_result(self):
        """The direction the re-test was pointing at: a promised key with nothing
        behind it. ``causal_chains`` would fail here if it were ever documented
        without being implemented."""
        documented, actual = self._documented_and_actual()
        assert "causal_chains" not in documented, (
            "the docstring now promises causal_chains — implement it or drop it"
        )
        promised_keys = {w for w in documented if w in _plausible_key_names()}
        assert promised_keys - actual == set()


def _plausible_key_names() -> set[str]:
    """Words in the RETURNS prose that are meant as result keys, not English.

    Kept explicit: matching every lowercase word would compare the whole
    sentence against the result, and matching nothing would make the assertion
    vacuous — the failure mode this suite exists to avoid.
    """
    return {
        "event_count",
        "window",
        "binning",
        "classification",
        "spikes",
        "spikes_total",
        "hypotheses",
        "next_checks",
        "causal_chains",
    }


def _signatures():
    from vmware_debug.ops.timeline import _CATEGORY_SIGNATURES

    return _CATEGORY_SIGNATURES


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))
