"""Writing the grade down — append-only, demotions included.

Step 08. The grade itself is computed in :mod:`grading`; this module is only
concerned with recording it in a way that cannot quietly improve on its own
past. Every call appends: a promotion, a demotion, and a re-check that changed
nothing all leave a dated entry naming the rules file that produced them.

``conclusion.md`` is the human-facing record and is what a customer reads;
``case.json`` carries the current grade so a listing does not have to parse
prose. If they ever disagree, the markdown is the one to trust — it is the one
nothing overwrites.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from vmware_debug.ops.cases.grading import GradeResult
from vmware_debug.ops.cases.store import CaseError, case_dir, load_case

_PLACEHOLDER = "_Not graded yet._"

#: One history entry, fenced so the parser has an unambiguous boundary and a
#: human still reads plain markdown. Parsed back by :func:`grade_history`.
_ENTRY_RE = re.compile(r"^<!-- grade (?P<json>\{.*?\}) -->$", re.MULTILINE)


@dataclass(frozen=True)
class GradeEntry:
    """One recorded grading, and how it moved."""

    at: str
    grade: str
    previous: str | None
    direction: str  # initial | up | down | unchanged
    rules_source: str
    reasons: tuple[str, ...]


def _direction(previous: str | None, grade: str) -> str:
    from vmware_debug.ops.cases.model import GRADES

    if previous is None:
        return "initial"
    if previous == grade:
        return "unchanged"
    # `excluded` is a terminal verdict rather than a rung on the ladder, so
    # movement to or from it is reported as a change of kind, not a promotion.
    if grade == "excluded" or previous == "excluded":
        return "up" if grade == "excluded" else "down"
    return "up" if GRADES.index(grade) > GRADES.index(previous) else "down"


def record_grade(case_id: str, result: GradeResult, at: str) -> GradeEntry:
    """Append one grading to the case record and update the index.

    Never rewrites an earlier entry. A case that reached Probable and then fell
    back to Candidate says so, with the date and the reason, because that is
    the part of an investigation most worth being able to look up later.
    """
    d = case_dir(case_id)
    path = d / "conclusion.md"
    history = grade_history(case_id)
    previous = history[-1].grade if history else None

    entry = GradeEntry(
        at=at,
        grade=result.grade,
        previous=previous,
        direction=_direction(previous, result.grade),
        rules_source=result.rules_source,
        reasons=tuple(result.reasons),
    )

    try:
        body = path.read_text()
    except OSError as exc:
        raise CaseError(
            f"Cannot read conclusion.md for case {case_id}: {exc}. The case "
            f"directory may have been moved or its permissions changed."
        ) from exc

    body = body.replace(_PLACEHOLDER + "\n\n", "").replace(_PLACEHOLDER + "\n", "")
    path.write_text(body.rstrip("\n") + "\n\n" + _render(entry))

    index_path = d / "case.json"
    index = json.loads(index_path.read_text())
    index["grade"] = result.grade
    index["graded_at"] = at
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")

    return entry


def _render(entry: GradeEntry) -> str:
    arrow = {
        "initial": "first grading",
        "up": f"raised from {entry.previous}",
        "down": f"**lowered from {entry.previous}**",
        "unchanged": "unchanged",
    }[entry.direction]
    machine = json.dumps(
        {
            "at": entry.at,
            "grade": entry.grade,
            "previous": entry.previous,
            "direction": entry.direction,
            "rules_source": entry.rules_source,
            "reasons": list(entry.reasons),
        },
        ensure_ascii=False,
    )
    lines = [
        f"## {entry.at} — {entry.grade.upper()} ({arrow})",
        "",
        f"Rules: `{entry.rules_source}`",
        "",
    ]
    lines += [f"- {r}" for r in entry.reasons]
    # The machine-readable copy rides in an HTML comment so the file stays
    # readable prose while still round-tripping exactly. Reparsing the rendered
    # sentences instead would be a second format to keep in sync with the first.
    lines += ["", f"<!-- grade {machine} -->", ""]
    return "\n".join(lines)


def grade_history(case_id: str) -> tuple[GradeEntry, ...]:
    """Every grading recorded for this case, oldest first."""
    path = case_dir(case_id) / "conclusion.md"
    if not path.is_file():
        load_case(case_id)  # raises CaseNotFound with the teaching message
        raise CaseError(
            f"Case {case_id} has no conclusion.md. The case directory is "
            f"incomplete; restore it from your copy rather than recreating it."
        )
    out = []
    for m in _ENTRY_RE.finditer(path.read_text()):
        try:
            d = json.loads(m.group("json"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"A grade entry in {path} is corrupt: {exc}. It was "
                f"hand-edited. Restore the file — dropping the entry would "
                f"erase a grade this case actually held."
            ) from exc
        out.append(
            GradeEntry(
                at=d.get("at", ""),
                grade=d.get("grade", ""),
                previous=d.get("previous"),
                direction=d.get("direction", ""),
                rules_source=d.get("rules_source", ""),
                reasons=tuple(d.get("reasons") or ()),
            )
        )
    return tuple(out)


def conclusion_path(case_id: str) -> Path:
    return case_dir(case_id) / "conclusion.md"
