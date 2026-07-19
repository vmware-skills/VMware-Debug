"""Regression evals for vmware-debug. Failures block release.

Covers the disciplines from CLAUDE.md 踩坑 #33/#34 (MCP schema build + tool
exposure) and keeps the symptom-routing catalogue in sync with the playbook docs.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vmware_debug.mcp_server.server import build_server
from vmware_debug.envelope import Event
from vmware_debug.ops.timeline import category_routing, incident_timeline, known_categories

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_mcp_exposes_expected_tools():
    """踩坑 #34: the CLI/advertised tools must actually be exposed over MCP, and
    #33: FastMCP must build their schemas without raising."""
    server = build_server()
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"incident_timeline", "list_symptom_categories"} <= names


def test_incident_timeline_output_is_json_serializable():
    out = incident_timeline(
        [
            Event(
                ts=1000, source="storage", severity="critical", entity="ds1",
                text="vsan no space apd",
            )
        ]
    )
    # Round-trips through JSON unchanged in structure (MCP returns JSON).
    assert json.loads(json.dumps(out))["hypotheses"][0]["category"] == "storage"


def test_routing_catalogue_matches_known_categories():
    assert [c["category"] for c in category_routing()] == known_categories()


def test_every_category_documented_in_routing_md():
    routing_md = (
        REPO_ROOT / "skills" / "vmware-debug" / "references" / "routing.md"
    ).read_text(encoding="utf-8")
    for category in known_categories():
        assert category in routing_md, f"{category} missing from routing.md"
