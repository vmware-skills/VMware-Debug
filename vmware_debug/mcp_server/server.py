"""vmware-debug MCP server entry point.

Tool logic lives in vmware_debug.mcp.tools (the case tools' bodies in
ops/cases/api.py). This module wires them into a FastMCP server and provides
the stdio entry point.

Every tool here goes through vmware_policy's ``@vmware_tool``, so each call —
a failed one included — writes one row to ~/.vmware/audit.db (HLD §8.1, I-10,
amended 2026-09-15b). Until then none did: this module said no audit was needed
because nothing here acts on a VMware target, and the case ledger was offered
as the record. The ledger records the investigation, not the calls — a live
session submitted evidence with invented fetch times and nothing could show
when the calls had really been made or which had failed. Large data the caller
hands over (``payload``, ``events``) is kept out of the row via
``sensitive_params``; it lives in the ledger or with the caller. The two tools
whose *result* quotes that event text — ``build_incident_timeline`` and
``record_case_timeline`` (``hypotheses[].sample_text``, ``unmatched_samples``,
``rejected``) — declare ``sensitive_result``, so their row keeps the call, the
arguments and the status but not the result body: event text fetched from
vCenter or a log can carry anything, credentials included (review D3,
2026-09-15). The other tools return ids, counts, grades and text the caller
wrote as an argument, which the row already holds.

Note: signatures here use typing.Optional, never PEP 604 ``X | None`` — FastMCP
reflects these at registration and ``X | None`` crashes on Python 3.10 + older
mcp/pydantic (CLAUDE.md 踩坑 #33).
"""

import sys as _sys
import logging
import sys
from typing import Optional, Union  # noqa: F401 — compatibility/runtime export

from mcp.server.fastmcp import FastMCP
from vmware_policy import describe_tool_parameters, sanitize, vmware_tool  # noqa: F401 — compatibility/runtime export

from vmware_debug.mcp import tools as t  # noqa: F401 — compatibility/runtime export
from vmware_debug import __version__
from vmware_debug.ops.cases.plan import DEFAULT_MAX_STEPS  # noqa: F401 — compatibility/runtime export
from vmware_debug.ops.cases.store import CaseError

logger = logging.getLogger("mcp_server")


# ---------------------------------------------------------------------------
# Environment declaration — deliberately absent
# ---------------------------------------------------------------------------
#
# This module registers no ``vmware_policy`` environment resolver. That is the
# fix for a real DENY -> ALLOW regression, not an oversight, so here is why in
# full.
#
# ``set_environment_resolver`` writes to a single process-global slot shared by
# every skill in the interpreter. This module used to call it at *import* time
# with a resolver that answered ``"local"`` for every target it was asked about.
# In an MCP host that loads several of these skills into one process — the
# normal deployment — importing vmware-debug therefore replaced whatever
# resolver a sibling had installed, and every one of that sibling's targets
# started resolving to ``"local"``. A rule scoped to
# ``environments: [production]`` stops matching, so it stops denying. The
# real-hardware round measured exactly that, and nothing surfaced it: a deny
# rule that under-matches is silent by construction.
#
# Two things were wrong and both had to go.
#
# The *timing*: registering a process-global at import means a bare ``import``
# — the thing a host does merely to enumerate a package — mutates enforcement
# for code that has nothing to do with this skill. The old note here said the
# registration sat at import because ``build_server()`` would otherwise re-run
# it. That reasoning answered the wrong question: the cost of re-registering a
# constant is nil, and the cost of registering it at all is a sibling's rules.
#
# The *answer*: a resolver is asked "what environment is target X in?" and this
# one replied ``"local"`` for every X. For X = ``prod-vc01`` that is not a
# conservative default, it is false, and it is precisely the value that makes
# production-scoped rules stop matching. Moving the same constant into
# ``build_server()`` would only narrow who gets lied to.
#
# So the registration is gone rather than relocated. vmware-debug has no target
# config and no connection to declare one about: its tools either correlate
# event dicts the calling agent already fetched, or read and write the
# investigation ledger under $OPS_HOME. The ledger tools are writes — to a
# directory on this machine, and nowhere else — but "a write with no
# VMware target" is not the same claim as "every target in this process is in
# environment local", and only the second one was being made. Having no basis to
# answer for any target, the honest thing is to leave the slot alone and let
# ``resolve_environment`` apply its documented default: unlabeled (``""``),
# which matches no environment-scoped rule and is never refused for lack of a
# label (HLD §6, D-3).
#
# Declining was a fix for this skill, not for the hazard, which lived in the
# one global slot. vmware-policy 1.12.0 fixed that part: a resolver registered
# with ``skill=`` is stored under that skill and answers only for it; the
# unkeyed form still works and still warns. Debug still registers none — it
# has no targets to answer for.

