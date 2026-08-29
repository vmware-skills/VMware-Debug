"""The case ledger's value types.

All frozen. A case record is evidence about what happened during an
investigation, and evidence that can be edited in place is not evidence — every
change goes through the store as a new write with its own timestamp, so the
history stays reconstructable from the directory alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The states of the eight-step loop, in order. Exported so the state machine,
#: the tools and the tests all read the one definition instead of re-declaring
#: it (the family has been bitten by hand-copied tuples going stale).
STATES: tuple[str, ...] = (
    "open",  # 01 the event is defined
    "collecting",  # 02/03 evidence is being gathered and checked
    "analyzing",  # 04/05 compression, ordering, timeline
    "hypothesis",  # 06 the hypothesis ledger is live
    "grading",  # 07/08 knowledge check and grading
    "closed",  # archived, indexed, distilled
)

#: Conclusion grades, weakest first. Order is meaningful: it is what lets the
#: grader say "this went down" without a second table to keep in sync.
GRADES: tuple[str, ...] = ("candidate", "probable", "confirmed", "excluded")


def _clean(text: str, field_name: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            f"Scope field {field_name!r} is required and cannot be blank. "
            f"It is what step 01 of the evidence loop produces; a case that "
            f"cannot state it is not scoped yet."
        )
    return text.strip()


@dataclass(frozen=True)
class Scope:
    """Step 01: what is being investigated, and how that was decided.

    ``determined_by`` is not documentation. A scope arrived at from a user's
    phone call and a scope arrived at from an alarm id support very different
    conclusions later, and by the time anyone asks, nobody remembers which it
    was.
    """

    summary: str
    objects: tuple[str, ...] = ()
    window_start: str | None = None
    window_end: str | None = None
    product_versions: dict[str, str] = field(default_factory=dict)
    determined_by: str = ""

    def __post_init__(self) -> None:
        _clean(self.summary, "summary")
        _clean(self.determined_by, "determined_by")

    def to_json(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "objects": list(self.objects),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "product_versions": dict(self.product_versions),
            "determined_by": self.determined_by,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Scope":
        return Scope(
            summary=d.get("summary", ""),
            objects=tuple(d.get("objects") or ()),
            window_start=d.get("window_start"),
            window_end=d.get("window_end"),
            product_versions=dict(d.get("product_versions") or {}),
            determined_by=d.get("determined_by", ""),
        )


@dataclass(frozen=True)
class Case:
    """A loaded case: its identity, its scope, and where it is in the loop."""

    case_id: str
    scope: Scope
    state: str
    opened_at: str
    grade: str | None = None
