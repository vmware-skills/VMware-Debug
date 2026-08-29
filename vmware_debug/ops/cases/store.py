"""Reading and writing the case directory.

The directory is the source of truth and the deliverable. ``case.json`` is an
index for fast listing; if it ever disagrees with the text files, the text files
win, because those are what a human audits.

Nothing here touches a VMware environment or holds a credential. That is the
point of the design: a case folder can be reopened and re-argued on a laptop
with no access to anything.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from vmware_policy.paths import ops_path

from vmware_debug.ops.cases.ids import validate_case_id
from vmware_debug.ops.cases.model import Case, Scope

_SKELETON_MD = {
    "timeline.md": (
        "# Timeline\n\n"
        "_Empty. Populated at step 04/05, once evidence has been submitted._\n\n"
        "## Trigger\n\n## Symptom\n\n## Propagation\n\n## Recovery\n"
    ),
    "hypotheses.md": (
        "# Hypotheses\n\n"
        "_Empty. Each hypothesis records its supporting evidence, its "
        "counter-evidence, the gaps that block it, and the next step._\n"
    ),
    "conclusion.md": (
        "# Conclusion\n\n"
        "_Not graded yet._\n\n"
        "The grade is computed from the ledger by `case_grade`; it is not "
        "written here by hand. Grade history, including any demotion, is "
        "appended below and never rewritten.\n"
    ),
}


class CaseError(Exception):
    """Base class for case-store failures."""


class CaseNotFound(CaseError):
    """No case with that id. Deliberately not an empty case."""


class CaseExists(CaseError):
    """A case with that id is already on disk."""


def cases_root() -> Path:
    """Where cases live. Honours ``OPS_HOME`` so a team can point this at a
    share or a ticket-system mount and hand the folder over as-is."""
    return ops_path("cases")


def case_dir(case_id: str) -> Path:
    """Resolve one case's directory. Validates before touching the path."""
    return cases_root() / validate_case_id(case_id)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _read_json(path: Path, what: str) -> dict:
    """Read one JSON file, or explain which file is broken.

    A corrupt ledger file is reported, never defaulted away: a case that
    silently reads back as empty is the family's most costly failure shape, and
    it would be at its most costly here.
    """
    try:
        raw = path.read_text()
    except OSError as exc:
        raise CaseError(
            f"Cannot read {what} at {path}: {exc}. The case directory may have "
            f"been moved or its permissions changed."
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{what} is not valid JSON ({path}): {exc}. It was hand-edited or "
            f"truncated. Restore it from your copy of the case folder rather "
            f"than deleting it — the rest of the case is still intact."
        ) from exc


def create_case(scope: Scope, at: str) -> Case:
    """Open a case: write the skeleton and return the loaded record.

    Args:
        scope: Step 01's output. Validated by :class:`Scope` itself.
        at: ISO-8601 instant, supplied by the caller so this stays pure.

    Raises:
        CaseExists: if the id is taken. Never overwrites — the second open of
            the same summary in the same second is a mistake worth surfacing,
            and silently replacing a ledger would destroy the evidence it holds.
    """
    from vmware_debug.ops.cases.ids import new_case_id  # local: keeps ids leaf-level

    case_id = new_case_id(scope.summary, at=at)
    d = cases_root() / case_id
    if d.exists():
        raise CaseExists(
            f"Case {case_id} already exists at {d}. Two cases opened for the "
            f"same summary within the same second collide on the id. Use "
            f"case_get to look at the existing one, or open the new case with "
            f"a summary that names what makes it different."
        )

    d.mkdir(parents=True)
    os.chmod(d, 0o700)
    (d / "evidence").mkdir()

    _write_json(d / "scope.json", scope.to_json())
    (d / "plan.jsonl").write_text("")
    _write_json(d / "gaps.json", {"gaps": []})
    for name, body in _SKELETON_MD.items():
        (d / name).write_text(body)
    _write_json(
        d / "case.json",
        {"case_id": case_id, "state": "open", "opened_at": at, "grade": None},
    )
    return Case(case_id=case_id, scope=scope, state="open", opened_at=at)


def load_case(case_id: str) -> Case:
    """Load one case. Raises :class:`CaseNotFound` rather than inventing one."""
    d = case_dir(case_id)
    if not d.is_dir():
        raise CaseNotFound(
            f"No case {case_id!r} under {cases_root()}. Run case_list to see "
            f"the ids that exist, or case_open to start one. (If you expected "
            f"it here, check OPS_HOME — cases follow it.)"
        )
    scope = Scope.from_json(_read_json(d / "scope.json", "scope.json"))
    index = _read_json(d / "case.json", "case.json")
    return Case(
        case_id=case_id,
        scope=scope,
        state=index.get("state", "open"),
        opened_at=index.get("opened_at", ""),
        grade=index.get("grade"),
    )


def list_cases() -> tuple[Case, ...]:
    """Every case, newest first.

    One unreadable entry does not take the listing down, and does not disappear
    from it either: it comes back with ``state="unreadable"`` so that a folder
    someone broke is visible as broken rather than absent.
    """
    root = cases_root()
    if not root.is_dir():
        return ()
    out: list[Case] = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        try:
            out.append(load_case(d.name))
        except (CaseError, ValueError):
            out.append(
                Case(
                    case_id=d.name,
                    scope=Scope(summary=d.name, determined_by="unreadable"),
                    state="unreadable",
                    opened_at="",
                )
            )
    return tuple(out)
