"""The evidence ledger and the gap ledger.

Step 02/03 of the eight-step loop. Every item records where it came from, the
exact query that produced it, when it was fetched, and — separately — the window
the data itself covers. The last distinction is the one the family's read tools
do not currently report and the one timeline correlation depends on: a
``list_events(hours=24)`` run at 10:00 and the same call at 18:00 answer
different questions.

The gap ledger is the other half. A fetch that failed, returned nothing, or was
refused is written down with what it blocks and how to close it. Dropping it
would leave a case that reads as better-supported than it is, which is the one
outcome this whole layer exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vmware_debug.ops.cases.store import CaseError, CaseNotFound, case_dir, cases_root

_GAPS = "gaps.json"


class EvidenceConflict(CaseError):
    """Two writers claimed the same evidence id.

    A domain error rather than the builtin ``FileExistsError``: the MCP layer
    passes deliberate exceptions through and reduces everything else to
    "operation failed", so a message worth reading has to be raised as one of
    ours. Reaching for a broad builtin base instead is what once swallowed nine
    repos' password-error guidance.
    """


def _require(value: str, field_name: str, why: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Evidence field {field_name!r} is required and cannot be blank. {why}")
    return value.strip()


@dataclass(frozen=True)
class Evidence:
    """One retrieved fact, with everything needed to re-fetch and re-judge it."""

    source_skill: str
    source_tool: str
    query: dict[str, Any]
    fetched_at: str
    summary: str
    window_start: str | None = None
    window_end: str | None = None
    time_source: str | None = None
    clock_skew_s: float | None = None
    #: Hypothesis ids this observation rules out. Non-empty is what separates
    #: "we looked and found nothing" (a gap) from "we found the thing that
    #: proves it was not this" (an exclusion). Only the latter can exclude.
    falsifies: tuple[str, ...] = ()
    #: Which knowledge entry this item IS, when it came from the knowledge
    #: layer. Required for such an item to be decisive: "some applicable entry
    #: is mounted somewhere" is a different claim from "this one applies", and
    #: only the second can carry a conclusion.
    knowledge_entry_id: str | None = None
    evidence_id: str = ""

    def __post_init__(self) -> None:
        _require(
            self.source_skill,
            "source_skill",
            "An item that cannot name the skill it came from cannot be "
            "re-fetched, and cannot be weighed against a conflicting one.",
        )
        _require(
            self.source_tool,
            "source_tool",
            "Name the tool, not just the skill — 'vmware-monitor said so' is not reproducible.",
        )
        _require(
            self.fetched_at,
            "fetched_at",
            "Without a fetch time there is no way to tell stale evidence "
            "from fresh, which matters most during an active incident.",
        )

    def to_json(self) -> dict[str, Any]:
        # Every key is present even when the value is unknown. An absent key is
        # exactly what a model fills in with invention; an explicit null is a
        # statement that nobody knows.
        return {
            "evidence_id": self.evidence_id,
            "source_skill": self.source_skill,
            "source_tool": self.source_tool,
            "query": dict(self.query),
            "fetched_at": self.fetched_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "time_source": self.time_source,
            "clock_skew_s": self.clock_skew_s,
            "falsifies": list(self.falsifies),
            "knowledge_entry_id": self.knowledge_entry_id,
            "summary": self.summary,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Evidence":
        return Evidence(
            evidence_id=d.get("evidence_id", ""),
            source_skill=d.get("source_skill", ""),
            source_tool=d.get("source_tool", ""),
            query=dict(d.get("query") or {}),
            fetched_at=d.get("fetched_at", ""),
            window_start=d.get("window_start"),
            window_end=d.get("window_end"),
            time_source=d.get("time_source"),
            clock_skew_s=d.get("clock_skew_s"),
            falsifies=tuple(d.get("falsifies") or ()),
            knowledge_entry_id=d.get("knowledge_entry_id"),
            summary=d.get("summary", ""),
        )


@dataclass(frozen=True)
class Gap:
    """Something the investigation could not get, and what to do about it."""

    what: str
    why: str
    blocks: tuple[str, ...] = ()
    #: Would obtaining this observation be able to prove the hypothesis WRONG?
    #:
    #: Most gaps are missing corroboration — the SMART reading that would have
    #: clinched a failing device. Those cap a case below Confirmed but leave the
    #: evidence that does exist standing. A gap that could overturn the
    #: hypothesis is different in kind: claiming Probable while it is open
    #: claims a check nobody ran, so it holds the case at Candidate.
    could_falsify: bool = False
    how_to_close: str = ""
    gap_id: str = ""

    def __post_init__(self) -> None:
        _require(self.what, "what", "Say which observation is missing.")
        _require(self.why, "why", "Say why it could not be obtained.")
        _require(
            self.how_to_close,
            "how_to_close",
            "A gap with no stated next action reads like a to-do and gets "
            "skipped. Name who or what would close it, even if that is "
            "outside this system.",
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "what": self.what,
            "why": self.why,
            "blocks": list(self.blocks),
            "could_falsify": self.could_falsify,
            "how_to_close": self.how_to_close,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Gap":
        return Gap(
            gap_id=d.get("gap_id", ""),
            what=d.get("what", ""),
            why=d.get("why", ""),
            blocks=tuple(d.get("blocks") or ()),
            could_falsify=bool(d.get("could_falsify", False)),
            how_to_close=d.get("how_to_close", ""),
        )


def _case_dir_or_raise(case_id: str) -> Path:
    d = case_dir(case_id)
    if not d.is_dir():
        raise CaseNotFound(
            f"No case {case_id!r} under {cases_root()}. Open it with case_open "
            f"before submitting evidence, or run case_list to find the right id."
        )
    return d


def _next_id(existing: list[str], prefix: str) -> str:
    """Continue the sequence from what is on disk, not from a counter in memory.

    Two processes appending at once could still collide; the writer below
    refuses to overwrite, so a collision surfaces as an error rather than as a
    lost item.
    """
    used = [int(x[1:]) for x in existing if x[:1] == prefix and x[1:].isdigit()]
    return f"{prefix}{max(used, default=0) + 1:03d}"


def record_evidence(case_id: str, evidence: Evidence, payload: Any = None) -> Evidence:
    """Append one item to the evidence ledger and return it with its id."""
    # A `falsifies` id nothing recognises would exclude nothing and say nothing,
    # so the reference is checked before the item is written.
    from vmware_debug.ops.cases.hypotheses import require_hypotheses

    require_hypotheses(case_id, evidence.falsifies)
    d = _case_dir_or_raise(case_id) / "evidence"
    d.mkdir(exist_ok=True)
    stems = [p.stem for p in d.glob("E*.json")]
    stamped = _with_id(evidence, _next_id(stems, "E"))

    path = d / f"{stamped.evidence_id}.json"
    if path.exists():
        raise EvidenceConflict(
            f"Evidence {stamped.evidence_id} already exists in case {case_id}. "
            f"Two writers appended at once; re-run the submission."
        )
    body = stamped.to_json()
    if payload is not None:
        body["payload"] = payload
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stamped


def _with_id(evidence: Evidence, evidence_id: str) -> Evidence:
    """Return a copy carrying the assigned id. Never mutates the caller's."""
    return Evidence(
        source_skill=evidence.source_skill,
        source_tool=evidence.source_tool,
        query=dict(evidence.query),
        fetched_at=evidence.fetched_at,
        summary=evidence.summary,
        window_start=evidence.window_start,
        window_end=evidence.window_end,
        time_source=evidence.time_source,
        clock_skew_s=evidence.clock_skew_s,
        falsifies=tuple(evidence.falsifies),
        knowledge_entry_id=evidence.knowledge_entry_id,
        evidence_id=evidence_id,
    )


