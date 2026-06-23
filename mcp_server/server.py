"""vmware-debug MCP server entry point.

Tools are defined in vmware_debug.mcp.tools (so audit logs see skill=debug).
This module wires them into a FastMCP server and provides the stdio entry point.

Note: signatures here use typing.Optional, never PEP 604 ``X | None`` — FastMCP
reflects these at registration and ``X | None`` crashes on Python 3.10 + older
mcp/pydantic (CLAUDE.md 踩坑 #33).
"""

import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from vmware_debug.mcp import tools as t


def build_server() -> FastMCP:
    """Construct and configure the MCP server."""
    server = FastMCP("vmware-debug")

    @server.tool(name="incident_timeline")
    def _incident_timeline_impl(
        events: list[dict],
        bin_seconds: Optional[float] = None,
        z_threshold: float = 2.0,
        top_n: int = 5,
    ) -> dict:
        """[READ] Correlate already-fetched VMware events into one incident view.

        WHEN: after you've pulled events for an incident from the data-source
        skills (vmware-monitor event_list/alarm_list, vmware-aria alerts/anomaly,
        vmware-log-insight log_search/log_aggregate, vmware-nsx) — feed them all
        here to find what correlates and where to look next. This tool does NOT
        fetch anything itself; it has no vCenter/network access.

        INPUT: events = list of event envelopes, each {ts, source, severity,
        entity, text, fields} (ts may be ISO-8601, epoch seconds, or millis;
        severity is normalised). Optional: bin_seconds (time-bin width; auto if
        omitted), z_threshold (spike sensitivity, default 2.0), top_n (max
        hypotheses, default 5).

        RETURNS: {event_count, window, spikes (anomalous time bins), hypotheses
        (ranked root-cause candidates, each with a suggested_check), next_checks
        (concrete ideas for what to investigate next, including which skill/tool
        to run)}.

        GOTCHAS: read-only and stateless — nothing is executed. Remediation is
        routed to vmware-aiops (single fix) or vmware-pilot (multi-step, gated).
        A malformed event raises ValueError naming its index."""
        return t.incident_timeline(events, bin_seconds, z_threshold, top_n)

    @server.tool(name="list_symptom_categories")
    def _list_symptom_categories_impl() -> list[dict]:
        """[READ] List the symptom categories vmware-debug recognises and, for
        each, example keywords and the suggested next check (which skill/tool to
        run). Takes no parameters. Use this when you don't yet know what to look
        at — it turns "something's wrong" into concrete investigation steps.
        Read-only; no network access."""
        return t.list_symptom_categories()

    return server


def main() -> None:
    """Entry point for `vmware-debug-mcp` (stdio transport)."""
    if sys.version_info < (3, 11):
        sys.exit(
            "vmware-debug-mcp requires Python >= 3.11 (FastMCP schema reflection "
            "is unreliable on 3.10). Reinstall under 3.11+: "
            "uv tool install --python 3.11 vmware-debug"
        )
    build_server().run()


if __name__ == "__main__":
    main()
