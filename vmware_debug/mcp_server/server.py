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
from vmware_policy import sanitize, set_environment_resolver

from vmware_debug.mcp import tools as t
from vmware_debug import __version__
from vmware_debug.ops.cases.store import CaseError

logger = logging.getLogger("mcp_server")


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
#: tools either correlate event dicts the calling agent already fetched, or
#: read and write the investigation ledger under $OPS_HOME. There is still no
#: network access and no VMware environment to be in, so ``local`` is the
#: honest answer rather than a placeholder.
#:
#: The ledger tools ARE writes, which the earlier version of this note said did
#: not exist. They are writes to a directory on this machine, at ``low`` risk:
#: nothing they touch can affect a vCenter, and the ledger is append-only. The
#: environment declaration is what keeps that distinction reviewable instead of
#: implicit.
LOCAL_ENVIRONMENT = "local"

#: Client-facing behaviour hints, matching the rest of the family. Both tools
#: are [READ]: pure correlation over dicts the caller already fetched, with the
#: same answer every time. These drive MCP client UI (e.g. whether a call needs
#: a confirmation prompt).
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

#: The case tools write to the local investigation ledger under $OPS_HOME.
#: Still ``openWorldHint: False`` — the boundary this skill does not cross is
#: the network, and these tools do not cross it either. ``destructiveHint`` is
#: False because the ledger is append-only: opening a case refuses to overwrite
#: one, evidence lands in its own file, and a grade is appended to the history
#: rather than replacing it. Nothing here has anything to undo.
#:
#: ``idempotentHint`` is False: submitting the same evidence twice records it
#: twice, which is correct — two fetches of the same query at different times
#: are two observations.
_WRITE_LOCAL = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
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

_CASE_ERROR_HINT = (
    "The case tools read and write the investigation ledger under $OPS_HOME "
    "(default ~/.vmware/cases) and touch no VMware system, so a failure here is "
    "about the case id, the arguments, or the folder itself — never about "
    "vCenter connectivity. 'error' names what is wrong and what to do; "
    "case_list shows the ids that exist. Note there is no 'items' key on a "
    "failure: a call that did not work must not read as an empty result."
)

