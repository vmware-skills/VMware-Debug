"""vmware-debug MCP server entry point.

Tools are defined in vmware_debug.mcp.tools (so audit logs see skill=debug).
This module wires them into a FastMCP server and provides the stdio entry point.

Note: signatures here use typing.Optional, never PEP 604 ``X | None`` — FastMCP
reflects these at registration and ``X | None`` crashes on Python 3.10 + older
mcp/pydantic (CLAUDE.md 踩坑 #33).
"""

import logging
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP
from vmware_policy import apply_read_only_gate, sanitize, set_environment_resolver

from vmware_debug.mcp import tools as t

logger = logging.getLogger("mcp_server")

#: Names withheld by the most recent :func:`build_server` call. The gate runs
#: inside the factory (this server has no module-level instance), so the result
#: is recorded here for startup logging and tests.
WITHHELD_WRITE_TOOLS: list[str] = []


# ---------------------------------------------------------------------------
# Environment declaration
# ---------------------------------------------------------------------------

#: What this skill reports as the environment of everything it touches.
#:
#: Policy rules scope by environment, and the baseline treats a target that
#: declares none as unknown — today that warns on state-changing operations,
#: and the next major release refuses them. Every other skill answers this from
#: its own config, where an operator labels each target ``production`` /
#: ``staging`` / ``lab``.
#:
#: vmware-debug has no such config and no connection to declare one about: its
#: tools are pure correlation over event dicts the calling agent has already
#: fetched with other skills' read tools. There is no network access, no
#: writes, and in fact no @vmware_tool-decorated operation above read risk — so
#: nothing here is gated under either setting today. Requiring a declaration it
#: has no place to make would leave it permanently blocked for no gain.
#:
#: The constant is registered anyway so that the answer is explicit and stays
#: true if this skill ever grows a tool that writes to a local store.
LOCAL_ENVIRONMENT = "local"

#: Client-facing behaviour hints, matching the rest of the family. Both tools
#: are [READ]: pure correlation over dicts the caller already fetched, with the
#: same answer every time. These drive MCP client UI (e.g. whether a call needs
#: a confirmation prompt); the read-only gate classifies independently, from
#: the [READ]/[WRITE] docstring marker.
#:
#: ``openWorldHint`` is False rather than the family's usual True: this skill
#: has no network access at all, which is exactly the closed world the hint
#: describes. Copying True would contradict both docstrings below.
_READ = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


#: Recovery step attached to every incident_timeline failure. Hoisted to a
#: constant so the wording stays in one place rather than drifting per call site.
_TIMELINE_ERROR_HINT = (
    "incident_timeline correlates events you have already fetched with other "
    "skills' read tools — it fetches nothing itself, so a rejected event has to be "
    "corrected in the 'events' argument you pass. 'error' names the offending "
    "entry and the expected form; fix it and call incident_timeline again. Run "
    "list_symptom_categories if you need to know which skill to pull events from."
)

#: Exception types this skill raises on purpose, whose text it authors and
#: therefore trusts to reach the agent verbatim. ``ValueError`` is the whole
#: list because it is the whole vocabulary: every rejection here comes from
#: ``envelope.py`` or ``ops/timeline.py`` refusing a malformed event, and each
#: one names the offending entry and the expected form.
#:
#: ``RuntimeError`` is deliberately absent. It is Python's generic catch-all, so
#: allowing it through would pass any library's raw text as if this skill had
#: written it.
_TEACHING_ERRORS = (ValueError,)


def _safe_error(exc: Exception, tool: str) -> str:
    """Return an agent-safe error string; log full detail server-side only.

    This skill fetches nothing, so its own messages are safe by construction —
    but the events it is handed came from other skills' read tools, and an
    unplanned exception raised while walking them can quote whatever they
    contain. A vCenter task URL with credentials in it is a value this tool can
    be handed; it is not a value it should hand back. Full traceback goes to the
    server log, and the agent sees only a control-char-stripped, length-capped
    message.

    500 rather than the family's usual 300: these messages interpolate a repr of
    the rejected event before reaching the remedy, and a modest four-field event
    already puts the sentence at ~425 characters. Capping at 300 would reliably
    truncate the one part the model needs.
    """
    logger.error("Tool %s failed", tool, exc_info=True)
    if isinstance(exc, _TEACHING_ERRORS):
        return sanitize(str(exc), 500)
    return f"{type(exc).__name__}: operation failed."


def _environment_for(target: Optional[str]) -> str:
    """Report the environment for policy scoping. Always ``local`` — see above."""
    return LOCAL_ENVIRONMENT


