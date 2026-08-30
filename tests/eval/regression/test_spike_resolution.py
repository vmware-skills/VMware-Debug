"""The only spike found in a 40-day incident was the day the hosts came back.

``_auto_bin_seconds`` was ``span / 30``, so a 29-day window gave 23.2-hour bins.
The hour in which four ESXi hosts died — 95 events, the second-busiest hour in
the whole dataset — was averaged in with a day of routine traffic and never rose
above the baseline, while the recovery day, which was busy for a whole day, sat
inside one bin and was the single spike reported. A manual
``bin_seconds=3600`` found the incident immediately at z=8.25.

The arithmetic was never wrong; the resolution was. And the incentive ran the
wrong way: when you do not know when an incident began you widen the window, and
under span/30 widening made the incident *less* findable.

The fixture below carries all three parts of the real dataset — a quiet
baseline, the 95-event hour, and the busy recovery day. The recovery day is what
makes this a regression test rather than a demonstration: without it the burst
is trivially the loudest thing in the window and the old heuristic finds it too.

The controls are the other half. "Call every bin a spike" passes every detection
test here and fails those: a stream with no burst must not come back full of
spikes, and a short dense window must still get a fine resolution rather than
the hour that happened to suit the 29-day case.
"""

from __future__ import annotations

import random

from vmware_debug.envelope import Event
from vmware_debug.ops.timeline import incident_timeline

#: A fixed epoch so every fixture is reproducible.
_START = 1785196800.0
_DAY = 86400.0

#: When the hosts died, and when they came back.
_BURST_AT = _START + 17 * _DAY
_RECOVERY_AT = _START + 23 * _DAY

#: What the incident looked like: four hosts, five event types each, repeated
#: sync failures, all inside one hour.
_BURST_MESSAGES = (
    "Host esxi0%d.knight.com has started to enter maintenance mode",
    "esxi0%d.knight.com has entered maintenance mode",
    "Shut down of esxi0%d.knight.com: operator initiated",
    "Lost connection to esxi0%d.knight.com",
    "Cannot synchronize host esxi0%d.knight.com",
)


def _ev(ts: float, text: str, severity: str = "info") -> Event:
    return Event(ts=ts, source="monitor", severity=severity, entity="vc01", text=text)


def _baseline(days: float, count: int, seed: int = 7) -> list[Event]:
    """Routine traffic, jittered rather than evenly spaced.

    A perfectly regular baseline has zero standard deviation, which makes spike
    detection trivially perfect and every control below meaningless.
    """
    rnd = random.Random(seed)
    return [
        _ev(_START + rnd.random() * days * _DAY, "routine inventory refresh")
        for _ in range(count)
    ]


def _burst(at: float, seed: int = 11) -> list[Event]:
    rnd = random.Random(seed)
    return [
        _ev(at + rnd.random() * 3600.0, template % (i % 4 + 1), "critical")
        for i in range(19)
        for template in _BURST_MESSAGES
    ]


def _recovery(at: float, count: int = 600, seed: int = 13) -> list[Event]:
    """The day everything came back: busy for a whole day, not for an hour."""
    rnd = random.Random(seed)
    return [_ev(at + rnd.random() * _DAY, "Host esxi01 connected") for _ in range(count)]


def the_incident(days: float = 29.0, baseline: int = 3123) -> list[Event]:
    return _baseline(days, baseline) + _burst(_BURST_AT) + _recovery(_RECOVERY_AT)


def _spikes_covering(out: dict, instant: float) -> list[dict]:
    return [s for s in out["spikes"] if s["start"] <= instant < s["end"]]