#: Exception types this skill raises on purpose, whose text it authors and
#: therefore trusts to reach the agent verbatim.
#:
#: ``ValueError`` covers the event vocabulary: every rejection from
#: ``envelope.py`` or ``ops/timeline.py`` names the offending entry and the
#: expected form. ``CaseError`` covers the investigation ledger — a case that
#: does not exist, a directory that cannot be read, an evidence id claimed
#: twice. Those messages exist precisely to tell the caller what to do next
#: ("run case_list to see the ids that exist"), and reducing them to
#: "CaseNotFound: operation failed." would throw away the part that helps. Both
#: are bases, so their subclasses pass with them.
#:
#: ``RuntimeError`` is deliberately absent. It is Python's generic catch-all, so
#: allowing it through would pass any library's raw text as if this skill had
#: written it. Broad builtin bases are avoided for the same reason: widening to
#: ``OSError`` is what once swallowed nine repos' password-error guidance, which
#: is why the ledger raises ``EvidenceConflict`` rather than ``FileExistsError``.
_TEACHING_ERRORS = (ValueError, CaseError)


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

    # FastMCP takes no version argument and leaves the lowlevel server's at
    # None, which makes `initialize` answer with the MCP SDK's version rather
    # than ours. Set it so a client can tell which release it is talking to.
    server._mcp_server.version = __version__

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

    # ── Investigation cases ───────────────────────────────────────────────
    # The eight-step evidence loop's ledger. debug holds no credentials and
    # reaches no VMware environment: the agent fetches with the data-source
    # skills' read tools and submits the results here, which is what lets a
    # finished case be reopened and re-argued on a machine with access to
    # nothing.

    def _case_error(exc: Exception, tool: str) -> dict:
        """Return a failed call as a payload, never as an empty result."""
        return {"error": _safe_error(exc, tool), "hint": _CASE_ERROR_HINT}

    @server.tool(name="case_open", annotations=_WRITE_LOCAL)
    def _case_open_impl(
        summary: str,
        determined_by: str,
        objects: Optional[list[str]] = None,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        product_versions: Optional[dict] = None,
    ) -> dict:
        """[WRITE] Open an investigation case — step 01, define the event.

        WHEN: at the start of an incident you expect to reason about rather than
        glance at. For a one-off lookup use incident_timeline alone.

        INPUT: summary (what is wrong, one line); determined_by (HOW the scope
        was decided — "user report", "vCenter alarm 42" — required, because a
        scope from a phone call and one from an alarm id support different
        conclusions and nobody remembers which it was later); optional objects,
        window_start/window_end (ISO-8601), product_versions (used to check
        whether a knowledge-base entry actually applies).

        RETURNS: {case_id, path, state, grade, ceiling, ceiling_reasons, next}.
        Read `ceiling` now — it is the best grade this install can reach at all.

        GOTCHAS: writes only under $OPS_HOME, never to a VMware system, and
        never over an existing case."""
        try:
            return t.case_open(
                summary=summary,
                determined_by=determined_by,
                objects=objects,
                window_start=window_start,
                window_end=window_end,
                product_versions=product_versions,
            )
        except Exception as exc:
            return _case_error(exc, "case_open")

    @server.tool(name="case_list", annotations=_READ)
    def _case_list_impl(limit: int = 50) -> dict:
        """[READ] List investigation cases, newest first.

        WHEN: to find the id of a case you or someone else opened earlier.
        Returns the family list envelope {items, returned, limit, total,
        truncated, hint}; each item is {case_id, summary, state, grade,
        opened_at}. A case whose folder is damaged appears with
        state="unreadable" rather than vanishing from the list."""
        try:
            return t.case_list(limit=limit)
        except Exception as exc:
            return _case_error(exc, "case_list")

    @server.tool(name="case_get", annotations=_READ)
    def _case_get_impl(case_id: str) -> dict:
        """[READ] One case: its scope, its ledger sizes, and its grade history.

        WHEN: to pick up an investigation, or to see why a case sits at the
        grade it does. Returns counts and identifiers rather than the whole
        ledger — read the case folder itself (the `path` from case_open) for
        full evidence bodies.

        RETURNS: {case_id, path, state, grade, opened_at, scope, evidence_count,
        sources, gap_count, blocking_gaps, grade_history}. `sources` is the
        distinct skills evidence came from, which is what corroboration is
        counted in."""
        try:
            return t.case_get(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "case_get")

    # The widest signature here, and deliberately so. `per_tool_token_discipline`
    # flags it, but ~322 of its ~570 manifest tokens are the JSON schema for
    # twelve parameters, not prose — and those twelve ARE the evidence contract:
    # drop one and a conclusion stops being traceable to what produced it.
    # Folding the four time fields into a nested `time_basis` object would shrink
    # the schema and make it likelier to be filled wrong; flat optional fields
    # with explicit nulls are what the design asks for, because an absent key is
    # what a model fills with invention. Trim the prose here and the cost stays
    # while the explanation goes.
    @server.tool(name="case_submit_evidence", annotations=_WRITE_LOCAL)
    def _case_submit_evidence_impl(
        case_id: str,
        source_skill: str,
        source_tool: str,
        summary: str,
        query: Optional[dict] = None,
        fetched_at: Optional[str] = None,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        time_source: Optional[str] = None,
        clock_skew_s: Optional[float] = None,
        falsifies: Optional[list[str]] = None,
        payload: Optional[dict] = None,
    ) -> dict:
        """[WRITE] Record one retrieved fact — steps 02/03 of the evidence loop.

        WHEN: after every read-tool call you intend to reason from.

        INPUT: source_skill + source_tool (which produced it) and query (the
        exact parameters), so it can be re-run. window_start/window_end are the
        period the DATA COVERS, not when it was fetched — list_events(hours=24)
        run at 10:00 and at 18:00 answer different questions, and correlation
        depends on which. time_source ("vcenter"/"host"/"client") and
        clock_skew_s feed skew detection; pass null when unknown, never a guess.
        falsifies: hypothesis ids this RULES OUT — the only thing that can
        exclude one. payload: the raw result.

        RETURNS: {case_id, evidence_id, grade, reasons}, including the resulting
        grade so you need no second call to see whether this changed anything.

        GOTCHAS: a fetch that failed or came back empty goes to case_record_gap,
        not here."""
        try:
            return t.case_submit_evidence(
                case_id=case_id,
                source_skill=source_skill,
                source_tool=source_tool,
                query=query or {},
                summary=summary,
                fetched_at=fetched_at,
                window_start=window_start,
                window_end=window_end,
                time_source=time_source,
                clock_skew_s=clock_skew_s,
                falsifies=falsifies,
                payload=payload,
            )
        except Exception as exc:
            return _case_error(exc, "case_submit_evidence")

    @server.tool(name="case_record_gap", annotations=_WRITE_LOCAL)
    def _case_record_gap_impl(
        case_id: str,
        what: str,
        why: str,
        how_to_close: str,
        blocks: Optional[list[str]] = None,
        could_falsify: bool = False,
    ) -> dict:
        """[WRITE] Record something the investigation could NOT obtain.

        WHEN: any time a fetch failed, was refused, returned nothing, or the
        data simply does not exist in this environment. This is the tool that
        keeps a case honest: an unrecorded gap makes it look better supported
        than it is.

        INPUT: what (the missing observation); why (why it could not be had);
        how_to_close (the next action, even if it is outside this system — a
        gap with no stated next action reads like a to-do and gets skipped);
        blocks (hypothesis ids it holds up); could_falsify — would obtaining
        this be able to prove the hypothesis WRONG? Most gaps are missing
        corroboration (false: caps the case below Confirmed). A gap that could
        overturn the hypothesis is different in kind (true: holds it at
        Candidate, because claiming otherwise claims a check nobody ran).

        RETURNS: {case_id, gap_id, grade, reasons}.

        GOTCHAS: recording a gap does not punish the case for the evidence it
        does have — a missing confirmation caps the grade, it does not demote
        it. Writing gaps down is meant to be free."""
        try:
            return t.case_record_gap(
                case_id=case_id,
                what=what,
                why=why,
                how_to_close=how_to_close,
                blocks=blocks,
                could_falsify=could_falsify,
            )
        except Exception as exc:
            return _case_error(exc, "case_record_gap")

    @server.tool(name="case_grade", annotations=_WRITE_LOCAL)
    def _case_grade_impl(case_id: str) -> dict:
        """[WRITE] Compute and record the conclusion grade — steps 07/08.

        WHEN: when you think the investigation has reached a conclusion, or to
        record where it stands before handing it over.

        There is deliberately NO parameter for the grade. You cannot state a
        conclusion level; it is recomputed from the ledger on every call. If
        you disagree with the result, change the ledger — submit the evidence
        that is missing, or record the gap that is blocking it.

        The levels: Candidate (a hypothesis exists); Probable (at least two
        INDEPENDENT sources agree — two calls to the same skill are one source
        — and nothing outstanding could overturn it); Confirmed (that, plus a
        decisive item: a direct hardware diagnostic, a version-checked
        knowledge-base entry, or a vendor SR, and no gap left open); Excluded
        (an observation that actually rules the hypothesis out — "we looked and
        found nothing" is a gap, not an exclusion).

        RETURNS: {grade, previous, direction, reasons, ceiling, ceiling_reasons,
        rules_source, rules_origin}. `direction` is initial/up/down/unchanged —
        grades may go DOWN, and the history records it when they do.

        GOTCHAS: on a stock install `ceiling` is "probable", because Confirmed
        needs a decisive source and there is neither a hardware-diagnostic
        channel nor a knowledge library mounted yet. That is a real limit, not
        a caution. Every grading is appended to conclusion.md and none is ever
        rewritten."""
        try:
            return t.case_grade(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "case_grade")

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
