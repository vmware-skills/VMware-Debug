"""The unified event envelope — the contract between vmware-debug and every
data-source skill (monitor, aria, log-insight, nsx, ...).

vmware-debug deliberately has NO runtime dependency on the other skill packages
(CLAUDE.md 踩坑 #21/#32: no hidden cross-skill coupling, no version lockstep).
Instead the orchestrating agent fetches events with each skill's own read tools
and hands them to debug's correlator as plain dicts. This module normalises
those heterogeneous dicts into one immutable ``Event`` shape so the timeline /
spike / hypothesis logic can stay source-agnostic and unit-testable.

Envelope shape (also documented in references/event-envelope.md):

    {
      "ts":       <ISO8601 string | epoch seconds | epoch millis>,
      "source":   "monitor" | "aria" | "loginsight" | "nsx" | ...,
      "severity": "critical" | "error" | "warning" | "info" | "unknown",
      "entity":   "vm-web01" | "host-12" | "" ,
      "text":     "<human-readable message>",
      "fields":   { ... source-specific extras ... }
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Canonical severities, ordered by weight (higher = more severe). Used both for
# normalisation and for hypothesis scoring.
SEVERITY_WEIGHT: dict[str, int] = {
    "critical": 5,
    "error": 4,
    "warning": 3,
    "info": 1,
    "unknown": 0,
}

# Common vendor spellings mapped onto the canonical set. Lower-cased on lookup.
_SEVERITY_ALIASES: dict[str, str] = {
    "crit": "critical",
    "critical": "critical",
    "fatal": "critical",
    "alert": "critical",
    "emergency": "critical",
    "err": "error",
    "error": "error",
    "red": "error",
    "warn": "warning",
    "warning": "warning",
    "yellow": "warning",
    "notice": "info",
    "info": "info",
    "information": "info",
    "informational": "info",
    "green": "info",
    "debug": "info",
}


#: Characters of a rejected value's repr that fit in a rejection message.
#:
#: Small on purpose. These messages nest — ``normalize_events`` wraps
#: ``normalize_event``'s text to add the entry index — so two remedies and the
#: evidence share one 500-char budget at the MCP wrapper. Unbounded, a single
#: vCenter event repr (~430 characters) pushed both remedies past the cap and
#: the agent received a truncated dict dump with no next step.
#:
#: Short evidence costs little here: the caller passed these events in, so it
#: can read ``events[i]`` itself. The index is the load-bearing part; the repr
#: only confirms which entry is meant. Keeping it short also keeps whatever the
#: source skill put in the event — a username, a task URL — from being echoed
#: back wholesale through the one path that reaches the agent verbatim.
#:
#: 60 is the largest value at which the worst composed message — the batch
#: wrapper around the no-timestamp rejection, the longest pair in this module —
#: still lands under the 500-char cap complete, marker included. Raising it
#: would put the cut back inside the evidence, where it is invisible.
_MAX_VALUE_REPR = 60


def _short_repr(value: object, limit: int = _MAX_VALUE_REPR) -> str:
    """Repr ``value``, truncated to ``limit`` with a visible marker.

    The marker matters: ``sanitize()`` truncates silently, so a cut message
    reads as a complete one.
    """
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…(truncated)"


@dataclass(frozen=True)
class Event:
    """One normalised observation on the incident timeline."""

    ts: float  # epoch seconds (UTC)
    source: str
    severity: str
    entity: str
    text: str
    fields: dict = field(default_factory=dict)


def normalize_severity(raw: object) -> str:
    """Map an arbitrary severity token onto the canonical set."""
    if raw is None:
        return "unknown"
    return _SEVERITY_ALIASES.get(str(raw).strip().lower(), "unknown")


# Numeric timestamps below this (epoch seconds for ~1973-03) are implausible for
# VMware incident data and almost certainly a parse error (e.g. a bare year like
# "2020" -> 1970). Rejected loudly rather than landing silently at the epoch.
_MIN_PLAUSIBLE_EPOCH = 10**8


def parse_timestamp(raw: object) -> float:
    """Parse a timestamp into epoch seconds (UTC).

    Accepts ISO-8601 strings (with or without 'Z'), epoch seconds, or epoch
    milliseconds (auto-detected by magnitude). ISO is tried before the numeric
    fallback, and implausibly small epochs are rejected, so a malformed value
    (a bare year, garbage) surfaces loudly rather than landing at 1970.
    bool is excluded explicitly (it is an int subclass).
    """
    if isinstance(raw, bool):
        raise ValueError(
            f"unparseable timestamp: {raw!r} — a bool is not a time. Set the event's "
            "'ts' field to an ISO-8601 string, epoch seconds, or epoch millis "
            "(e.g. '2026-07-20T14:03:00Z' or 1784908980), then re-run "
            "incident_timeline."
        )
    if isinstance(raw, (int, float)):
        value = float(raw)
        # Values past ~year 2286 in seconds are really milliseconds.
        if value > 1e11:
            value /= 1000.0
        if value < _MIN_PLAUSIBLE_EPOCH:
            raise ValueError(
                f"implausible epoch timestamp: {raw!r} — earlier than 1973, which "
                "usually means a bare year (2020) or a truncated value was passed "
                "instead of a real epoch. Set 'ts' to full epoch seconds/millis or "
                "an ISO-8601 string (e.g. '2026-07-20T14:03:00Z'), then re-run "
                "incident_timeline."
            )
        return value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError(
                "empty timestamp: the event's 'ts' field is blank. It must be an "
                "ISO-8601 string, epoch seconds, or epoch millis (e.g. "
                "'2026-07-20T14:03:00Z'). Copy 'ts' from the source event as the "
                "producing skill returned it — vmware-monitor, vmware-aria and "
                "vmware-log-insight all carry one — then re-run incident_timeline."
            )
        # ISO-8601 first (so "2020-..." is a date, not epoch 2020).
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
        # Then a numeric epoch string, subject to the same plausibility floor.
        # float() must not be called bare: its own ValueError ("could not
        # convert string to float: 'not-a-time'") is on the MCP allowlist by
        # type, so it would reach the agent as-is — bypassing all four authored
        # messages here and arriving with a diagnosis but no remedy.
        try:
            numeric = float(text)
        except ValueError as exc:
            raise ValueError(
                "unparseable timestamp: 'ts' is a string that is neither "
                "ISO-8601 nor numeric. Use ISO-8601, epoch seconds, or epoch "
                "millis — e.g. '2026-07-20T14:03:00Z' or 1784908980. Copy 'ts' "
                "from the source event as the producing skill returned it. "
                f"Got: {_short_repr(text)}"
            ) from exc
        return parse_timestamp(numeric)
    raise ValueError(
        f"unparseable timestamp of type {type(raw).__name__}. 'ts' must be "
        "an ISO-8601 string, epoch seconds, or epoch millis — e.g. "
        "'2026-07-20T14:03:00Z' or 1784908980. Convert that value before passing "
        f"the event to incident_timeline. Got: {_short_repr(raw)}"
    )


def _first(d: dict, *keys: str) -> object:
    """Return the first present, non-None value among ``keys``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def normalize_event(raw: dict, source: str | None = None) -> Event:
    """Normalise one source-specific event dict into an :class:`Event`.

    Tolerant of the common field-name variations across vCenter events, Aria
    alerts/anomalies, Log Insight events, and NSX. Unknown extras are preserved
    under ``fields`` so nothing is silently dropped.
    """
    ts_raw = _first(raw, "ts", "timestamp", "time", "createTime", "startTimeUTC")
    if ts_raw is None:
        # Remedy before evidence: `raw` is a whole caller-supplied event, the
        # one part of this message whose length this code does not control.
        # No "re-run incident_timeline" here — normalize_events always wraps
        # this text and says it, and saying it twice is what pushed the
        # composed message past the cap.
        raise ValueError(
            "event has no timestamp field — expected one of "
            "ts/timestamp/time/createTime/startTimeUTC. Add 'ts' (ISO-8601, epoch "
            "seconds, or epoch millis) from the event as vmware-monitor / "
            "vmware-aria / vmware-log-insight returned it. "
            f"Offending event: {_short_repr(raw)}"
        )

    src = source or _first(raw, "source", "skill") or "unknown"
    sev = normalize_severity(_first(raw, "severity", "criticality", "level", "status"))
    entity = _first(
        raw, "entity", "entity_name", "resourceName", "vm", "vm_name", "object", "host"
    )
    text = _first(raw, "text", "message", "msg", "description", "fullFormattedMessage")

    known = {
        "ts", "timestamp", "time", "createTime", "startTimeUTC",
        "source", "skill", "severity", "criticality", "level", "status",
        "entity", "entity_name", "resourceName", "vm", "vm_name", "object", "host",
        "text", "message", "msg", "description", "fullFormattedMessage",
    }
    extras = {k: v for k, v in raw.items() if k not in known}

    return Event(
        ts=parse_timestamp(ts_raw),
        source=str(src),
        severity=sev,
        entity=str(entity) if entity is not None else "",
        text=str(text) if text is not None else "",
        fields=extras,
    )


def normalize_events(raw_events: list[dict], source: str | None = None) -> list[Event]:
    """Normalise a batch, skipping nothing — a bad event raises with its index."""
    out: list[Event] = []
    for i, raw in enumerate(raw_events):
        try:
            out.append(normalize_event(raw, source))
        except (ValueError, AttributeError, TypeError) as exc:
            # This message nests the one above, so both remedies compete for the
            # same 500-char cap. Keep this layer to what it uniquely adds — the
            # index and the stops-at-first-bad-event caveat — and let the inner
            # message (which already ends in bounded evidence) trail, so a cut
            # takes evidence rather than instructions.
            raise ValueError(
                f"event[{i}] could not be normalised — fix or remove that entry "
                "and call incident_timeline again. Normalisation stops at the "
                f"first bad event, so later entries are unchecked. Cause: {exc}"
            ) from exc
    return out
