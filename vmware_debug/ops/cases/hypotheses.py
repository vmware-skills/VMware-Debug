"""The hypothesis ledger — step 06, and the registry everything else assumed.

Gaps carry ``blocks=["H1"]`` and evidence carries ``falsifies=["H1"]``. Until
this module existed, nothing created H1: a mistyped id silently blocked nothing
and silently falsified nothing, so the grade came out a level higher than the
investigator meant and no output said why. A dangling identifier is the
family's empty-result shape wearing a name.

The ledger itself is computed, never asserted. A hypothesis does not get to
claim it is well supported; what points at it decides, the same way a case does
not get to state its own grade.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from vmware_debug.ops.cases.store import CaseError, case_dir, cases_root

_FILE = "hypotheses.md"
_PLACEHOLDER_MARK = "_Empty."

#: Machine-readable copy rides in an HTML comment so the file stays prose a
#: customer can read while still round-tripping exactly — the same device
#: conclusion.md uses for grade history.
_ENTRY = "<!-- hypothesis {} -->"


class HypothesisNotFound(CaseError):
    """A gap or an observation referenced a hypothesis that was never registered."""


@dataclass(frozen=True)
class Hypothesis:
    """One candidate explanation."""

    hypothesis_id: str
    statement: str
    at: str = ""


def _path(case_id: str):
    d = case_dir(case_id)
    if not d.is_dir():
        raise CaseError(
            f"No case {case_id!r} under {cases_root()}. Open it with case_open, "
            f"or run case_list to find the right id."
        )
    return d / _FILE


def load_hypotheses(case_id: str) -> tuple[Hypothesis, ...]:
    """Every registered hypothesis, in registration order."""
    path = _path(case_id)
    try:
        body = path.read_text()
    except OSError as exc:
        raise CaseError(f"Cannot read {_FILE} for case {case_id}: {exc}") from exc
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not (line.startswith("<!-- hypothesis ") and line.endswith("-->")):
            continue
        raw = line[len("<!-- hypothesis ") : -len("-->")].strip()
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"A hypothesis entry in {path} is corrupt: {exc}. It was "
                f"hand-edited. Restore the file — dropping the entry would erase "
                f"a hypothesis the gaps and evidence still reference."
            ) from exc
        out.append(
            Hypothesis(
                hypothesis_id=d.get("id", ""),
                statement=d.get("statement", ""),
                at=d.get("at", ""),
            )
        )
    return tuple(out)


def add_hypothesis(case_id: str, statement: str, at: str = "") -> Hypothesis:
    """Register a candidate explanation and return it with its id."""
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError(
            "A hypothesis needs a statement — what you think happened, in one "
            "line. An unnamed hypothesis cannot be supported, refuted or "
            "reported against."
        )
    path = _path(case_id)
    existing = load_hypotheses(case_id)
    h = Hypothesis(
        hypothesis_id=f"H{len(existing) + 1}",
        statement=statement.strip(),
        at=at,
    )
    body = path.read_text().replace(_PLACEHOLDER_MARK, "").rstrip("\n")
    machine = json.dumps(
        {"id": h.hypothesis_id, "statement": h.statement, "at": h.at},
        ensure_ascii=False,
    )
    path.write_text(f"{body}\n\n## {h.hypothesis_id} — {h.statement}\n\n{_ENTRY.format(machine)}\n")
    return h


def require_hypotheses(case_id: str, ids: tuple[str, ...] | list[str]) -> None:
    """Refuse a reference to a hypothesis that was never registered.

    Called by the gap and evidence writers. Ignoring an unknown id would make a
    typo cost a grade level with nothing in the output to explain it, which is
    precisely what this module was added to stop.
    """
    if not ids:
        return
    known = {h.hypothesis_id for h in load_hypotheses(case_id)}
    unknown = [i for i in ids if i not in known]
    if not unknown:
        return
    have = ", ".join(sorted(known)) if known else "none registered yet"
    raise HypothesisNotFound(
        f"Unknown hypothesis id(s): {', '.join(unknown)}. This case has: {have}. "
        f"Register one with case_hypotheses(statement=...) before referring to "
        f"it — an id nothing recognises would silently block and falsify "
        f"nothing, which reads as a stronger case than you have."
    )


def hypothesis_ledger(case_id: str) -> list[dict[str, Any]]:
    """Each hypothesis with what points at it, and what to do next.

    Status is derived: an observation that rules a hypothesis out settles it, so
    ``refuted`` outranks ``blocked`` — once the answer is known, a missing
    measurement no longer matters.
    """
    from vmware_debug.ops.cases.evidence import load_evidence, load_gaps

    evidence = load_evidence(case_id)
    gaps = load_gaps(case_id)
    out: list[dict[str, Any]] = []
    for h in load_hypotheses(case_id):
        refuted = [e.evidence_id for e in evidence if h.hypothesis_id in e.falsifies]
        blocking = [g for g in gaps if h.hypothesis_id in g.blocks]
        if refuted:
            status, steps = (
                "refuted",
                ["Ruled out. Record the remaining candidates, or open a new one."],
            )
        elif blocking:
            status = "blocked"
            steps = [g.how_to_close for g in blocking if g.how_to_close]
        else:
            status = "open"
            steps = [
                "Nothing is blocking this one. Run case_plan for the next "
                "evidence to fetch, and record what you cannot get as a gap."
            ]
        out.append(
            {
                "hypothesis_id": h.hypothesis_id,
                "statement": h.statement,
                "status": status,
                "refuted_by": refuted,
                "blocked_by": [g.gap_id for g in blocking],
                "next_steps": steps,
            }
        )
    return out
