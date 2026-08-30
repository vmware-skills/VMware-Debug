"""What counts as an event payload, and saying so where it is submitted.

``case_submit_evidence`` took ``payload: Any`` and documented it as "the raw
result". A tester submitted the summary dict from a get_events call —
``{"total": 3818, "by_type": {...}}`` — and it was accepted, recorded, and
answered with a grade. Nothing said the timeline would never see it.
``case_timeline`` then reported zero events and advised submitting the
get_events result with ``payload``, which is what they had just done.

Two things were wrong and this module fixes the second, which is the one that
matters. The contract being undocumented is cheap to correct; the expensive part
is that the only moment at which the mismatch was knowable — the submission
itself — did not look.

The distinction the notes below keep is between a payload of the wrong shape and
a query that genuinely returned nothing. They produce the same zero and need
opposite responses: resubmit in one case, record a gap in the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Keys a submitted payload may carry its event rows under. ``items`` is the
#: family list envelope; the others are what the read tools that predate it use.
EVENT_KEYS: tuple[str, ...] = ("items", "events", "rows")


@dataclass(frozen=True)
class PayloadShape:
    """What one submitted payload turned out to be."""

    rows: tuple[dict, ...] = ()
    #: Which key the rows came from, ``"list"`` for a bare array, ``""`` when
    #: no carrier was found at all.
    carrier: str = ""
    #: The payload's own top-level keys, when it was a mapping. Reported back
    #: because "submit the get_events result" is unusable advice for someone
    #: who believes they did — naming what arrived is what makes the mismatch
    #: visible.
    keys: tuple[str, ...] = ()
    absent: bool = False

    @property
    def event_count(self) -> int:
        return len(self.rows)


def inspect_payload(payload: Any) -> PayloadShape:
    """Read a submitted payload without judging it. Never raises."""
    if payload is None:
        return PayloadShape(absent=True)
    if isinstance(payload, list):
        return PayloadShape(rows=_rows(payload), carrier="list")
    if isinstance(payload, dict):
        keys = tuple(str(k) for k in payload)
        for key in EVENT_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return PayloadShape(rows=_rows(value), carrier=key, keys=keys)
        return PayloadShape(keys=keys)
    return PayloadShape()


def _rows(value: list) -> tuple[dict, ...]:
    return tuple(r for r in value if isinstance(r, dict))


def payload_note(shape: PayloadShape) -> str:
    """One sentence about what this payload will and will not contribute."""
    if shape.absent:
        return (
            "No payload was submitted, so this item adds nothing to "
            "case_timeline. That is correct for evidence that is not an event "
            "stream — a knowledge citation, a configuration dump — and the item "
            "still counts as a source. Pass the read tool's raw result as "
            "`payload` if it returned events."
        )
    if shape.rows:
        return (
            f"{len(shape.rows)} event row(s) read from "
            f"{'a bare list' if shape.carrier == 'list' else '`' + shape.carrier + '`'}; "
            f"they will appear in case_timeline."
        )
    if shape.carrier:
        return (
            f"`{shape.carrier}` was present but held nothing, so this item adds "
            f"no events to case_timeline. An empty result is a finding rather "
            f"than a payload problem — if the query genuinely returned nothing, "
            f"record it with case_record_gap so the grade reflects it."
        )
    return (
        "payload carried no event rows, so this item adds nothing to "
        "case_timeline — it is recorded and still counts as a source. Events "
        "are read from a bare list, or from `items` (the family list envelope), "
        "`events` or `rows`; this payload's top-level keys are "
        f"{', '.join(shape.keys) or '(none — it is not a mapping)'}. If this was "
        "a get_events or log_search result, submit the tool's raw result rather "
        "than a summary of it."
    )


def describe_empty(evidence_id: str, shape: PayloadShape) -> str:
    """How one event-less item is named in the timeline's note."""
    if shape.absent:
        return f"{evidence_id}: no payload"
    if shape.carrier:
        return f"{evidence_id}: `{shape.carrier}` empty"
    return f"{evidence_id}: keys {', '.join(shape.keys) or 'none'}"
