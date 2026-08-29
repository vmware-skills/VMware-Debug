"""Agent-facing case operations — plain dicts in, plain dicts out.

Sits between the MCP tool registrations and the ledger modules so the tool
bodies stay declarative and the behaviour stays unit-testable without a running
MCP server.

Two conventions, both from expensive family lessons:

* Every write reports the resulting grade. An agent that has just submitted a
  piece of evidence should not have to make a second call to learn whether it
  changed anything, and a grade that only appears when asked for is a grade
  that gets asked for too late.
* Failures raise. They are never returned as an empty result — the tool layer
  turns them into an ``{"error": ..., "hint": ...}`` payload with no ``items``
  key, so a failed call can never be mistaken for an empty page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from vmware_policy import paginated

from vmware_debug.ops.cases.conclusion import grade_history, record_grade
from vmware_debug.ops.cases.evidence import (
    Evidence,
    Gap,
    load_evidence,
    load_gaps,
    record_evidence,
    record_gap,
)
from vmware_debug.ops.cases.grading import grade_case
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.plan import plan_next as _plan_next
from vmware_debug.ops.cases.readiness import readiness as _readiness
from vmware_debug.ops.cases.store import case_dir, create_case, list_cases, load_case


def utc_now() -> str:
    """An ISO-8601 instant. The one place this layer reads a clock."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_case(
    summary: str,
    determined_by: str,
    objects: list[str] | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    product_versions: dict[str, str] | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Step 01. Define the event and return the case id."""
    scope = Scope(
        summary=summary,
        objects=tuple(objects or ()),
        window_start=window_start,
        window_end=window_end,
        product_versions=dict(product_versions or {}),
        determined_by=determined_by,
    )
    case = create_case(scope, at=at or utc_now())
    result = grade_case(case.case_id)
    return {
        "case_id": case.case_id,
        "path": str(case_dir(case.case_id)),
        "state": case.state,
        "grade": result.grade,
        "ceiling": result.ceiling,
        "ceiling_reasons": list(result.ceiling_reasons),
        "next": (
            "Gather evidence with the data-source skills' read tools, then "
            "submit each result with case_submit_evidence. Anything you could "
            "not get goes to case_record_gap — an unrecorded gap makes the "
            "case look better supported than it is."
        ),
    }


def get_case(case_id: str) -> dict[str, Any]:
    """One case: its scope, its ledger sizes, and how it has been graded."""
    case = load_case(case_id)
    evidence = load_evidence(case_id)
    gaps = load_gaps(case_id)
    return {
        "case_id": case.case_id,
        "path": str(case_dir(case_id)),
        "state": case.state,
        "grade": case.grade,
        "opened_at": case.opened_at,
        "scope": case.scope.to_json(),
        "evidence_count": len(evidence),
        "sources": sorted({e.source_skill for e in evidence}),
        "gap_count": len(gaps),
        "blocking_gaps": [g.gap_id for g in gaps if g.blocks],
        "grade_history": [
            {"at": h.at, "grade": h.grade, "previous": h.previous, "direction": h.direction}
            for h in grade_history(case_id)
        ],
    }


def list_open_cases(limit: int = 50) -> dict[str, Any]:
    """Every case, newest first, in the family list envelope."""
    cases = list_cases()
    rows = [
        {
            "case_id": c.case_id,
            "summary": c.scope.summary,
            "state": c.state,
            "grade": c.grade,
            "opened_at": c.opened_at,
        }
        for c in cases[:limit]
    ]
    return paginated(rows, limit=limit, total=len(cases))


def submit_evidence(
    case_id: str,
    source_skill: str,
    source_tool: str,
    query: dict[str, Any],
    summary: str,
    fetched_at: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    time_source: str | None = None,
    clock_skew_s: float | None = None,
    falsifies: list[str] | None = None,
    payload: Any = None,
) -> dict[str, Any]:
    """Step 02/03. Record one retrieved fact and report where that leaves the case."""
    item = Evidence(
        source_skill=source_skill,
        source_tool=source_tool,
        query=dict(query or {}),
        fetched_at=fetched_at or utc_now(),
        summary=summary,
        window_start=window_start,
        window_end=window_end,
        time_source=time_source,
        clock_skew_s=clock_skew_s,
        falsifies=tuple(falsifies or ()),
    )
    stored = record_evidence(case_id, item, payload=payload)
    result = grade_case(case_id)
    return {
        "case_id": case_id,
        "evidence_id": stored.evidence_id,
        "grade": result.grade,
        "reasons": list(result.reasons),
    }


def add_gap(
    case_id: str,
    what: str,
    why: str,
    how_to_close: str,
    blocks: list[str] | None = None,
    could_falsify: bool = False,
) -> dict[str, Any]:
    """Record something the investigation could not obtain."""
    stored = record_gap(
        case_id,
        Gap(
            what=what,
            why=why,
            blocks=tuple(blocks or ()),
            could_falsify=could_falsify,
            how_to_close=how_to_close,
        ),
    )
    result = grade_case(case_id)
    return {
        "case_id": case_id,
        "gap_id": stored.gap_id,
        "grade": result.grade,
        "reasons": list(result.reasons),
    }


def grade(case_id: str, at: str | None = None) -> dict[str, Any]:
    """Steps 07/08. Recompute the grade from the ledger and record it.

    There is no parameter for the grade, here or anywhere below. The level is
    derived from what has been submitted; a caller that disagrees changes the
    ledger, not the verdict.
    """
    result = grade_case(case_id)
    entry = record_grade(case_id, result, at=at or utc_now())
    return {
        "case_id": case_id,
        "grade": result.grade,
        "previous": entry.previous,
        "direction": entry.direction,
        "reasons": list(result.reasons),
        "ceiling": result.ceiling,
        "ceiling_reasons": list(result.ceiling_reasons),
        "rules_source": result.rules_source,
        "rules_origin": result.rules_origin,
    }


def check_readiness(available_skills: list[str] | None = None) -> dict[str, Any]:
    """What grade each kind of investigation can reach here — design section 5."""
    return _readiness(available_skills=available_skills)


def plan(
    case_id: str,
    category: str | None = None,
    available_skills: list[str] | None = None,
) -> dict[str, Any]:
    """What to fetch next for this case, recomputed from its current state."""
    return _plan_next(case_id, category=category, available_skills=available_skills)
