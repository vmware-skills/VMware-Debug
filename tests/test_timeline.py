"""Unit tests for the vmware-debug correlation engine and event envelope."""

from __future__ import annotations

import pytest

from vmware_debug.envelope import (
    Event,
    normalize_event,
    normalize_events,
    normalize_severity,
    parse_timestamp,
)
from vmware_debug.ops.timeline import (
    bin_events,
    build_timeline,
    detect_spikes,
    incident_timeline,
    known_categories,
    rank_hypotheses,
)


# ── envelope: timestamp parsing ─────────────────────────────────────────


def test_parse_timestamp_epoch_seconds():
    assert parse_timestamp(1582675200) == 1582675200.0


def test_parse_timestamp_epoch_millis_autodetect():
    # 13-digit epoch is millis -> divided by 1000
    assert parse_timestamp(1582675200000) == 1582675200.0


def test_parse_timestamp_iso_with_z():
    assert parse_timestamp("2020-02-26T00:00:00Z") == pytest.approx(1582675200.0)


def test_parse_timestamp_rejects_garbage():
    with pytest.raises(ValueError):
        parse_timestamp("not-a-time")


def test_parse_timestamp_rejects_bare_year_string():
    # "2020" must not silently become epoch second 2020 (-> 1970).
    with pytest.raises(ValueError):
        parse_timestamp("2020")


def test_parse_timestamp_rejects_implausible_small_epoch():
    with pytest.raises(ValueError):
        parse_timestamp(1)


def test_parse_timestamp_rejects_bool():
    with pytest.raises(ValueError):
        parse_timestamp(True)


# ── envelope: severity + normalisation ──────────────────────────────────


def test_normalize_severity_aliases():
    assert normalize_severity("CRITICAL") == "critical"
    assert normalize_severity("warn") == "warning"
    assert normalize_severity("red") == "error"
    assert normalize_severity("nonsense") == "unknown"
    assert normalize_severity(None) == "unknown"


def test_normalize_event_maps_varied_field_names():
    raw = {
        "timestamp": "2020-02-26T00:00:00Z",
        "criticality": "warn",
        "resourceName": "vm-web01",
        "fullFormattedMessage": "scsi latency high on datastore ds1",
        "extra": "kept",
    }
    e = normalize_event(raw, source="aria")
    assert e.source == "aria"
    assert e.severity == "warning"
    assert e.entity == "vm-web01"
    assert "scsi latency" in e.text
    assert e.fields == {"extra": "kept"}


def test_normalize_event_requires_timestamp():
    with pytest.raises(ValueError):
        normalize_event({"message": "no ts"})


def test_normalize_events_reports_bad_index():
    with pytest.raises(ValueError, match=r"event\[1\]"):
        normalize_events([{"ts": 1582675200}, {"message": "missing ts"}])


# ── timeline + bins ─────────────────────────────────────────────────────


def _ev(ts, severity="info", source="monitor", text="", entity=""):
    return Event(ts=ts, source=source, severity=severity, entity=entity, text=text)


def test_build_timeline_sorts():
    evs = [_ev(30), _ev(10), _ev(20)]
    assert [e.ts for e in build_timeline(evs)] == [10, 20, 30]


def test_bin_events_counts_and_dimensions():
    evs = [
        _ev(0, "error", "monitor"),
        _ev(1, "warning", "aria"),
        _ev(12, "info", "monitor"),
    ]
    buckets = bin_events(evs, bin_seconds=10)
    assert len(buckets) == 2
    assert buckets[0].count == 2
    assert buckets[0].by_severity == {"error": 1, "warning": 1}
    assert buckets[0].by_source == {"monitor": 1, "aria": 1}
    assert buckets[1].count == 1


def test_bin_events_rejects_nonpositive_width():
    with pytest.raises(ValueError):
        bin_events([_ev(0)], bin_seconds=0)


# ── spike detection ─────────────────────────────────────────────────────


def test_detect_spikes_finds_burst():
    # 9 quiet bins (1 event each) then a burst of 50.
    evs = [_ev(i * 10) for i in range(9)]
    evs += [_ev(90 + i * 0.1) for i in range(50)]
    buckets = bin_events(evs, bin_seconds=10)
    spikes = detect_spikes(buckets, z_threshold=2.0)
    assert spikes
    assert max(s.count for s in spikes) >= 50


def test_detect_spikes_needs_baseline():
    assert detect_spikes(bin_events([_ev(0), _ev(1)], bin_seconds=10)) == []


# ── hypothesis ranking ──────────────────────────────────────────────────


def test_rank_hypotheses_orders_by_severity_weight():
    evs = [
        _ev(1, "critical", "storage", text="datastore ds1 APD all paths down"),
        _ev(2, "info", "monitor", text="vmotion completed for vm-web01"),
        _ev(3, "warning", "monitor", text="cpu ready high"),
    ]
    hyps = rank_hypotheses(evs)
    assert hyps[0].category == "storage"  # critical outweighs the rest
    assert hyps[0].suggested_check  # has a routing idea
    cats = {h.category for h in hyps}
    assert "network" in cats and "compute" in cats


def test_rank_hypotheses_keeps_uncategorized_visible():
    hyps = rank_hypotheses([_ev(1, "error", text="something totally novel")])
    assert hyps[0].category == "uncategorized"
    # The advice used to be "widen the search window", which is a loop for
    # anyone who has already widened it. What replaces it has to be reachable
    # from what the caller is holding.
    assert "widen" not in hyps[0].suggested_check.lower()
    assert "sample_text" in hyps[0].suggested_check


# ── top-level incident_timeline ─────────────────────────────────────────


def test_incident_timeline_empty_gives_starting_advice():
    out = incident_timeline([])
    assert out["event_count"] == 0
    assert out["hypotheses"] == []
    assert out["next_checks"] and "vmware-monitor" in out["next_checks"][0]


def test_incident_timeline_full_shape():
    evs = [
        _ev(1000, "critical", "storage", text="vsan disk full, no space on ds1"),
        _ev(1001, "error", "loginsight", text="scsi apd event vmkernel"),
        _ev(1002, "warning", "nsx", text="dfw rule drop packet"),
    ]
    out = incident_timeline(evs)
    assert out["event_count"] == 3
    assert out["window"]["start"] == 1000
    assert out["hypotheses"][0]["category"] == "storage"
    assert any("log-insight" in c or "storage" in c for c in out["next_checks"])


def test_known_categories_nonempty():
    cats = known_categories()
    assert "storage" in cats and "network" in cats and "auth" in cats
