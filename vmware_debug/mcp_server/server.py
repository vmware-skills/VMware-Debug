"""vmware-debug MCP server entry point.

Tools are defined in vmware_debug.mcp.tools (so audit logs see skill=debug).
This module wires them into a FastMCP server and provides the stdio entry point.

Note: signatures here use typing.Optional, never PEP 604 ``X | None`` — FastMCP
reflects these at registration and ``X | None`` crashes on Python 3.10 + older
mcp/pydantic (CLAUDE.md 踩坑 #33).
"""

import logging
import sys
from typing import Optional, Union

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

    # 454 tokens against a 350 ceiling. The first seven over were the two
    # sentences pointing at `binning` and `classification`, added because a real
    # investigation went wrong for want of them: the answer had been computed at
    # 23-hour resolution and over a stream it could read 20% of, and said
    # neither. A result whose limits are not stated is read as one without any,
    # so trimming those two would keep the cost and remove the point. The rest
    # is the `Args:` block, which is not decoration either: without a per-
    # parameter description the schema an agent reads says only "number", and
    # bin_seconds and z_threshold are exactly the two knobs a wrong guess makes
    # silently useless.
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

        RETURNS: {event_count, window, binning, classification, spikes
        (strongest anomalous bins), spikes_total, hypotheses (ranked
        root-cause candidates, each with a suggested_check), next_checks (which
        skill/tool to run next)}. Read `binning` for the resolution you were
        given, and `classification` for how much of the stream matched nothing —
        the ranking describes only the part that did.

        GOTCHAS: read-only, stateless, no network — nothing is executed.
        Remediation routes to vmware-aiops (single fix) or vmware-pilot
        (multi-step). A malformed event returns {error, hint} naming the
        offending index.

        Args:
            events: Event envelopes, each {ts, source, severity, entity, text,
                fields}. ts may be ISO-8601, epoch seconds or epoch millis and
                is required; severity is normalised onto critical/error/warning/
                info/unknown, so vendor spellings (fatal, red, warn, yellow,
                notice, green) are accepted. An entry that cannot be normalised
                is refused with its index, not skipped.
            bin_seconds: Time-bin width in seconds. Omit and it is chosen from
                event density off the ladder 1/10/60/300/900/3600/21600/86400,
                taking the finest width still averaging 4 events per bin.
            z_threshold: Standard deviations above the mean bin count that mark
                a spike (default 2.0). Under 3 bins, or a flat series, yields
                none at any threshold — empty spikes is not "calm".
            top_n: How many ranked hypotheses come back (default 5). Spikes are
                capped separately at 20, true count in 'spikes_total'.
        """
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

        RETURNS: {case_id, path, state, grade, ceiling, ceiling_reasons, next}.
        Read `ceiling` now — it is the best grade this install can reach at all.

        GOTCHAS: writes only under $OPS_HOME, never to a VMware system, and
        never over an existing case.

        Args:
            summary: What is wrong, one line. case_plan reads it to infer the
                symptom category, so name the symptom in the estate's own words
                ("datastore latency on cluster-a"), not a diagnosis.
            determined_by: HOW the scope was decided — "user report", "vCenter
                alarm 42". Required: a scope from a phone call and one from an
                alarm id support different conclusions, and nobody remembers
                which it was later.
            objects: Entities in scope, names or MoIDs ("vm-web01", "host-12").
                Omit for a case not yet pinned to specific objects.
            window_start: When the incident is believed to have started,
                ISO-8601. Omit if unknown — evidence gets fetched against it,
                so an invented window is worse than none.
            window_end: Believed end, ISO-8601. Omit while still in progress.
            product_versions: Versions keyed by product — {"esxi": "8.0.3"} —
                plus "driver.<name>" / "firmware.<name>" entries. Knowledge-base
                entries are version-checked against this: one constraining a
                product absent here can support a hypothesis but never make the
                case Confirmed, since an unanswerable constraint is not a pass.
        """
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
        state="unreadable" rather than vanishing from the list.

        Args:
            limit: Maximum cases in the page, newest first (default 50). There
                is no offset — 'total' reports every case, so when truncated is
                true the only way to reach older ones is a larger limit.
        """
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
        counted in.

        Args:
            case_id: The case id returned by case_open, or listed by case_list.
                An unknown id is an error, never an empty case.
        """
        try:
            return t.case_get(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "case_get")

    # The widest signature here, and deliberately so. `per_tool_token_discipline`
    # flags it, but ~322 of its ~900 manifest tokens are the JSON schema for
    # thirteen parameters, not prose — and those thirteen ARE the evidence
    # contract:
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
        knowledge_entry_id: Optional[str] = None,
        # Union, not dict: the documented payload shape includes a bare array of
        # events, and typed Optional[dict] that shape was refused by schema
        # validation before any of this skill's code ran.
        payload: Optional[Union[dict, list]] = None,
    ) -> dict:
        """[WRITE] Record one retrieved fact — steps 02/03 of the evidence loop.

        WHEN: after every read-tool call you intend to reason from.

        RETURNS: {case_id, evidence_id, payload_events, payload_note, grade,
        reasons} — the resulting grade, so you need no second call to see
        whether this changed anything, and what the payload was read as, so a
        summary submitted in place of a result is visible here rather than as a
        zero from case_timeline later.

        GOTCHAS: a fetch that failed or came back empty goes to case_record_gap,
        not here.

        Args:
            case_id: The case this fact belongs to (from case_open/case_list).
                Required.
            source_skill: The skill that produced it, as the family spells it —
                "vmware-monitor", "vmware-aria", "vmware-log-insight". Required,
                non-blank. Two items from the SAME skill count as ONE source
                when corroboration is counted, so this string decides whether
                the case can reach Probable. Two reserved values name the
                knowledge layer instead: "knowledge-kb" and "knowledge-sr", the
                only sources that can be decisive.
            source_tool: The tool within that skill, e.g. "get_events".
                Required, non-blank — "monitor said so" is not reproducible.
            summary: What this item shows, one line.
            query: The exact parameters the tool was called with, so it can be
                re-run. Omit only if there genuinely were none.
            fetched_at: When the fetch happened, ISO-8601. Omit to stamp now —
                wrong for an item transcribed from earlier.
            window_start: Start of the period the DATA COVERS, ISO-8601 — not
                when it was fetched. get_events(hours=24) run at 10:00 and at
                18:00 answer different questions.
            window_end: End of the period the data covers, ISO-8601.
            time_source: Whose clock stamped it — "vcenter", "host" or "client".
                Recorded so a reader can judge whether two sources' timestamps
                compare; nothing in this release corrects for it. Null when
                unknown, never a guess.
            clock_skew_s: Known offset of that clock from UTC, in seconds.
                Recorded, likewise not applied. Null when unknown.
            falsifies: Hypothesis ids (H1, H2, …) this observation RULES OUT —
                the only route to Excluded; "we looked and found nothing" is a
                gap, not an exclusion. Ids must already exist via
                case_hypotheses; an unregistered id is refused and nothing is
                written.
            knowledge_entry_id: Which mounted knowledge entry this item IS.
                REQUIRED when source_skill is knowledge-kb or knowledge-sr —
                without it the entry's applies_to cannot be checked, so it
                counts as ordinary support and can never make the case
                Confirmed. case_knowledge lists what is mounted.
            payload: The read tool's RAW result, not a summary. For its events
                to reach case_timeline it must be a list of event dicts, or
                carry them under 'items', 'events' or 'rows'. What was found
                comes back as payload_events/payload_note.
        """
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
                knowledge_entry_id=knowledge_entry_id,
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

        RETURNS: {case_id, gap_id, grade, reasons}.

        GOTCHAS: recording a gap does not punish the case for the evidence it
        does have — a missing confirmation caps the grade, it does not demote
        it. Writing gaps down is meant to be free.

        Args:
            case_id: The case this gap belongs to (from case_open/case_list).
            what: The observation that could not be obtained — the thing you
                wanted, not the error you got.
            why: Why it could not be had — refused, unreachable, collected by
                nothing in this family, absent from this environment.
            how_to_close: The next action that would close it, even outside this
                system ("open a vendor SR"). A gap with no stated next action
                reads like a to-do and gets skipped.
            blocks: Hypothesis ids (H1, H2, …) this gap holds up; they must
                already exist via case_hypotheses. Empty is fine.
            could_falsify: Would OBTAINING this be able to prove the hypothesis
                WRONG? false (default) is the ordinary case — missing
                corroboration, capping the case below Confirmed. true means it
                could overturn the hypothesis, holding it at Candidate.
        """
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
        rewritten.

        Args:
            case_id: The case to grade (from case_open/case_list). This is the
                only parameter — see above for why there is no grade parameter.
        """
        try:
            return t.case_grade(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "case_grade")

    @server.tool(name="case_readiness", annotations=_READ)
    def _case_readiness_impl(available_skills: Optional[list[str]] = None) -> dict:
        """[READ] What strength of conclusion can this environment reach?

        WHEN: before starting an investigation, or when a case will not go
        higher and you want to know whether that is fixable. Answering this
        first is worth far more than discovering it halfway through.

        RETURNS: {classes, categories, unrecognised_skills, note}. A name that
        matched no skill in the catalogue comes back in `unrecognised_skills`
        rather than being absorbed into "not installed" — otherwise a typo reads
        as advice to install something you already have. Per evidence class:
        whether it is
        available, through which tools, and if not, `how_to_supply`. Per
        symptom category (storage, network, compute, ha_drs, configuration,
        accelerator, kubernetes, hardware): a `ceiling` and the
        `independent_sources` behind it. There is deliberately no single score —
        "readiness 78%" cannot be acted on, "storage reaches Probable, hardware
        reaches Candidate" can.

        GOTCHAS: two classes served by the SAME skill count as one source, so
        two available classes do not always mean Probable. The hardware class is
        unavailable no matter what is installed — nothing in this family reaches
        below ESXi — and the knowledge class becomes available only when entries
        are mounted under $OPS_HOME/knowledge/.

        Args:
            available_skills: The skills actually installed and configured, in
                either of the family's spellings — "monitor" and "vmware-monitor"
                name the same thing. Omit to assume all of them, which reports
                the ceiling imposed by the family itself rather than by this
                install. A name matching no catalogued skill comes back in
                `unrecognised_skills` rather than being read as "not installed".
        """
        try:
            return t.case_readiness(available_skills=available_skills)
        except Exception as exc:
            return _case_error(exc, "case_readiness")

    @server.tool(name="case_plan", annotations=_READ)
    def _case_plan_impl(
        case_id: str,
        category: Optional[str] = None,
        available_skills: Optional[list[str]] = None,
        max_steps: int = 6,
    ) -> dict:
        """[READ] What to fetch next for this case — step 02, recomputed each call.

        WHEN: right after case_open, and again after each round of evidence. It
        is not a checklist: submit something and the next plan is shorter, lose
        a source and it routes around it.

        RETURNS: {category, category_signals, steps, already_covered,
        held_back, unavailable,
        ceiling, note}. Steps are interleaved across evidence classes, so you
        get breadth before depth — corroboration is counted in distinct sources,
        which is what actually moves the grade.
        Each step is {evidence_class, skill, tool, purpose, objects, window,
        degraded} — call that skill's tool, then submit the result with
        case_submit_evidence.

        GOTCHAS: `unavailable` is the important half. A source this install
        cannot reach is listed there with how_to_supply rather than left out, so
        the gap is visible now instead of when the conclusion refuses to firm
        up. An empty `steps` is never silent — `note` says whether everything
        reachable is already in, or whether nothing here can be reached.
        `category_signals` names the words that chose the category, and any
        category that also matched — check it first, since the rest runs off
        that one word.

        Args:
            case_id: The case to plan for (from case_open/case_list).
            category: Force the symptom class instead of inferring it. Exactly
                one of: storage, network, compute, ha_drs, configuration,
                accelerator, kubernetes, hardware, host_lifecycle,
                power_lifecycle, auth, platform. Omit to infer, then read
                `category_signals` for the word that decided it.
            available_skills: Narrow to the skills actually installed, in either
                spelling ("monitor" or "vmware-monitor"). Omit to assume all of
                them, which can produce steps this install cannot run.
            max_steps: HAS NO EFFECT in this release — accepted but never
                forwarded to the planner, which always caps at 6. The result's
                note may still advise raising it; doing so changes nothing.
                Read `held_back` for how many steps were cut.
        """
        try:
            return t.case_plan(
                case_id=case_id,
                category=category,
                available_skills=available_skills,
            )
        except Exception as exc:
            return _case_error(exc, "case_plan")

    @server.tool(name="case_hypotheses", annotations=_WRITE_LOCAL)
    def _case_hypotheses_impl(case_id: str, statement: Optional[str] = None) -> dict:
        """[WRITE] Register a candidate explanation, or read the ledger — step 06.

        WHEN: as soon as you have a theory worth testing, and again to see where
        each one stands. Pass `statement` to add one; omit it to just read.

        Every hypothesis gets an id (H1, H2, …). Those ids are what
        case_record_gap(blocks=[...]) and case_submit_evidence(falsifies=[...])
        refer to, and an id that was never registered is REFUSED rather than
        ignored — a dangling reference blocks nothing and falsifies nothing,
        which quietly reports a stronger case than you have.

        RETURNS: {case_id, added, hypotheses, note}. Each entry carries its
        status and what produced it: `refuted` (an observation ruled it out,
        with the evidence id), `blocked` (a gap is in the way, with the gap id
        and how to close it), or `open`. Status is computed from what points at
        the hypothesis — a hypothesis does not get to claim it is well
        supported, the same way a case does not get to state its own grade.

        GOTCHAS: refuted outranks blocked. Once an observation settles the
        question, a missing measurement no longer matters.

        Args:
            case_id: The case whose hypothesis ledger this is (from
                case_open/case_list).
            statement: The candidate explanation, in one line. Pass it to
                register a new hypothesis, which is assigned the next id (H1,
                H2, …); omit it to read the ledger without changing it. There is
                no parameter for a hypothesis's status — status is computed from
                the evidence and gaps that point at it.
        """
        try:
            return t.case_hypotheses(case_id=case_id, statement=statement)
        except Exception as exc:
            return _case_error(exc, "case_hypotheses")

    @server.tool(name="case_timeline", annotations=_WRITE_LOCAL)
    def _case_timeline_impl(
        case_id: str,
        bin_seconds: Optional[float] = None,
        z_threshold: float = 2.0,
        top_n: int = 5,
    ) -> dict:
        """[WRITE] Correlate everything this case has collected — steps 04/05.

        WHEN: once evidence is in. Unlike incident_timeline, this takes no
        events: it reads the payloads already submitted, so the result is
        reproducible from the case folder alone months later, on a machine with
        access to nothing.

        RETURNS: {event_count, window, binning, classification, spikes,
        spikes_total, hypotheses, evidence_without_events,
        evidence_without_events_detail, rejected, note} and writes timeline.md.

        GOTCHAS: `note` distinguishes three states that all show zero events —
        no evidence submitted at all, evidence that carried none, and a genuinely
        quiet window — and names which items carried none, with what they held
        instead. `rejected` names any row that could not be read, with the
        evidence item it came from; dropping those silently would shrink the
        picture the conclusion rests on. Submit a read tool's raw result as
        `payload` for its events to reach here — a summary of the result carries
        no rows, and case_submit_evidence says so at the time.

        Args:
            case_id: The case whose submitted payloads are correlated (from
                case_open/case_list). Unlike incident_timeline this takes no
                events — everything comes from the case folder.
            bin_seconds: Time-bin width in seconds. Omit and it is chosen from
                event density (ladder 1..86400, finest width still averaging 4
                events per bin); `binning` reports which was used.
            z_threshold: Standard deviations above the mean bin count that mark
                a spike (default 2.0). Under 3 bins, or a flat series, yields
                none at any threshold.
            top_n: How many ranked hypotheses come back (default 5). Spikes are
                capped separately at 20, true count in 'spikes_total'.
        """
        try:
            return t.case_timeline(
                case_id=case_id,
                bin_seconds=bin_seconds,
                z_threshold=z_threshold,
                top_n=top_n,
            )
        except Exception as exc:
            return _case_error(exc, "case_timeline")

    @server.tool(name="case_close", annotations=_WRITE_LOCAL)
    def _case_close_impl(case_id: str) -> dict:
        """[WRITE] Record the final grade and archive the case — step 08.

        WHEN: when the investigation is finished, or is being handed over.
        Closing turns a working folder into a record other people rely on, so it
        computes and records the grade rather than accepting one.

        RETURNS: {case_id, state, grade, open_gaps, path, note}. `open_gaps`
        names anything still blocking a hypothesis at the moment of closing —
        stated here rather than left in the file for someone to find.

        GOTCHAS: a closed case is not closed again and its record is never
        rewritten. To reopen the question, open a new case that cites this one,
        so the original conclusion and whatever changed it both stay readable.

        Args:
            case_id: The case to close (from case_open/case_list). There is no
                grade parameter — closing recomputes the grade from the ledger.
                An already-closed case is refused rather than closed again.
        """
        try:
            return t.case_close(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "case_close")

    @server.tool(name="case_knowledge", annotations=_READ)
    def _case_knowledge_impl(case_id: Optional[str] = None) -> dict:
        """[READ] What the knowledge layer accepts, and what is mounted.

        WHEN: when someone asks what can be added to make conclusions stronger,
        or when a case will not reach Confirmed and you need to say why in terms
        they can act on. This is the answer to "which knowledge formats do you
        take" — the first question anyone mounting a library asks.

        RETURNS: {root, sections, entries, with_applies_to, by_source,
        unreadable, unsupported, formats, needs_conversion, note} — plus
        {applicable, decisive_here} when a case_id is given.

        `formats` lists every extension read and how each carries its metadata:
        Markdown front-matter, YAML/JSON whole-file, JSONL per line, CSV/TSV per
        row, and plain text with a sibling .yaml. `needs_conversion` names the
        ones that must become Markdown first (PDF, DOCX, PPTX, HTML).

        GOTCHAS: an entry is decisive ONLY if its `applies_to` block was checked
        against the case scope and passed — matching is by version
        applicability, never by similarity, because an entry written for the
        wrong build reads exactly like the right one. An entry with no
        `applies_to` can support a hypothesis but can never make a case
        Confirmed. A constraint the case scope cannot answer is not a match
        either: silence is not a pass.

        Args:
            case_id: Omit to describe the knowledge layer itself — what formats
                are read and what is mounted. Pass a case id and every mounted
                entry is additionally version-checked against THAT case's
                product_versions, adding `applicable` and `decisive_here` with
                the reason each entry did or did not qualify.
        """
        try:
            return t.case_knowledge(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "case_knowledge")

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
