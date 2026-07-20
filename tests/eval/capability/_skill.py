"""The only repo-specific facts in this capability suite.

Every ``test_*.py`` file in this directory is identical across the family repos;
they differ only through this module. Keeping the difference in one small file is
what makes a rubric change portable — edit the eval once, copy it, and the scores
stay comparable between skills.
"""

from __future__ import annotations

#: Import path of the Python package under test.
PACKAGE = "vmware_debug"

#: Module holding the FastMCP server.
SERVER_MODULE = "vmware_debug.mcp_server.server"

#: CLI entry point name, used when scoring whether an error names something
#: concrete for the operator to run.
CLI_NAME = "vmware-debug"

#: Companion skills this one legitimately routes to. A required entity name that
#: this surface cannot produce is not a dead end *if* the description says which
#: sibling skill produces it — that is a documented hand-off rather than a gap.
COMPANION_SKILLS = (
    "vmware-aiops",
    "vmware-monitor",
    "vmware-storage",
    "vmware-vks",
    "vmware-nsx",
    "vmware-nsx-security",
    "vmware-aria",
    "vmware-avi",
    "vmware-harden",
    "vmware-pilot",
)

#: Entity tokens this skill's tools name, mapped to the words its listing tools
#: use. Authored from the registry's own required parameters, not from the
#: domain in the abstract. Drives ``test_entity_reachability``: a stem that is
#: not here is invisible to that eval, which is why the suite asserts coverage.
#: Deliberately empty: neither tool takes a discoverable identifier —
#: ``incident_timeline`` takes an event payload and
#: ``list_symptom_categories`` takes nothing. The suite asserts that an
#: empty result comes from an empty map rather than from a map that
#: simply failed to fit, so this emptiness has to be stated.
ENTITY_WORDS = {
}

#: Skill-specific parameters that end in an entity suffix but are supplied by the
#: operator rather than discovered from an API. Universal exclusions (``target``,
#: paths, filters) live in the eval itself.
NOT_AN_ENTITY: frozenset[str] = frozenset()

def get_server(module):
    """Return the FastMCP instance ``SERVER_MODULE`` exposes.

    This skill builds its server in a factory rather than at import time, so the
    read-only gate is applied per call. Declared here rather than probed with a
    try/except chain — a fallback would let a server that stops exposing what
    this file says silently resolve to the other shape.
    """
    return module.build_server()
