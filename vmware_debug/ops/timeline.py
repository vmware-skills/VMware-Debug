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
        # A host changing its own availability state. Split from
        # power_lifecycle, whose vocabulary is entirely VM-centric — "Shut down
        # of esxi05" contains no "power off", and 3050 of 3818 real vCenter
        # events on 2026-08-03 landed in uncategorized because of it. The two
        # are also different investigations: a VM that will not boot is an
        # aiops task question, four hosts that took themselves out of service
        # inside 32 seconds is a cluster, DPM, vLCM or drift question.
        "host_lifecycle",
        ("maintenance mode", "entering maintenance", "entered maintenance",
         "exit maintenance", "exited maintenance", "standby mode",
         "entering standby", "exited standby", "host shutdown", "shut down of",
         "shutting down host", "host reboot", "reboot of host",
         "lost connection to", "connection lost", "host connection",
         "not responding", "cannot synchronize", "sync failed", "host sync"),
        "vmware-monitor (host connection state, cluster_health_summary for the "
        "hosts still up, and get_events on the cluster for what preceded the "
        "first transition) + vmware-harden (list_drift_events — a host that "
        "left service on cue was usually told to) + vmware-log-insight (vpxd "
        "and hostd around the first host)",
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


#: What to do with events the taxonomy could not read.
#:
#: The text this replaces said "widen the search window", and it was the advice
#: returned for a window that was already 40 days wide. An instruction the
#: reader has already followed to its limit is a loop, not a next step — and
#: widening is the one move that makes a burst *less* visible, so it was also
#: wrong. Everything named here is reachable from what the caller has in hand.
_UNCATEGORIZED_REMEDY = (
    "No symptom keyword matched these. They are counted, not dropped: read "
    "sample_text for what they actually say, and use the spikes above — pass "
    "bin_seconds to change the resolution — to find WHEN they clustered, which "
    "is answerable without knowing what they are. If they name a subsystem "
    "this taxonomy does not know, run list_symptom_categories to see what is "
    "recognised and pull that subsystem's own read tools. The span of the "
    "window is not what makes a burst visible; the bin resolution is."
)

#: Above this share of unreadable events, the fact is repeated in next_checks
#: rather than left in `classification`. A quarter of the stream unread is
#: enough to change which hypothesis ranks first, and a conclusion that does
#: not say how much it could not read has the same shape as the events it could
#: not read: absence presented as absence of a problem.
_UNREAD_SHARE_LOUD = 0.25

#: How many distinct unmatched texts to quote back. Enough to recognise a
#: pattern, few enough not to become the answer.
_UNMATCHED_SAMPLES = 5

#: Bin widths the auto-selector chooses between: a second, ten seconds, a
#: minute, five minutes, a quarter hour, an hour, six hours, a day. Human units
#: rather than an arbitrary division of the span, so the width that comes back
#: is one a person can reason about.
_BIN_LADDER: tuple[float, ...] = (1.0, 10.0, 60.0, 300.0, 900.0, 3600.0, 21600.0, 86400.0)

#: The mean events per bin the chosen width has to reach.
#:
#: detect_spikes flags a bin at mean + 2 sd. For counts, sd grows as the square
#: root of the mean, so mean + 2 sd only exceeds *twice* the mean once the mean
#: reaches four. Below that, "two standard deviations above normal" is satisfied
#: by three routine events landing in one bin, and ordinary traffic comes back
#: full of spikes. Four is where the threshold starts meaning "twice the usual
#: rate" — derived from the test detect_spikes applies, not tuned to a dataset.
_MIN_EVENTS_PER_BIN = 4.0

#: detect_spikes has no baseline below three bins.
_MIN_BINS = 3

#: How many spikes come back. A finer resolution finds more of them, and a list
#: of several dozen "anomalies" is a list nobody reads — the one that matters is
#: lost in it. The strongest are kept and returned in time order; `spikes_total`
#: reports how many there were, so the cut is visible rather than silent.
_MAX_SPIKES_REPORTED = 20


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


