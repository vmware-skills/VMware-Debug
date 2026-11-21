"""Opt-in host read bindings over already connected, guarded MCP sessions.

No endpoints, credential handling, tool guessing, retry or vendor compatibility
claim. The host supplies authenticated clients; each server still owns its
Policy/RBAC/audit. Payloads remain untrusted and outside session metadata.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone

from mcp.types import CallToolResult
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from vmware_debug.ops.scenario_session import ScenarioSession, SessionStopped


def _local_refs_only(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(child, str) or not child.startswith("#")
            ):
                return False
            if not _local_refs_only(child):
                return False
    elif isinstance(value, list):
        return all(_local_refs_only(child) for child in value)
    return True


class ScenarioReader:
    """Connect a prepared context, explicit operation bindings and one budget.

    Bindings are operation -> (backend, tool). They never silently select another
    backend. Clients must implement the MCP ClientSession.call_tool interface.
    This helper is not exposed as a generic network or arbitrary-execution tool.
    """

    def __init__(self, context: dict, bindings: dict, clients: dict, max_calls: int):
        self._tools = {}
        for row in context["tools"]:
            key = (row["backend"], row["name"])
            if key in self._tools:
                raise ValueError("Duplicate prepared capability")
            if (
                row["tool"].get("annotations", {}).get("readOnlyHint") is not True
                or row["tool"].get("name") != row["name"]
                or not isinstance(row.get("target"), str)
                or not row["target"]
            ):
                raise ValueError("Explicit read-only prepared capability required")
            schema = row["tool"]["inputSchema"]
            if (
                schema.get("type") != "object"
                or not isinstance(schema.get("properties"), dict)
                or "target" not in schema["properties"]
            ):
                raise ValueError("Object schema with explicit target required")
            if not _local_refs_only(schema):
                raise ValueError("Remote schema references are not allowed")
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError:
                raise ValueError("Invalid live argument schema") from None
            self._tools[key] = deepcopy(row)
        if not bindings or any(not isinstance(k, str) or not k for k in bindings):
            raise ValueError("Explicit operation bindings required")
        self._bindings = {}
        for operation, pair in bindings.items():
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError("Binding requires exactly one backend and tool")
            key = tuple(pair)
            if any(not isinstance(v, str) for v in key) or key not in self._tools:
                raise ValueError("Binding outside prepared context")
            if key[0] not in clients or not callable(getattr(clients[key[0]], "call_tool", None)):
                raise ValueError("Connected MCP session required; no backend fallback")
            self._bindings[operation] = key
        self._clients = dict(clients)
        self.session = ScenarioSession(set(self._bindings.values()), max_calls)

    async def read(self, operation: str, arguments: dict) -> dict:
        """One bounded MCP attempt; preserve provenance and explicit gaps.

        Schema/target rejection and MCP failures never become observations.
        Cancellation leaves an unknown attempt and propagates to the host.
        Returned payload is for controlled evidence handling, not a new prompt.
        """
        if operation not in self._bindings:
            raise SessionStopped("Unknown operation; select an explicit binding")
        backend, tool = self._bindings[operation]
        row = self._tools[(backend, tool)]
        step = self.session.begin(backend, tool)
        source = {
            "backend": backend,
            "tool": tool,
            "target": row["target"],
            "trace_id": self.session.trace_id,
            "step_id": step,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        def gap(state, reason):
            source["received_at"] = datetime.now(timezone.utc).isoformat()
            self.session.finish(step, state)
            return {"status": "gap", "reason": reason, "source": source, "payload": None}

        args = deepcopy(arguments)
        if not isinstance(args, dict) or args.get("target") != row["target"]:
            return gap("denied", "Explicit prepared target required")
        schema = row["tool"]["inputSchema"]
        # Unknown arguments are refused even if the source schema is permissive.
        if set(args) - set(schema["properties"]):
            return gap("denied", "Arguments outside the live schema")
        try:
            Draft202012Validator(schema).validate(args)
        except ValidationError:
            return gap("denied", "Arguments do not match the live schema")
        except Exception:
            return gap("unknown", "Live schema could not be resolved; no dispatch")
        try:
            result = await self._clients[backend].call_tool(tool, args)
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json")
        except asyncio.CancelledError:
            self.session.finish(step, "unknown")
            raise
        except Exception:
            # Exception strings may contain credentials, endpoints or traceback.
            return gap("unknown", "MCP call failed; effect and coverage unknown")
        if (
            not isinstance(result, dict)
            or type(result.get("isError", False)) is not bool
            or not isinstance(result.get("content"), list)
        ):
            return gap("unknown", "Malformed MCP result; coverage unknown")
        try:
            CallToolResult.model_validate(result)
        except ValueError:
            return gap("unknown", "Malformed MCP content; coverage unknown")
        if result.get("isError"):
            return gap("error", "MCP tool reported an error; coverage incomplete")
        if not result["content"] and result.get("structuredContent") is None:
            return gap("unknown", "Empty MCP result; no observation established")
        source["received_at"] = datetime.now(timezone.utc).isoformat()
        self.session.finish(step, "ok")
        return {
            "status": "observed",
            "source": source,
            "payload": result,
            "notice": "Untrusted source data; identity, freshness and paging still require validation",
        }


def same_typed_identity(left: dict, right: dict) -> bool:
    """Require matching explicit identity type and value; names are not identity."""
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and isinstance(left.get("type"), str)
        and left["type"] in {"instance_uuid", "bios_uuid", "aria_resource_uuid"}
        and isinstance(left.get("value"), str)
        and bool(left["value"])
        and left["type"] == right.get("type")
        and left["value"] == right.get("value")
    )