def load_evidence(case_id: str) -> tuple[Evidence, ...]:
    """Every recorded item, in id order."""
    d = _case_dir_or_raise(case_id) / "evidence"
    out = []
    for path in sorted(d.glob("E*.json")):
        try:
            out.append(Evidence.from_json(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Evidence file {path.name} in case {case_id} is unreadable: "
                f"{exc}. Fix or remove that one file; the rest of the ledger "
                f"is intact. It is not treated as absent evidence."
            ) from exc
    return tuple(out)


def record_gap(case_id: str, gap: Gap) -> Gap:
    """Append one gap. Gaps accumulate; recording never replaces the list."""
    # A `blocks` id nothing recognises would hold up nothing and say nothing,
    # so the reference is checked before the gap is written.
    from vmware_debug.ops.cases.hypotheses import require_hypotheses

    require_hypotheses(case_id, gap.blocks)
    d = _case_dir_or_raise(case_id)
    existing = load_gaps(case_id)
    stamped = Gap(
        what=gap.what,
        why=gap.why,
        blocks=tuple(gap.blocks),
        could_falsify=gap.could_falsify,
        how_to_close=gap.how_to_close,
        gap_id=_next_id([g.gap_id for g in existing], "G"),
    )
    payload = {"gaps": [g.to_json() for g in existing] + [stamped.to_json()]}
    (d / _GAPS).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stamped


def load_gaps(case_id: str) -> tuple[Gap, ...]:
    """Every recorded gap.

    An empty result means *no gap has been recorded*, which is not the same as
    *nothing was missing*. Callers that grade a conclusion must not read it as
    the latter.
    """
    path = _case_dir_or_raise(case_id) / _GAPS
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read {_GAPS} for case {case_id}: {exc}.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{_GAPS} in case {case_id} is not valid JSON: {exc}. It was "
            f"hand-edited. Restore it rather than deleting it — an empty gap "
            f"list would make this case look better supported than it is."
        ) from exc
    return tuple(Gap.from_json(g) for g in body.get("gaps", []))