#: Client-facing behaviour hints, matching the rest of the family, for the seven
#: [READ] tools: the two correlation tools (pure functions over dicts the caller
#: already fetched) and the case reads over the ledger. Repeating a call
#: changes nothing. These drive MCP client UI (e.g. whether a call needs a
#: confirmation prompt).
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
#: False because nothing a caller recorded is ever lost: opening a case refuses
#: to overwrite one, evidence lands in its own file, gaps and hypotheses are
#: only added to, and a grade is appended to the history rather than replacing
#: it. What is rewritten is derived — ``timeline.md`` is regenerated from the
#: evidence, and ``case.json`` carries the current grade and state — so there
#: is nothing to undo. That holds for concurrent writers on a shared $OPS_HOME
#: too: every write takes the case's ledger lock (``store.ledger_lock``) and
#: moves its file into place atomically, so no entry is lost to a race.
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


#: Recovery step attached to every build_incident_timeline failure. Hoisted to a
#: constant so the wording stays in one place rather than drifting per call site.
_TIMELINE_ERROR_HINT = (
    "build_incident_timeline correlates events you have already fetched with other "
    "skills' read tools — it fetches nothing itself, so a rejected event has to be "
    "corrected in the 'events' argument you pass. 'error' names the offending "
    "entry and the expected form; fix it and call build_incident_timeline again. Run "
    "list_symptom_categories if you need to know which skill to pull events from."
)

_CASE_ERROR_HINT = (
    "The case tools read and write the investigation ledger under $OPS_HOME "
    "(default ~/.vmware/cases) and touch no VMware system, so a failure here is "
    "about the case id, the arguments, or the folder itself — never about "
    "vCenter connectivity. 'error' names what is wrong and what to do; "
    "list_cases shows the ids that exist. Note there is no 'items' key on a "
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
#: ("run list_cases to see the ids that exist"), and reducing them to
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


    # ── Investigation cases ───────────────────────────────────────────────
    # The eight-step evidence loop's ledger. debug holds no credentials and
    # reaches no VMware environment: the agent fetches with the data-source
    # skills' read tools and submits the results here, which is what lets a
    # finished case be reopened and re-argued on a machine with access to
    # nothing.

    def _case_error(exc: Exception, tool: str) -> dict:
        """Return a failed call as a payload, never as an empty result."""
        return {"error": _safe_error(exc, tool), "hint": _CASE_ERROR_HINT}




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


    from vmware_debug.mcp_server._tools_cases import register_callbacks as _register_tools_cases

    (
        build_incident_timeline,
        list_symptom_categories,
        open_case,
        list_cases,
        get_case,
        submit_case_evidence,
        record_case_gap,
    ) = _register_tools_cases(server, _sys.modules[__name__], _case_error)








    from vmware_debug.mcp_server._tools_runs import register_callbacks as _register_tools_runs

    (
        grade_case,
        get_case_readiness,
        plan_case,
        record_case_hypothesis,
        record_case_timeline,
        close_case,
        get_case_knowledge,
    ) = _register_tools_runs(server, _sys.modules[__name__], _case_error)
    # The docstrings above are the schema — see describe_tool_parameters.
    describe_tool_parameters(server._tool_manager._tools)


    return server
def main(
    expose: Optional[str] = None,
    allow_write: Optional[str] = None,
    transport: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    public_name: Optional[str] = None,
    token_file: Optional[str] = None,
) -> None:
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
    from vmware_policy.serve import run_server
    server = build_server()
    run_server(
        server,
        skill="debug",
        expose=expose, allow_write=allow_write, transport=transport, host=host, port=port,
        cert=cert, key=key, public_name=public_name, token_file=token_file,
    )


# Role metadata for network callers (HLD §18).
import vmware_debug.policy_roles  # noqa: F401

if __name__ == "__main__":
    main()
