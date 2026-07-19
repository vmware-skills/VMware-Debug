"""Read-only mode must be a no-op here — and that is worth pinning.

Regression source: VMware-AIops issue #31 (juanpf-ha). An operator driving the
family with a local Llama 3.3 70B had to hand-write the prompt instruction
"work exclusively in read-only mode and never modify alerts, definitions,
reports or configuration", because read-only was only ever a documented
intent. A weak model can ignore a prompt; it cannot call a tool that is not in
list_tools().

vmware-debug is the awkward case for the gate. Its tools are registered by a
``build_server()`` factory that passes **no MCP annotations at all**, so
``readOnlyHint`` is None for every tool and the gate has only the
``[READ]``/``[WRITE]`` docstring marker to classify on. That marker is exactly
why nothing is withheld here — the gate's fallback for an unclassifiable tool
is "treat as write and remove it", so a dropped marker would silently gut this
server. These tests pin that it does not happen.

Both tools are stateless local analysis over events the caller already
fetched; this skill has no vCenter or network access of its own.
"""

import asyncio

import pytest
from vmware_policy import apply_read_only_gate

from mcp_server import server as server_module

EXPECTED_TOOLS = {"incident_timeline", "list_symptom_categories"}


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("VMWARE_READ_ONLY", raising=False)
    monkeypatch.delenv("VMWARE_DEBUG_READ_ONLY", raising=False)


def _tools(server):
    return asyncio.run(server.list_tools())


def _tool_names(server):
    return {t.name for t in _tools(server)}


def test_no_tool_is_marked_write():
    """The premise: this skill has no write tools to withhold."""
    for tool in _tools(server_module.build_server()):
        description = (tool.description or "").lstrip()
        assert description.startswith("[READ]"), tool.name


def test_every_tool_declares_read_only_annotations():
    """Annotations now exist, and they must agree with the docstring marker.

    They were added for MCP client UI, which reads the hints rather than the
    docstring. The gate still classifies from the [READ]/[WRITE] marker (it is
    checked before readOnlyHint), so these hints change no safety decision —
    but a hint that contradicted the marker would mislead every client, so the
    two are pinned together here.
    """
    for tool in _tools(server_module.build_server()):
        annotations = getattr(tool, "annotations", None)
        assert annotations is not None, f"{tool.name} carries no annotations"
        assert annotations.readOnlyHint is True, tool.name
        assert annotations.destructiveHint is False, tool.name
        assert annotations.idempotentHint is True, tool.name


def test_open_world_hint_is_false_because_nothing_reaches_a_network():
    """This skill correlates dicts it was handed; it opens no connection.

    Copying the family's usual openWorldHint=True would contradict both tool
    docstrings, which promise no network access.
    """
    for tool in _tools(server_module.build_server()):
        assert tool.annotations.openWorldHint is False, tool.name


def test_default_mode_exposes_every_tool():
    server = server_module.build_server()
    assert _tool_names(server) == EXPECTED_TOOLS
    assert server_module.WITHHELD_WRITE_TOOLS == []


def test_read_only_withholds_nothing(monkeypatch):
    """Read-only mode must not cost this skill any capability."""
    monkeypatch.setenv("VMWARE_READ_ONLY", "true")
    server_module.build_server()
    assert server_module.WITHHELD_WRITE_TOOLS == []


def test_read_only_keeps_every_tool(monkeypatch):
    """Every tool survives — the whole point of testing a read-only skill."""
    monkeypatch.setenv("VMWARE_READ_ONLY", "true")
    assert _tool_names(server_module.build_server()) == EXPECTED_TOOLS


def test_skill_env_var_also_withholds_nothing(monkeypatch):
    monkeypatch.setenv("VMWARE_DEBUG_READ_ONLY", "true")
    server = server_module.build_server()
    assert server_module.WITHHELD_WRITE_TOOLS == []
    assert _tool_names(server) == EXPECTED_TOOLS


def test_gate_is_live_not_a_no_op(monkeypatch):
    """An empty withheld list must mean "no write tools", not "gate never ran".

    Every other assertion in this file is satisfied just as well by a gate that
    was never wired in. Register a tool this skill does not have, marked
    [WRITE], and prove the gate removes it under the same env and skill name
    build_server() uses.
    """
    monkeypatch.setenv("VMWARE_READ_ONLY", "true")
    server = server_module.build_server()
    assert server_module.WITHHELD_WRITE_TOOLS == []

    @server.tool(name="_probe_write")
    def _probe() -> str:
        """[WRITE] Probe tool — must not survive the gate."""
        return "probe"

    assert apply_read_only_gate(server, "vmware-debug") == ["_probe_write"]
    assert _tool_names(server) == EXPECTED_TOOLS


def test_fastmcp_registry_api_still_present():
    """The gate reaches into _tool_manager.list_tools(); pin that it exists.

    If an mcp upgrade moves this, we want a red test here rather than a gate
    that silently stops removing anything.
    """
    server = server_module.build_server()
    assert callable(getattr(server, "remove_tool", None))
    assert callable(getattr(server._tool_manager, "list_tools", None))
    assert server._tool_manager.list_tools()


def test_build_server_actually_applies_the_gate(monkeypatch):
    """The gate must be CALLED by the factory, not merely importable.

    This repo shipped for a while with `apply_read_only_gate` imported and never
    called. Every other assertion here still passed — "withholds nothing" is
    trivially true of a gate that never runs — and a grep for the symbol found
    the unused import. Only observing the factory invoke it closes that gap.
    """
    import mcp_server.server as server

    calls = []
    real = server.apply_read_only_gate

    def spy(instance, skill, config_flag=None):
        calls.append(skill)
        return real(instance, skill, config_flag=config_flag)

    monkeypatch.setattr(server, "apply_read_only_gate", spy)
    server.build_server()
    assert calls == ["vmware-debug"], "build_server() must apply the read-only gate"