@dataclass(frozen=True)
class CategoryMatch:
    """One category a text matched, and the words that made it match."""

    category: str
    keywords: tuple[str, ...]
    #: Total length of the distinct keywords that matched. A longer phrase is a
    #: more specific claim about the text than a common noun, so this ranks a
    #: category matched by "lost connection to" above one matched by
    #: "datastore" appearing once as a consequence.
    strength: int
    suggested_check: str


def _match_categories(text: str, entity: str) -> tuple[CategoryMatch, ...]:
    """Every signature the text matches, strongest signal first.

    Ranking by matched-keyword length rather than by count, and rather than by
    table order. The same incident described five ways used to route five ways —
    storage, network, auth, compute, and nothing — because whichever category
    owned an incidental noun in the sentence won. Counting matches does not fix
    that on its own: "a vpxd service restart did not bring them back" matches
    three short platform words and two long host-lifecycle phrases, and the
    three short words are not what the sentence is about.

    ``sorted`` is stable, so a genuine tie falls back to the table's own order
    and the answer stays deterministic.
    """
    haystack = f"{text} {entity}".lower()
    matches = []
    for category, keywords, suggestion in _CATEGORY_SIGNATURES:
        hits = tuple(kw for kw in keywords if kw in haystack)
        if hits:
            matches.append(
                CategoryMatch(
                    category=category,
                    keywords=hits,
                    strength=sum(len(kw) for kw in hits),
                    suggested_check=suggestion,
                )
            )
    return tuple(sorted(matches, key=lambda m: -m.strength))


def _categorize(text: str, entity: str) -> list[tuple[str, str]]:
    """Return (category, suggested_check) for every signature the text matches."""
    return [(m.category, m.suggested_check) for m in _match_categories(text, entity)]


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
                suggested_check=g["suggestion"] or _UNCATEGORIZED_REMEDY,
            )
        )
    hypotheses.sort(key=lambda h: (h.score, h.last_seen), reverse=True)
    return hypotheses[:top_n]


def classification_coverage(events: list[Event]) -> dict:
    """How much of the stream the taxonomy could actually read.

    Reported rather than absorbed. On 2026-08-03, 3050 of 3818 real vCenter
    events matched nothing and the answer said only that no pattern had been
    found — the 80% never appeared anywhere, so the ranking of the 20% read as
    a ranking of the incident.
    """
    unmatched: list[str] = []
    seen: set[str] = set()
    categorized = 0
    for e in events:
        if _match_categories(e.text, e.entity):
            categorized += 1
        elif e.text and e.text not in seen:
            seen.add(e.text)
            unmatched.append(e.text)

    total = len(events)
    uncategorized = total - categorized
    share = round(uncategorized / total, 3) if total else 0.0
    return {
        "total": total,
        "categorized": categorized,
        "uncategorized": uncategorized,
        "uncategorized_share": share,
        "unmatched_samples": [t[:200] for t in unmatched[:_UNMATCHED_SAMPLES]],
        "distinct_unmatched_texts": len(unmatched),
        "note": _coverage_note(total, uncategorized, share),
    }


def _coverage_note(total: int, uncategorized: int, share: float) -> str:
    if not total:
        return "No events to classify."
    if not uncategorized:
        return f"All {total} event(s) matched at least one symptom category."
    return (
        f"{uncategorized} of {total} event(s) ({round(share * 100)}%) matched "
        f"no symptom keyword and are grouped under 'uncategorized' rather than "
        f"dropped. They are still binned and still counted in the spikes, so "
        f"WHEN they happened is answerable even though WHAT they are is not. "
        + _UNCATEGORIZED_REMEDY
    )


