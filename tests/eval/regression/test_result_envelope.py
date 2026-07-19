"""Regression: list tools state their completeness instead of implying it.

Source: VMware-AIops issue #31 (juanpf-ha). Running the family against a local
Llama 3.3 70B, the operator reported that "with long tool responses, it may
omit existing information or incorrectly state that no data was returned."

A bare ``list[dict]`` gives a model nothing to distinguish a complete answer
from page one, so it guesses — and a guess that reads "no data" looks like a
finding. ``list_symptom_categories`` now returns the family envelope from
``vmware_policy.paginated``, so ``returned``/``total``/``truncated`` are stated.

This tool has no ``limit`` parameter, and that is precisely why it needs the
envelope: ``truncated: false`` is the information the model would otherwise
have to guess at. The routing table is a fixed in-process constant, so the
total is real and the answer is always the whole set.
"""

from __future__ import annotations

from vmware_debug.mcp.tools import list_symptom_categories
from vmware_debug.ops.timeline import category_routing

ENVELOPE_KEYS = {"items", "returned", "limit", "total", "truncated", "hint"}


def test_envelope_carries_every_key():
    """Explicit nulls, never missing keys — a missing key invites invention."""
    assert ENVELOPE_KEYS <= set(list_symptom_categories())


def test_items_are_the_routing_catalogue_unchanged():
    """The envelope wraps the rows; it must not filter or reorder them."""
    assert list_symptom_categories()["items"] == category_routing()


def test_returned_counts_the_items():
    out = list_symptom_categories()
    assert out["returned"] == len(out["items"])
    assert out["returned"] > 0


def test_total_is_real_not_fabricated():
    """The catalogue is a constant, so its full size is known for free."""
    assert list_symptom_categories()["total"] == len(category_routing())


def test_unlimited_tool_reports_no_limit():
    assert list_symptom_categories()["limit"] is None


def test_result_is_never_truncated_and_says_so():
    """The whole point for a tool with no limit: complete is stated, not implied."""
    out = list_symptom_categories()
    assert out["truncated"] is False
    assert out["hint"] is None
