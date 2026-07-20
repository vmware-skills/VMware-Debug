"""Incident correlation engine — the heart of vmware-debug.

Pure functions over a list of normalised :class:`~vmware_debug.envelope.Event`.
No I/O, no network, no cross-skill imports: the orchestrating agent fetches
events via each data-source skill's read tools and feeds them here. That keeps
the valuable logic (timeline merge, spike detection, hypothesis ranking, and
next-check suggestions) self-contained and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vmware_debug.envelope import SEVERITY_WEIGHT, Event

# A symptom taxonomy: keyword signatures -> (category, which skill/tool to look
# at next). This is what lets debug "give a valuable idea even when the user
# doesn't know what to check". Keywords are matched case-insensitively against
# event text + entity. Order matters only for the human-readable label; scoring
# counts every category that matches.
_CATEGORY_SIGNATURES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "storage",
        ("datastore", "scsi", "latency", "vsan", "lun", "naa.", "apd", "pdl",
         "disk full", "no space", "vmfs", "iscsi"),
        "vmware-storage (datastore/vsan health) + vmware-log-insight (search "
        "the host's vmkernel for scsi/apd events around the spike)",
    ),
    (
        "network",
        ("vmotion", "vnic", "dvswitch", "dvs ", "uplink", "link down", "mtu",
         "firewall", "dfw", "segment", "tier-0", "tier-1", "bgp", "packet drop"),
        "vmware-nsx / vmware-nsx-security (run a traceflow between the affected "
        "endpoints; check DFW rule hits) + vmware-log-insight (network logs)",
    ),
    (
        "compute",
        ("cpu ready", "memory", "balloon", "swap", "contention", "overcommit",
         "numa"),
        "vmware-aria (CPU-ready / memory-contention metrics + anomalies for the "
        "VM and its host) ",
    ),
    (
        "ha_drs",
        ("ha ", "high availability", "drs", "failover", "admission control",
         "host isolation", "heartbeat"),
        "vmware-monitor (cluster + host health, recent HA/DRS events) + "
        "vmware-aiops (cluster state)",
    ),
    (
        "power_lifecycle",
        ("power on", "power off", "failed to start", "boot", "vmx", "ovf",
         "deploy", "clone", "snapshot", "consolidate"),
        "vmware-aiops (VM task status, snapshot tree) + vmware-monitor (the VM's "
        "recent events)",
    ),
    (
        "auth",
        ("login", "authentication", "permission", "denied", "unauthorized",
         "401", "403", "token", "certificate", "tls"),
        "check the service account + credentials in config/.env; verify the "
        "target's certificate/time sync",
    ),
    (
        "platform",
        ("vpxd", "hostd", "service", "restart", "crash", "core dump", "503",
         "not responding", "disconnected"),
        "vmware-monitor (host connection state + service health) + "
        "vmware-log-insight (vpxd/hostd logs around the first error)",
    ),
)


@dataclass(frozen=True)
class Bucket:
    """A time bin with event counts."""

    start: float
    end: float
    count: int
    by_severity: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Spike:
    """A bin whose count is anomalously high vs the series mean."""

    start: float
    end: float
    count: int
    zscore: float


@dataclass(frozen=True)
class Hypothesis:
    """A ranked root-cause candidate with evidence and a next step."""

    category: str
    score: float
    summary: str
    evidence_count: int
    first_seen: float
    last_seen: float
    sample_text: str
    suggested_check: str


def build_timeline(events: list[Event]) -> list[Event]:
    """Return events sorted chronologically (stable)."""
    return sorted(events, key=lambda e: e.ts)


def bin_events(events: list[Event], bin_seconds: float) -> list[Bucket]:
    """Bucket events into fixed-width time bins covering [min_ts, max_ts]."""
    if not events:
        return []
    if bin_seconds <= 0:
        raise ValueError(
            f"bin_seconds must be positive (got {bin_seconds!r}). Pass a width in "
            "seconds — e.g. bin_seconds=60 for one-minute bins — or omit it so "
            "incident_timeline derives one from the event window."
        )
    ordered = build_timeline(events)
    start = ordered[0].ts
    end = ordered[-1].ts
    n_bins = int((end - start) // bin_seconds) + 1
    counts: list[dict] = [
        {"count": 0, "by_severity": {}, "by_source": {}} for _ in range(n_bins)
    ]
    for e in ordered:
        idx = min(int((e.ts - start) // bin_seconds), n_bins - 1)
        b = counts[idx]
        b["count"] += 1
        b["by_severity"][e.severity] = b["by_severity"].get(e.severity, 0) + 1
        b["by_source"][e.source] = b["by_source"].get(e.source, 0) + 1
    return [
        Bucket(
            start=start + i * bin_seconds,
            end=start + (i + 1) * bin_seconds,
            count=b["count"],
            by_severity=b["by_severity"],
            by_source=b["by_source"],
        )
        for i, b in enumerate(counts)
    ]


def detect_spikes(buckets: list[Bucket], z_threshold: float = 2.0) -> list[Spike]:
    """Flag bins whose count exceeds mean + ``z_threshold`` * stddev.

    Needs at least 3 non-trivial bins to have a meaningful baseline; below that
    it returns nothing rather than calling every event a spike.
    """
    counts = [b.count for b in buckets]
    if len(counts) < 3:
        return []
    mean = sum(counts) / len(counts)
    variance = sum((c - mean) ** 2 for c in counts) / len(counts)
    stddev = variance**0.5
    if stddev == 0:
        return []
    spikes = []
    for b in buckets:
        z = (b.count - mean) / stddev
        if z >= z_threshold:
            spikes.append(Spike(start=b.start, end=b.end, count=b.count, zscore=z))
    return spikes


def _categorize(text: str, entity: str) -> list[tuple[str, str]]:
    """Return (category, suggested_check) for every signature the text matches."""
    haystack = f"{text} {entity}".lower()
    hits = []
    for category, keywords, suggestion in _CATEGORY_SIGNATURES:
        if any(kw in haystack for kw in keywords):
            hits.append((category, suggestion))
    return hits


def rank_hypotheses(events: list[Event], top_n: int = 5) -> list[Hypothesis]:
    """Cluster events by symptom category and rank them as root-cause candidates.

    Score = sum of severity weights of the events in the category. Ties broken
    by recency (a category whose evidence is more recent ranks higher). Events
    matching no category are grouped under "uncategorized" so they remain
    visible rather than dropped.
    """
    groups: dict[str, dict] = {}
    for e in events:
        cats = _categorize(e.text, e.entity) or [("uncategorized", "")]
        for category, suggestion in cats:
            g = groups.setdefault(
                category,
                {"score": 0.0, "events": [], "suggestion": suggestion},
            )
            g["score"] += SEVERITY_WEIGHT.get(e.severity, 0)
            g["events"].append(e)
            if suggestion:
                g["suggestion"] = suggestion

    hypotheses = []
    for category, g in groups.items():
        evs = build_timeline(g["events"])
        worst = max(evs, key=lambda e: SEVERITY_WEIGHT.get(e.severity, 0))
        hypotheses.append(
            Hypothesis(
                category=category,
                score=g["score"],
                summary=(
                    f"{len(evs)} {category} event(s); most severe is "
                    f"'{worst.severity}' from {worst.source}"
                ),
                evidence_count=len(evs),
                first_seen=evs[0].ts,
                last_seen=evs[-1].ts,
                sample_text=worst.text[:200],
                suggested_check=g["suggestion"]
                or "no category matched — widen the search window or pull events "
                "from another source (monitor events, aria alerts, log-insight)",
            )
        )
    hypotheses.sort(key=lambda h: (h.score, h.last_seen), reverse=True)
    return hypotheses[:top_n]


def _auto_bin_seconds(events: list[Event]) -> float:
    """Pick a sensible bin width from the event span (~30 bins, min 1s)."""
    ordered = build_timeline(events)
    span = ordered[-1].ts - ordered[0].ts
    if span <= 0:
        return 1.0
    return max(1.0, span / 30.0)


def incident_timeline(
    events: list[Event],
    *,
    bin_seconds: float | None = None,
    z_threshold: float = 2.0,
    top_n: int = 5,
) -> dict:
    """Top-level correlation: timeline summary + spikes + ranked hypotheses.

    Returns a JSON-serialisable dict suitable for an MCP tool response. Empty
    input yields an explicit "no events" result with a suggestion rather than
    an empty/ambiguous payload.
    """
    if not events:
        return {
            "event_count": 0,
            "window": None,
            "spikes": [],
            "hypotheses": [],
            "next_checks": [
                "No events supplied. Pull a starting set: vmware-monitor "
                "event_list / alarm_list for the affected entity, then "
                "vmware-log-insight log_search around the reported time."
            ],
        }

    ordered = build_timeline(events)
    width = bin_seconds or _auto_bin_seconds(ordered)
    buckets = bin_events(ordered, width)
    spikes = detect_spikes(buckets, z_threshold=z_threshold)
    hyps = rank_hypotheses(ordered, top_n=top_n)

    next_checks = [h.suggested_check for h in hyps if h.category != "uncategorized"]
    if not next_checks:
        next_checks = [
            "No known symptom pattern matched. Widen the time window, or pull "
            "metrics from vmware-aria (anomalies) and logs from "
            "vmware-log-insight to enrich the timeline."
        ]

    return {
        "event_count": len(ordered),
        "window": {"start": ordered[0].ts, "end": ordered[-1].ts, "bin_seconds": width},
        "spikes": [
            {"start": s.start, "end": s.end, "count": s.count, "zscore": round(s.zscore, 2)}
            for s in spikes
        ],
        "hypotheses": [
            {
                "category": h.category,
                "score": h.score,
                "summary": h.summary,
                "evidence_count": h.evidence_count,
                "first_seen": h.first_seen,
                "last_seen": h.last_seen,
                "sample_text": h.sample_text,
                "suggested_check": h.suggested_check,
            }
            for h in hyps
        ],
        "next_checks": next_checks,
    }


# Kept module-private but exported for tests that assert the catalogue stays
# in sync with the routing playbooks.
def known_categories() -> list[str]:
    """Return the symptom categories debug knows how to route."""
    return [name for name, _kw, _s in _CATEGORY_SIGNATURES]


def category_routing() -> list[dict]:
    """Return each symptom category with sample keywords and its next-check idea.

    Powers the discovery tool that answers "what can you help me check?" — and
    lets a regression test keep this catalogue in sync with the playbooks.
    """
    return [
        {"category": name, "example_keywords": list(keywords[:6]), "suggested_check": suggestion}
        for name, keywords, suggestion in _CATEGORY_SIGNATURES
    ]
