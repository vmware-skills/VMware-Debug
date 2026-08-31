"""Steps 04/05 and 08 — build the timeline from the ledger, and close the case.

``build_case_timeline`` reuses the correlation engine this skill already had.
The difference from ``incident_timeline`` is where the events come from: a case
has already collected them, each stamped with the tool and query that produced
it, so the timeline can be rebuilt from the folder alone months later.

``close_case`` is the act that turns a working folder into a record other people
rely on, so it is deliberately loud about what it is closing over: an unresolved
gap is named in the result rather than left for someone to notice in the file.
"""

from __future__ import annotations

from typing import Any

from vmware_debug.envelope import normalize_events
from vmware_debug.ops.cases.conclusion import record_grade
from vmware_debug.ops.cases.evidence import load_evidence, load_gaps
from vmware_debug.ops.cases.grading import grade_case
from vmware_debug.ops.cases.payloads import describe_empty, inspect_payload
from vmware_debug.ops.cases.store import CaseError, case_dir, load_case
from vmware_debug.ops.timeline import incident_timeline


def build_case_timeline(
    case_id: str,
    bin_seconds: float | None = None,
    z_threshold: float = 2.0,
    top_n: int = 5,
) -> dict[str, Any]:
    """Correlate everything this case has collected into one timeline.

    Reads each evidence item's stored payload rather than asking the caller for
    events, so the result is reproducible from the case folder with no access to
    anything.

    An event that cannot be normalised is REPORTED, with the item it came from —
    dropping it would quietly shrink the picture the conclusion rests on.
    """
    import json

    evidence = load_evidence(case_id)
    d = case_dir(case_id) / "evidence"

    rows: list[dict] = []
    rejected: list[str] = []
    # Named, not merely counted. "N items carried no events" is the same
    # unusable answer the tester was given; which items, and what they held
    # instead, is what lets someone see they submitted a summary.
    without_events: list[str] = []
    for item in evidence:
        path = d / f"{item.evidence_id}.json"
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rejected.append(f"{item.evidence_id}: payload unreadable")
            continue
        shape = inspect_payload(body.get("payload"))
        if not shape.rows:
            without_events.append(describe_empty(item.evidence_id, shape))
            continue
        for i, raw in enumerate(shape.rows):
            try:
                rows.extend(normalize_events([raw]))
            except Exception as exc:
                rejected.append(f"{item.evidence_id}[{i}]: {exc}")

    result: dict[str, Any] = (
        incident_timeline(rows, bin_seconds=bin_seconds, z_threshold=z_threshold, top_n=top_n)
        if rows
        else {"event_count": 0, "window": None, "spikes": [], "hypotheses": []}
    )
    result["case_id"] = case_id
    result["evidence_without_events"] = len(without_events)
    result["evidence_without_events_detail"] = without_events
    result["rejected"] = rejected
    result["note"] = _note(len(evidence), len(rows), without_events, rejected)

    _write_timeline_md(case_id, result, rows)
    return result


def _note(evidence_count: int, event_count: int, without: list[str], rejected: list) -> str:
    if evidence_count == 0:
        return (
            "No evidence has been submitted, so there is no timeline to build. "
            "Run case_plan for what to fetch, then case_submit_evidence with the "
            "results — pass the raw result as `payload` for it to appear here."
        )
    if event_count == 0:
        return (
            f"No events among {evidence_count} evidence item(s): "
            f"{len(without)} carried no event rows ({'; '.join(without)}). That "
            f"is not the same as a quiet window. Events are read from a bare "
            f"list, or from `items` (the family list envelope), `events` or "
            f"`rows` in the submitted payload — submit the read tool's raw "
            f"result rather than a summary of it. A query that genuinely "
            f"returned nothing belongs in case_record_gap, not here."
        )
    tail = ""
    if without:
        tail += f" {len(without)} item(s) carried no event rows ({'; '.join(without)})."
    if rejected:
        tail += f" {len(rejected)} row(s) could not be read and are listed."
    return (
        f"{event_count} event(s) from {evidence_count - len(without)} evidence item(s)." + tail
    )


def _write_timeline_md(case_id: str, result: dict, rows: list) -> None:
    lines = [
        "# Timeline",
        "",
        result["note"],
        "",
        "## Trigger · Symptom · Propagation · Recovery",
        "",
    ]
    for ev in rows[:200]:
        lines.append(
            f"- `{getattr(ev, 'ts', '')}` **{getattr(ev, 'severity', '')}** "
            f"{getattr(ev, 'entity', '')} — {getattr(ev, 'text', '')}"
        )
    if result.get("rejected"):
        lines += ["", "## Could not be read", ""]
        lines += [f"- {r}" for r in result["rejected"]]
    (case_dir(case_id) / "timeline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def close_case(case_id: str, at: str) -> dict[str, Any]:
    """Step 08. Record the final grade, archive, and say what was left open."""
    import json

    case = load_case(case_id)
    if case.state == "closed":
        raise ValueError(
            f"Case {case_id} is already closed. Its record is not rewritten — "
            f"reopen the question by opening a new case that cites this one, so "
            f"the original conclusion and what changed it both stay readable."
        )

    result = grade_case(case_id)
    record_grade(case_id, result, at=at)

    open_gaps = [g.gap_id for g in load_gaps(case_id) if g.blocks]
    index_path = case_dir(case_id) / "case.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseError(f"Cannot read case.json for {case_id}: {exc}") from exc
    index.update({"state": "closed", "closed_at": at, "grade": result.grade})
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    note = f"Closed at {result.grade}."
    if open_gaps:
        note += (
            f" Closed with {len(open_gaps)} gap(s) still open ({', '.join(open_gaps)}) — "
            f"they are named here rather than left in the file, because a closed "
            f"case is a record other people rely on."
        )
    return {
        "case_id": case_id,
        "state": "closed",
        "grade": result.grade,
        "open_gaps": open_gaps,
        "path": str(case_dir(case_id)),
        "note": note,
    }
