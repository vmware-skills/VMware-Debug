"""Debug's tool annotations must match what each tool actually does.

Recovered from ``test_read_only_mode.py`` (retired with the read-only feature in
v1.8.7). The read-only gate is gone, but these are NOT gate tests — they pin
that every tool's MCP hints agree with the ``[READ]``/``[WRITE]`` marker its
docstring promises, since those hints drive client UI and a factory-built server
can drift them silently.

**What changed, and what did not.** Until the investigation ledger landed, every
tool here was ``[READ]`` and a test said so. The ledger tools write — to a
directory on this machine. The property worth guarding was never "debug writes
nothing"; it was **"debug reaches no network and touches no VMware system"**,
and that is still exactly true. So the network assertion below is unchanged and
applies to every tool, while the read/write assertions now follow the marker
instead of assuming one answer for the whole skill.
"""

import asyncio

from vmware_debug.mcp_server import server as server_module

#: Tools that write to the investigation ledger under $OPS_HOME. Listed rather
#: than derived from the marker, so that a tool silently gaining a ``[WRITE]``
#: docstring shows up here as a failure and has to be added deliberately.
_LEDGER_WRITERS = {
    "case_open",
    "case_submit_evidence",
    "case_record_gap",
    "case_grade",
    "case_hypotheses",
    "case_timeline",
    "case_close",
}


def _tools():
    return asyncio.run(server_module.build_server().list_tools())


def test_every_tool_declares_read_or_write_and_the_write_set_is_the_expected_one():
    marked_write = set()
    for tool in _tools():
        desc = (tool.description or "").lstrip()
        assert desc.startswith("[READ]") or desc.startswith("[WRITE]"), tool.name
        if desc.startswith("[WRITE]"):
            marked_write.add(tool.name)
    assert marked_write == _LEDGER_WRITERS, (
        f"the set of write tools changed: {marked_write ^ _LEDGER_WRITERS}. "
        f"Every write here must be to the local ledger only — if a tool now "
        f"writes to a VMware system, that is a change of what this skill is, "
        f"not a list to update."
    )


def test_annotations_agree_with_the_docstring_marker():
    """A hint contradicting the marker misleads every MCP client."""
    for tool in _tools():
        ann = getattr(tool, "annotations", None)
        assert ann is not None, f"{tool.name} carries no annotations"
        is_write = (tool.description or "").lstrip().startswith("[WRITE]")
        assert ann.readOnlyHint is not is_write, tool.name
        # Nothing here is destructive in either direction: the read tools cannot
        # be, and the ledger is append-only — opening a case refuses to
        # overwrite one, evidence lands in its own file, and a grade is appended
        # to the history rather than replacing it.
        assert ann.destructiveHint is False, tool.name


def test_read_tools_are_idempotent_and_ledger_writes_are_not():
    """Submitting the same evidence twice records it twice, and should: two
    fetches of one query at different times are two observations."""
    for tool in _tools():
        is_write = (tool.description or "").lstrip().startswith("[WRITE]")
        assert tool.annotations.idempotentHint is not is_write, tool.name


def test_open_world_hint_is_false_because_nothing_reaches_a_network():
    """The assertion that did not change. This skill correlates dicts it was
    handed and reads and writes a local folder; it opens no connection, so no
    tool may claim an open world."""
    for tool in _tools():
        assert tool.annotations.openWorldHint is False, tool.name