def _bin_count(span: float, width: float) -> int:
    return int(span // width) + 1


def _auto_bin_seconds(events: list[Event]) -> float:
    """Choose a bin width from event DENSITY, not from the length of the window.

    The old rule was span/30, and it inverted the incentive that matters most.
    When you do not know when an incident began you widen the window — and a
    wider window bought wider bins, which averaged the incident into the
    baseline. A 29-day window gave 23.2-hour bins; the hour in which four ESXi
    hosts died, the second-busiest hour in the data, produced no spike at all,
    while the recovery day was busy long enough to fill a bucket and was the
    only spike reported. A manual bin_seconds=3600 found the incident at z=8.25.

    Density has no such coupling: lengthening a window at the same event rate
    leaves the chosen width where it was. Of the widths whose baseline can still
    tell a burst from jitter, the finest is taken, because a finer bin is what
    makes a short burst sharp.
    """
    ordered = build_timeline(events)
    span = ordered[-1].ts - ordered[0].ts
    if span <= 0:
        return 1.0
    for width in _BIN_LADDER:
        if len(ordered) / _bin_count(span, width) >= _MIN_EVENTS_PER_BIN:
            return width
    # Too sparse for any width to reach the floor. Take the coarsest that still
    # leaves detect_spikes a baseline: that is the same criterion — most events
    # per bin — applied to what is achievable here.
    usable = [w for w in _BIN_LADDER if _bin_count(span, w) >= _MIN_BINS]
    return usable[-1] if usable else 1.0


def _binning_report(width: float, buckets: list[Bucket], count: int, from_caller: bool) -> dict:
    """The resolution the answer was computed at, stated in the answer.

    Which bin width you were given decides which bursts you could possibly have
    been shown, so it is not an implementation detail.
    """
    mean = round(count / len(buckets), 2) if buckets else 0.0
    if from_caller:
        note = (
            f"Bin width {width}s as supplied. Omit bin_seconds to have one "
            f"chosen from the event density instead."
        )
    else:
        note = (
            f"Bin width {width}s, chosen so bins average {mean} event(s) — the "
            f"finest width whose baseline can still tell a burst from ordinary "
            f"jitter. Pass bin_seconds to change it: a finer bin sharpens a "
            f"short burst, a coarser one makes a slow drift visible."
        )
    return {
        "bin_seconds": width,
        "chosen_by": "caller" if from_caller else "event-density",
        "bins": len(buckets),
        "mean_events_per_bin": mean,
        "note": note,
    }


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
                "get_events / get_alarms for the affected entity, then "
                "vmware-log-insight log_search around the reported time."
            ],
        }

    ordered = build_timeline(events)
    width = bin_seconds or _auto_bin_seconds(ordered)
    buckets = bin_events(ordered, width)
    found = detect_spikes(buckets, z_threshold=z_threshold)
    # Strongest kept, then put back in time order: the list is read as a
    # timeline, and the count that was cut is reported below.
    spikes = sorted(
        sorted(found, key=lambda s: -s.zscore)[:_MAX_SPIKES_REPORTED], key=lambda s: s.start
    )
    hyps = rank_hypotheses(ordered, top_n=top_n)
    coverage = classification_coverage(ordered)

    next_checks = [h.suggested_check for h in hyps if h.category != "uncategorized"]
    if not next_checks:
        next_checks = [coverage["note"]]
    elif coverage["uncategorized_share"] >= _UNREAD_SHARE_LOUD:
        # Ahead of the routing advice, not after it. The checks below are
        # derived from the share of the stream that WAS read, and how large
        # that share is changes what they are worth.
        next_checks.insert(0, coverage["note"])

    return {
        "event_count": len(ordered),
        "window": {"start": ordered[0].ts, "end": ordered[-1].ts, "bin_seconds": width},
        "binning": _binning_report(width, buckets, len(ordered), from_caller=bool(bin_seconds)),
        "classification": coverage,
        "spikes_total": len(found),
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


def classify_symptom(text: str, entity: str = "") -> list[str]:
    """Return the symptom categories this text matches, strongest signal first.

    The public face of the keyword taxonomy. The investigation planner needs to
    classify a case's scope summary, and reaching into ``_categorize`` for that
    would make a private helper part of another module's contract by accident.

    An empty list means nothing matched, and that stays a possible answer: a
    classifier that always has something to say has stopped being consulted.
    """
    return [m.category for m in _match_categories(text, entity)]


def classify_symptom_matches(text: str, entity: str = "") -> list[dict]:
    """Like :func:`classify_symptom`, but says what decided each category.

    A category handed over with no evidence for it cannot be argued with, and
    the classification of a scope summary is the step at which a wrong turn
    costs the whole investigation — everything downstream routes off it.
    """
    return [
        {
            "category": m.category,
            "matched_keywords": list(m.keywords),
            "strength": m.strength,
            "suggested_check": m.suggested_check,
        }
        for m in _match_categories(text, entity)
    ]


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