class TestTheIncidentIsFound:
    def test_the_incident_hour_is_detected_without_being_told_the_resolution(self):
        out = incident_timeline(the_incident())
        assert _spikes_covering(out, _BURST_AT + 1800.0), (
            "the hour four hosts died produced no spike at the auto resolution"
        )

    def test_the_recovery_day_no_longer_outranks_the_incident(self):
        """It did, and it was the only spike reported: a whole busy day inside
        one 23-hour bin beats one busy hour averaged into another."""
        out = incident_timeline(the_incident())
        strongest = max(out["spikes"], key=lambda s: s["zscore"])
        assert strongest["start"] <= _BURST_AT + 1800.0 < strongest["end"]

    def test_widening_the_window_does_not_hide_it(self):
        """The inverted incentive, stated as a test. Three times the window at
        the same event density — and the burst must still be found, or 'widen
        the window' remains a way of losing the incident."""
        out = incident_timeline(the_incident(days=87.0, baseline=3123 * 3))
        assert _spikes_covering(out, _BURST_AT + 1800.0)


class TestTheResolutionIsVisible:
    def test_the_chosen_bin_width_is_reported_with_how_it_was_chosen(self):
        out = incident_timeline(the_incident())
        assert out["binning"]["bin_seconds"] == out["window"]["bin_seconds"]
        assert out["binning"]["chosen_by"] == "event-density"
        assert out["binning"]["bins"] > 0
        assert out["binning"]["note"]

    def test_an_explicit_bin_width_is_honoured_exactly(self):
        out = incident_timeline(the_incident(), bin_seconds=3600)
        assert out["window"]["bin_seconds"] == 3600
        assert out["binning"]["chosen_by"] == "caller"


class TestControlsAgainstCallingEverythingASpike:
    def test_a_stream_with_no_burst_does_not_read_as_all_spike(self):
        out = incident_timeline(_baseline(29.0, 3818))
        assert out["spikes_total"] < out["binning"]["bins"] * 0.1, (
            "a quiet estate came back mostly spikes"
        )

    def test_the_auto_width_leaves_a_baseline_a_spike_can_stand_out_from(self):
        """z=2 only means 'twice the normal rate' once a bin averages four
        events; below that, ordinary Poisson jitter clears the threshold."""
        assert incident_timeline(_baseline(29.0, 3818))["binning"]["mean_events_per_bin"] >= 4.0

    def test_a_short_dense_window_still_gets_a_fine_resolution(self):
        """The other degenerate fix is a constant. Sixty events inside an hour
        must not be handed the hour-wide bin that suited the 29-day case."""
        events = [_ev(_START + i * 60.0, "routine inventory refresh") for i in range(60)]
        assert incident_timeline(events)["binning"]["bin_seconds"] <= 300.0

    def test_a_burst_inside_a_short_window_is_found_at_that_resolution(self):
        events = [_ev(_START + i * 60.0, "routine inventory refresh") for i in range(60)]
        events += [_ev(_START + 1800.0 + i, "Lost connection to esxi01") for i in range(40)]
        assert _spikes_covering(incident_timeline(events), _START + 1810.0)


class TestTheSpikeListStaysReadable:
    """Finer bins find more spikes. A list of several dozen is a list nobody
    reads, and the two that matter are lost in it — so the cut has to be made
    and it has to be visible."""

    def test_the_list_is_capped_and_the_true_count_reported(self):
        """`spikes_total` counting only what survived would make the cut
        invisible, which is the cut being silent — the thing it exists to
        avoid. This fixture genuinely produces more than the cap."""
        out = incident_timeline(the_incident())
        assert len(out["spikes"]) < out["spikes_total"]

    def test_the_strongest_survives_the_cut(self):
        out = incident_timeline(the_incident())
        assert _spikes_covering(out, _BURST_AT + 1800.0)

    def test_control_a_short_spike_list_is_not_trimmed(self):
        events = [_ev(_START + i * 60.0, "routine inventory refresh") for i in range(60)]
        events += [_ev(_START + 1800.0 + i, "Lost connection to esxi01") for i in range(40)]
        out = incident_timeline(events)
        assert out["spikes_total"] == len(out["spikes"])
