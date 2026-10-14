"""vmware-debug MCP tool logic. No network, no credentials, no cross-skill
imports. The agent fetches events with the other skills' read tools and passes
them here as plain dicts (the unified event envelope). The two correlation
tools are pure reads; the case tools read and write the local investigation
ledger under $OPS_HOME, and nothing else."""

from __future__ import annotations

from typing import Optional

from vmware_policy import paginated

from vmware_debug.envelope import normalize_events
from vmware_debug.ops.timeline import category_routing
from vmware_debug.ops.timeline import incident_timeline as _incident_timeline


def build_incident_timeline(
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
    add_gap as record_case_gap,
    close as close_case,
    check_readiness as get_case_readiness,
    get_case,
    grade as grade_case,
    hypotheses as record_case_hypothesis,
    knowledge as get_case_knowledge,
    list_open_cases as list_cases,
    open_case,
    plan as plan_case,
    submit_evidence as submit_case_evidence,
    timeline as record_case_timeline,
)

__all__ = [
    "build_incident_timeline",
    "list_symptom_categories",
    "open_case",
    "get_case",
    "list_cases",
    "submit_case_evidence",
    "record_case_gap",
    "grade_case",
    "get_case_readiness",
    "plan_case",
    "record_case_hypothesis",
    "record_case_timeline",
    "close_case",
    "get_case_knowledge",
]
