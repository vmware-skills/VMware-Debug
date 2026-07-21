"""Debug's tool annotations must match its read-only, network-free nature.

Recovered from ``test_read_only_mode.py`` (retired with the read-only feature in
v1.8.7). The read-only gate is gone, but these are NOT gate tests — they pin that
every tool is ``[READ]``, declares the read-only MCP hints its docstring promises,
and sets ``openWorldHint=False`` (it correlates dicts the caller already fetched
and opens no connection of its own). vmware-debug registers tools through a
``build_server()`` factory that could easily drift these hints, and the family's
usual ``openWorldHint=True`` would contradict both tool docstrings — so they are
pinned here.
"""

import asyncio

from vmware_debug.mcp_server import server as server_module


def _tools():
    return asyncio.run(server_module.build_server().list_tools())


def test_no_tool_is_marked_write():
    """This skill has no write tools."""
    for tool in _tools():
        assert (tool.description or "").lstrip().startswith("[READ]"), tool.name


def test_every_tool_declares_read_only_annotations():
    """Annotations must agree with the ``[READ]`` docstring marker — they drive
    MCP client UI, and a hint contradicting the marker would mislead clients."""
    for tool in _tools():
        annotations = getattr(tool, "annotations", None)
        assert annotations is not None, f"{tool.name} carries no annotations"
        assert annotations.readOnlyHint is True, tool.name
        assert annotations.destructiveHint is False, tool.name
        assert annotations.idempotentHint is True, tool.name


def test_open_world_hint_is_false_because_nothing_reaches_a_network():
    """This skill correlates dicts it was handed; it opens no connection."""
    for tool in _tools():
        assert tool.annotations.openWorldHint is False, tool.name