# Registered at import time rather than inside build_server(): the resolver is
# process-global state in vmware_policy, not per-server-instance, and every
# build_server() call would otherwise re-register the same constant.
set_environment_resolver(_environment_for)


def build_server() -> FastMCP:
    """Construct and configure the MCP server."""
    server = FastMCP("vmware-debug")

    @server.tool(name="incident_timeline", annotations=_READ)
    def _incident_timeline_impl(
        events: list[dict],
        bin_seconds: Optional[float] = None,
        z_threshold: float = 2.0,
        top_n: int = 5,
    ) -> dict:
        """[READ] Correlate already-fetched VMware events into one incident view.

        WHEN: use this after you've pulled events for an incident from the
        data-source skills (vmware-monitor get_events/get_alarms, vmware-aria
        list_alerts/list_anomalies, vmware-log-insight log_search/log_aggregate,
        vmware-nsx) — feed them here to find what correlates and where to look
        next. Not sure which events to pull? Run list_symptom_categories
        first. This tool does NOT fetch anything itself.

        INPUT: events = event envelopes, each {ts, source, severity, entity,
        text, fields} (ts may be ISO-8601, epoch seconds or millis; severity
        is normalised). Optional: bin_seconds (time-bin width; auto if
        omitted), z_threshold (spike sensitivity, default 2.0), top_n (max
        hypotheses, default 5).

        RETURNS: {event_count, window, spikes (anomalous bins), hypotheses
        (ranked root-cause candidates, each with a suggested_check),
        next_checks (what to investigate next, including which skill/tool)}.

        GOTCHAS: read-only, stateless, no network — nothing is executed.
        Remediation routes to vmware-aiops (single fix) or vmware-pilot
        (multi-step). A malformed event returns {error, hint} naming the
        offending index."""
        try:
            return t.incident_timeline(events, bin_seconds, z_threshold, top_n)
        except Exception as exc:
            # Returned rather than raised, matching the rest of the family: the
            # caller gets a payload it can act on instead of a protocol fault,
            # and `hint` carries the recovery step the bare exception lacks.
            # No `items` key — a failed call must never read as an empty page.
            return {
                "error": _safe_error(exc, "incident_timeline"),
                "hint": _TIMELINE_ERROR_HINT,
            }

    @server.tool(name="list_symptom_categories", annotations=_READ)
    def _list_symptom_categories_impl() -> dict:
        """[READ] List the symptom categories vmware-debug recognises, each with
        example keywords and a suggested next check (which skill/tool to run).
        Takes no parameters. Use this when you don't yet know what to look
        at — it turns "something's wrong" into concrete investigation steps.
        Then gather the events those checks name and pass them to
        incident_timeline. Returns the family list envelope {items, returned,
        limit, total, truncated, hint}; each item is {category,
        example_keywords, suggested_check}. The routing table is a fixed
        constant, so truncated is always false and total exact —
        this is every category, not a page. Read-only; no network access."""
        return t.list_symptom_categories()

    # Applied after every tool above has registered and before the server is
    # handed out. The [READ]/[WRITE] docstring marker is what the gate reads
    # first, so the readOnlyHint annotations above inform client UI without
    # changing this classification; both tools are [READ] and nothing is
    # withheld. Wired anyway so that stays provable, and so the gate is already
    # in place the day this skill grows a write tool.
    global WITHHELD_WRITE_TOOLS  # noqa: PLW0603 — factory has no module instance
    WITHHELD_WRITE_TOOLS = apply_read_only_gate(
        server, "vmware-debug", config_flag=None
    )

    return server


def main() -> None:
    """Entry point for `vmware-debug-mcp` (stdio transport)."""
    # Floor is 3.10, matching `requires-python` and the other eleven skills.
    # This guard used to demand 3.11 on the grounds that FastMCP schema
    # reflection was unreliable on 3.10 (踩坑 #33). That was the symptom; the
    # cause was PEP 604 `X | None` in the server's own signatures, fixed by
    # converting them to `Optional[X]`. 3.10 was then verified end to end on
    # 2026-07-19 — every tool's schema built, zero failures, pydantic 2.13.4 —
    # so the stricter floor was rejecting a version that works.
    if sys.version_info < (3, 10):
        sys.exit(
            "vmware-debug-mcp requires Python >= 3.10 "
            f"(got {sys.version_info.major}.{sys.version_info.minor}). "
            "Reinstall on a newer interpreter: "
            "uv tool install --python 3.12 --force vmware-debug"
        )
    build_server().run()


if __name__ == "__main__":
    main()
