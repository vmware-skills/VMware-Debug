"""vmware-debug MCP tool logic — pure, read-only correlation. No network, no
writes, no cross-skill imports. The agent fetches events with the other skills'
read tools and passes them here as plain dicts (the unified event envelope)."""

from __future__ import annotations

from typing import Optional

from vmware_policy import paginated

from vmware_debug.envelope import normalize_events
from vmware_debug.ops.timeline import category_routing
from vmware_debug.ops.timeline import incident_timeline as _incident_timeline


def incident_timeline(
    events: list[dict],
    bin_seconds: Optional[float] = None,
    z_threshold: float = 2.0,
    top_n: int = 5,
) -> dict:
    """Correlate pre-fetched events into a timeline + spikes + ranked hypotheses.

    ``events`` is a list of event envelopes (see references/event-envelope.md).
    Raises ValueError (with the offending index) if an event can't be normalised.
    """
    normalized = normalize_events(events)
    return _incident_timeline(
        normalized, bin_seconds=bin_seconds, z_threshold=z_threshold, top_n=top_n
    )


def list_symptom_categories() -> dict:
    """List the symptom categories debug recognises and what to check for each.

    Returns the family list envelope; `items` holds the categories. The routing
    table is a fixed, in-process constant, so `total` is the real count and
    `truncated` is always False — there is no page two to go looking for.
    """
    categories = category_routing()
    return paginated(categories, total=len(categories))


# ── Investigation cases ───────────────────────────────────────────────────
# The bodies live in ops/cases/api.py so the behaviour stays testable without
# an MCP server. These are the names the tool registrations bind to.

from vmware_debug.ops.cases.api import (  # noqa: E402  (grouped with its section)
    add_gap as case_record_gap,
    get_case as case_get,
    grade as case_grade,
    list_open_cases as case_list,
    open_case as case_open,
    submit_evidence as case_submit_evidence,
)

__all__ = [
    "incident_timeline",
    "list_symptom_categories",
    "case_open",
    "case_get",
    "case_list",
    "case_submit_evidence",
    "case_record_gap",
    "case_grade",
]
