"""vmware-debug MCP tool logic — pure, read-only correlation. No network, no
writes, no cross-skill imports. The agent fetches events with the other skills'
read tools and passes them here as plain dicts (the unified event envelope)."""

from __future__ import annotations

from typing import Optional

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


def list_symptom_categories() -> list[dict]:
    """List the symptom categories debug recognises and what to check for each."""
    return category_routing()
