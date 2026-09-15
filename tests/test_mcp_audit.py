"""Every call to this skill's MCP tools — and every failed call — writes one audit row.

Until 2026-09-15 no vmware-debug tool wrote to ``~/.vmware/audit.db``. The server
said that was deliberate ("nothing in this skill acts on a VMware target"), and
the case ledger was offered as the record. It is a record of the investigation,
not of the calls: a live session submitted evidence with invented timestamps,
and nothing could show when the calls had really been made or which had failed.
HLD §8.1 / I-10 (amended 2026-09-15b) now require a row for every MCP call,
local tools included, and for the CLI twins of those tools under the same name.

Rows are read back from the sandbox database the engine is bound to, so these
tests would fail if the call reached the audit engine but not the file.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner
from vmware_policy import get_engine
from vmware_policy.audit import reset_engine

from vmware_debug.cli import app
from vmware_debug.mcp_server.server import build_server

EXPECTED_TOOLS = {
    "incident_timeline",
    "list_symptom_categories",
    "case_open",
    "case_list",
    "case_get",
    "case_submit_evidence",
    "case_record_gap",
    "case_grade",
    "case_readiness",
    "case_plan",
    "case_hypotheses",
    "case_timeline",
    "case_close",
    "case_knowledge",
}

AT = "2026-09-15T05:48:16Z"


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path, monkeypatch):
    """A fresh OPS_HOME per test, and an engine bound to it."""
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def server():
    return build_server()


def rows(tool: str | None = None) -> list[dict]:
    db = Path(get_engine()._path)
    if not db.exists():
        return []
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        query = "SELECT skill, tool, status, params, result FROM audit_log"
        args: tuple = ()
        if tool is not None:
            query += " WHERE tool = ?"
            args = (tool,)
        return [dict(r) for r in con.execute(query + " ORDER BY id", args)]


def call(server, name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


def test_every_registered_tool_wears_vmware_tool(server) -> None:
    tools = server._tool_manager._tools
    assert set(tools) >= EXPECTED_TOOLS, f"tools missing from the registry: {EXPECTED_TOOLS - set(tools)}"
    unwrapped = sorted(name for name, tool in tools.items() if not getattr(tool.fn, "_is_vmware_tool", False))
    assert not unwrapped, f"MCP tools that write no audit row: {unwrapped}"


def test_a_successful_call_writes_one_ok_row_under_the_tool_name(server) -> None:
    call(server, "case_open", {"summary": "vsan latency on cluster-01", "determined_by": "alarm 42"})

    found = rows("case_open")
    assert len(found) == 1, f"expected one case_open row, got {rows()}"
    assert found[0]["skill"] == "debug"
    assert found[0]["status"] == "ok"


def test_a_failed_call_writes_an_error_row(server) -> None:
    call(server, "case_get", {"case_id": "20990101-000000-no-such-case"})

    found = rows("case_get")
    assert len(found) == 1, f"a failed call left no row: {rows()}"
    assert found[0]["status"] == "error"


def test_a_malformed_incident_timeline_call_is_recorded_as_an_error(server) -> None:
    call(server, "incident_timeline", {"events": [{"not": "an event"}]})

    found = rows("incident_timeline")
    assert len(found) == 1
    assert found[0]["status"] == "error"


def test_evidence_payload_is_not_copied_into_the_audit_row(server) -> None:
    opened = call(server, "case_open", {"summary": "vcenter memory exhaustion", "determined_by": "alarm"})
    case_id = _structured(opened)["case_id"]
    marker = "PAYLOAD-MARKER-3f9c"

    call(
        server,
        "case_submit_evidence",
        {
            "case_id": case_id,
            "source_skill": "vmware-monitor",
            "source_tool": "get_events",
            "summary": "one event",
            "fetched_at": AT,
            "query": {"target": "home-vcenter", "hours": 1},
            "payload": [{"time": AT, "message": marker}],
        },
    )

    found = rows("case_submit_evidence")
    assert len(found) == 1
    assert found[0]["status"] == "ok"
    assert marker not in found[0]["params"], "the raw payload was duplicated into audit.db"
    params = json.loads(found[0]["params"])
    assert params["case_id"] == case_id
    assert params["source_tool"] == "get_events"
    assert params["fetched_at"] == AT


def test_cli_categories_is_audited_under_its_mcp_twin_name() -> None:
    result = CliRunner().invoke(app, ["categories"])

    assert result.exit_code == 0, result.output
    found = rows("list_symptom_categories")
    assert len(found) == 1 and found[0]["status"] == "ok", rows()


def test_cli_triage_is_audited_under_its_mcp_twin_name(tmp_path) -> None:
    events = tmp_path / "events.json"
    events.write_text(
        json.dumps([{"ts": AT, "source": "vcenter", "severity": "warning", "entity": "vcsa", "text": "memory"}]),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["triage", "--events", str(events)])

    assert result.exit_code == 0, result.output
    found = rows("incident_timeline")
    assert len(found) == 1 and found[0]["status"] == "ok", rows()


def test_a_failed_cli_triage_is_recorded_as_an_error(tmp_path) -> None:
    bad = tmp_path / "events.json"
    bad.write_text("{not json", encoding="utf-8")

    result = CliRunner().invoke(app, ["triage", "--events", str(bad)])

    assert result.exit_code != 0
    found = rows("incident_timeline")
    assert len(found) == 1, f"a failed triage left no row: {rows()}"
    assert found[0]["status"] == "error"


def test_version_writes_no_row() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert rows() == []


def _structured(result) -> dict:
    """FastMCP returns (content, structured) or a content list depending on version."""
    if isinstance(result, tuple):
        content, structured = result
        if isinstance(structured, dict):
            return structured.get("result", structured)
        result = content
    return json.loads("".join(getattr(c, "text", "") for c in result))
