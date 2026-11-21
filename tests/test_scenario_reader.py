"""Host dispatch controls, including real in-memory MCP protocol traffic."""

import asyncio
import copy

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp.shared.memory import create_connected_server_and_client_session

from vmware_debug.ops.scenario_context import prepare_context
from vmware_debug.ops.scenario_reader import ScenarioReader, same_typed_identity
from vmware_debug.ops.scenario_session import SessionStopped


def context():
    tool = {
        "name": "read_state",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["target"],
        },
    }
    return prepare_context(
        {
            "id": "probe",
            "procedure": "probe.md",
            "tools": {"monitor": ["read_state"], "aria": ["read_state"]},
        },
        {b: {"read_state": copy.deepcopy(tool)} for b in ("monitor", "aria")},
        {b: ["read_state"] for b in ("monitor", "aria")},
        {b: "lab" for b in ("monitor", "aria")},
        {b: ["lab"] for b in ("monitor", "aria")},
    )


class Client:
    def __init__(self, result=None, error=False):
        self.result = result
        self.error = error
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise RuntimeError("password=must-not-leak")
        return self.result


def reader(client, max_calls=1):
    return ScenarioReader(
        context(),
        {"inventory": ("monitor", "read_state"), "metrics": ("aria", "read_state")},
        {"monitor": client, "aria": client},
        max_calls,
    )


@pytest.mark.parametrize(
    "arguments",
    [{}, {"target": "other"}, {"target": "lab", "limit": 0}, {"target": "lab", "unknown": 1}],
)
def test_invalid_request_never_dispatches_and_consumes_attempt(arguments):
    c = Client()
    r = reader(c)
    assert asyncio.run(r.read("inventory", arguments))["status"] == "gap"
    assert not c.calls
    with pytest.raises(SessionStopped):
        asyncio.run(r.read("metrics", {"target": "lab"}))


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        {"content": [123]},
        {"content": None},
        {"content": [], "isError": "false"},
        {"content": []},
        {"content": [], "isError": True},
    ],
)
def test_missing_malformed_or_denied_results_are_not_observations(result):
    r = reader(Client(result))
    out = asyncio.run(r.read("inventory", {"target": "lab"}))
    assert out["status"] == "gap" and out["payload"] is None
    assert r.session.snapshot()["attempted_calls"] == 1


def test_failure_unknown_and_cancelled_are_not_retried_or_refunded():
    c = Client(error=True)
    r = reader(c)
    out = asyncio.run(r.read("inventory", {"target": "lab"}))
    assert "must-not-leak" not in str(out) and len(c.calls) == 1
    assert r.session.snapshot()["steps"][0]["state"] == "unknown"

    class Cancelled(Client):
        async def call_tool(self, *args):
            raise asyncio.CancelledError()

    r = reader(Cancelled())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(r.read("inventory", {"target": "lab"}))
    assert r.session.snapshot()["steps"][0]["state"] == "unknown"


def test_explicit_binding_no_fallback_and_no_remote_schema_resolution():
    c = Client()
    with pytest.raises(ValueError):
        ScenarioReader(context(), {"read": ("official", "guess_tool")}, {"monitor": c}, 1)
    ctx = context()
    ctx["tools"][0]["tool"]["inputSchema"]["properties"]["target"]["$ref"] = (
        "https://invalid.example/schema"
    )
    with pytest.raises(ValueError, match="Remote schema"):
        ScenarioReader(ctx, {"read": ("monitor", "read_state")}, {"monitor": c}, 1)
    assert not c.calls


def test_typed_uuid_does_not_match_a_different_identity_type():
    assert same_typed_identity(
        {"type": "instance_uuid", "value": "123"}, {"type": "instance_uuid", "value": "123"}
    )
    assert not same_typed_identity(
        {"type": "bios_uuid", "value": "123"}, {"type": "instance_uuid", "value": "123"}
    )
    assert not same_typed_identity({"name": "same"}, {"name": "same"})
    assert not same_typed_identity(
        {"type": "name", "value": "same"}, {"type": "name", "value": "same"}
    )


def test_connected_mcp_clients_share_budget_and_preserve_provenance():
    async def run():
        server = FastMCP("synthetic")

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def read_state(target: str, limit: int = 1):
            return {"target": target, "synthetic": True, "limit": limit}

        async with create_connected_server_and_client_session(server._mcp_server) as client:
            r = reader(client, 2)
            one = await r.read("inventory", {"target": "lab"})
            two = await r.read("metrics", {"target": "lab", "limit": 2})
            assert one["status"] == two["status"] == "observed"
            assert one["source"]["backend"] != two["source"]["backend"]
            assert one["source"]["trace_id"] == two["source"]["trace_id"]
            assert one["source"]["started_at"] <= one["source"]["received_at"]
            with pytest.raises(SessionStopped):
                await r.read("inventory", {"target": "lab"})
            snap = r.session.snapshot()
            assert snap["attempted_calls"] == 2
            assert all(row["input_tokens"] is None for row in snap["steps"])
            assert "payload" not in str(snap) and "arguments" not in str(snap)

    asyncio.run(run())
