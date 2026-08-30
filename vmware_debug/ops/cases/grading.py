"""Conclusion grading — the ledger decides, not the caller.

``grade_case`` deliberately has no parameter for the grade. The model using
these tools submits evidence and records gaps; the level is recomputed from
what is on disk every time it is asked. vmware-harden v1.9.0 is the precedent:
76 of 99 compliance rules reported a pass without ever having judged the host,
because there was a route for the program to announce its own verdict. Removing
the route is more reliable than documenting that it should not be used.

The result also reports the *ceiling* — the best grade this installation could
reach at all, measured rather than asserted. On a stock install that is
Probable, because Confirmed needs a decisive source and there is neither a
hardware-diagnostic channel nor a knowledge library yet; mount one and the
ceiling rises on its own. Saying this up front beats leaving someone to wonder
why a well-supported case never gets higher.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vmware_policy.paths import ops_path

from vmware_debug.ops.cases.evidence import load_evidence, load_gaps
from vmware_debug.ops.cases.store import load_case
from vmware_debug.ops.skill_names import group_by_skill

PACKAGED_RULES = Path(__file__).resolve().parents[2] / "rules" / "grading_rules.yaml"


@dataclass(frozen=True)
class GradeResult:
    """A computed grade, with everything needed to argue with it."""

    case_id: str
    grade: str
    reasons: tuple[str, ...]
    ceiling: str
    ceiling_reasons: tuple[str, ...]
    rules_source: str
    rules_origin: str


def site_rules_path() -> Path:
    return ops_path("investigation", "grading_rules.yaml")


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    try:
        with path.open(encoding="utf-8") as fh:
            body = yaml.safe_load(fh)
    except OSError as exc:
        raise ValueError(f"Cannot read grading_rules.yaml at {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(
            f"grading_rules.yaml at {path} is not valid YAML: {exc}. Fix it "
            f"rather than deleting it — falling back to the defaults silently "
            f"would grade cases under rules nobody chose."
        ) from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValueError(
            f"grading_rules.yaml at {path} must be a mapping at the top level, "
            f"got {type(body).__name__}. Expected a 'grades:' block."
        )
    return body


def load_rules() -> tuple[dict[str, Any], str, str]:
    """Return ``(rules, source_path, origin)``.

    ``origin`` is ``packaged-default`` or ``site``. A site file replaces whole
    grade blocks; blocks it does not mention keep the packaged rule. There is
    no deep merge inside a block, so every rule in force can be read off one
    file.
    """
    packaged = _load_yaml(PACKAGED_RULES)
    site_path = site_rules_path()
    if not site_path.is_file():
        return packaged, str(PACKAGED_RULES), "packaged-default"

    site = _load_yaml(site_path)
    merged = dict(packaged)
    grades = dict(packaged.get("grades") or {})
    grades.update(site.get("grades") or {})
    merged["grades"] = grades
    return merged, str(site_path), "site"


def knowledge_root() -> Path:
    """Where KB entries, runbooks, vendor SRs and past cases are mounted."""
    return ops_path("knowledge")


def _knowledge_entries() -> int:
    """How many knowledge files are actually mounted.

    Counts files, not directories. Creating ``~/.vmware/knowledge/kb/`` and
    leaving it empty is not supplying a knowledge library, and treating the
    folder's existence as the answer would be the family's empty-result shape
    one directory up.
    """
    root = knowledge_root()
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


def _ceiling(rules: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """The best grade this installation can currently reach, and why not more.

    Measured, not asserted. Confirmed needs a decisive source; the two routes
    to one are a direct hardware diagnostic (which this family has no channel
    for) and the knowledge library (which ships empty). The library is
    countable, so the ceiling rises by itself the moment entries are mounted —
    a hardcoded "probable" would still be claiming the same limitation after
    someone had removed it.
    """
    conf = (rules.get("grades") or {}).get("confirmed") or {}
    declared = tuple(conf.get("decisive_sources") or ())
    if not declared:
        return "probable", (
            "No decisive sources are declared in the rules file, so Confirmed "
            "is unreachable by construction.",
        )

    entries = _knowledge_entries()
    if entries:
        return "confirmed", (
            f"Confirmed is reachable: {entries} entry/entries mounted under "
            f"{knowledge_root()}. An entry counts as decisive only if its "
            f"applies_to block matched this case's scope — a similar-looking "
            f"KB for the wrong build is supporting evidence, nothing more.",
        )

    return "probable", (
        f"Confirmed requires a decisive source: a direct hardware diagnostic, "
        f"a version-checked knowledge-base entry, or a vendor SR. The "
        f"knowledge library at {knowledge_root()} is empty, and this family "
        f"has no hardware-diagnostic channel (no Redfish/BMC, no SMART/NVMe), "
        f"so none of {', '.join(declared)} can produce evidence yet. The "
        f"ceiling is Probable until one of them is supplied.",
    )


def grade_case(case_id: str) -> GradeResult:
    """Recompute the conclusion grade for one case from its ledger.

    There is no way to pass in a grade. That is the design, not an oversight.
    """
    rules, source, origin = load_rules()
    grades = rules.get("grades") or {}
    evidence = load_evidence(case_id)
    gaps = load_gaps(case_id)
    # The scope carries the versions a knowledge entry's applies_to is checked
    # against; without it, an entry for the wrong build is indistinguishable
    # from the right one.
    scope = load_case(case_id).scope

    # Independence is counted per SKILL, not per string. 'monitor' and
    # 'vmware-monitor' are the same skill under two of the family's own
    # spellings, and counting them separately sells a promotion to Probable for
    # the price of a typo — corroboration invented out of orthography.
    by_skill = group_by_skill([e.source_skill for e in evidence])
    sources = set(by_skill)
    blocking = tuple(g for g in gaps if g.blocks)
    falsifiable = tuple(g for g in blocking if g.could_falsify)
    reasons: list[str] = []

    if evidence:
        submitted = sorted(name for spellings in by_skill.values() for name in spellings)
        reasons.append(
            f"{len(evidence)} evidence item(s) from {len(sources)} source(s): "
            f"{', '.join(submitted)}."
        )
        merged = {k: v for k, v in by_skill.items() if len(v) > 1}
        if merged:
            reasons.append(
                "Counted as one source each: "
                + "; ".join(f"{' and '.join(v)} are the same skill" for v in merged.values())
                + ". Corroboration means a second skill, not a second spelling."
            )
    else:
        reasons.append("No evidence recorded yet.")
    if blocking:
        reasons.append(
            "Blocking gap(s): "
            + "; ".join(
                f"{g.gap_id} {g.what} (blocks {', '.join(g.blocks)}"
                + (", could overturn it)" if g.could_falsify else ")")
                for g in blocking
            )
            + "."
        )

    excluded = _check_excluded(grades.get("excluded") or {}, evidence, sources, reasons)
    if excluded:
        grade = "excluded"
    elif _meets_probable(grades.get("probable") or {}, sources, falsifiable):
        grade = (
            "confirmed"
            if _meets_confirmed(
                grades.get("confirmed") or {},
                evidence,
                blocking,
                reasons,
                prerequisite_met=True,
                scope=scope,
            )
            else "probable"
        )
    elif _meets_confirmed(
        grades.get("confirmed") or {},
        evidence,
        blocking,
        reasons,
        prerequisite_met=False,
        scope=scope,
    ):
        # Only reachable when the rules file relaxes `confirmed.requires`.
        # Mutation-testing found that key was never read: changing it looked
        # like it worked and did nothing, which is worse than not offering it.
        grade = "confirmed"
    else:
        grade = "candidate"
        reasons.append(_why_not_probable(grades.get("probable") or {}, sources, falsifiable))

    ceiling, ceiling_reasons = _ceiling(rules)
    return GradeResult(
        case_id=case_id,
        grade=grade,
        reasons=tuple(reasons),
        ceiling=ceiling,
        ceiling_reasons=ceiling_reasons,
        rules_source=source,
        rules_origin=origin,
    )


def _meets_probable(rule: dict[str, Any], sources: set[str], falsifiable: tuple) -> bool:
    """Corroboration, and nothing outstanding that could overturn it.

    A merely missing confirmation does not block Probable — see the rules file
    for why making it block would quietly discourage recording gaps at all.
    """
    if len(sources) < int(rule.get("min_independent_sources", 2)):
        return False
    return not (rule.get("blocked_by_falsifiable_gaps", True) and falsifiable)


def _why_not_probable(rule: dict[str, Any], sources: set[str], falsifiable: tuple) -> str:
    need = int(rule.get("min_independent_sources", 2))
    if len(sources) < need:
        return (
            f"Held at Candidate: {len(sources)} independent source(s), {need} "
            f"required. Corroborate from a different skill, not another call to "
            f"the same one."
        )
    return (
        "Held at Candidate: an open gap could overturn this hypothesis "
        f"({', '.join(g.gap_id for g in falsifiable)}). Close it, or — if it "
        "genuinely cannot be closed — say so and mark it could_falsify=false, "
        "which caps the case at Probable instead of holding it here."
    )


#: Decisive sources that are knowledge entries rather than instrument readings.
#: These are the ones whose applicability has to be proved: a hardware
#: diagnostic is a measurement of THIS machine, while a knowledge-base entry is
#: a claim about some machine, and which one is the whole question.
_KNOWLEDGE_SOURCES = frozenset({"knowledge-kb", "knowledge-sr"})


def _applicable(hits: tuple, scope, reasons: list[str]) -> tuple:
    """Drop knowledge evidence whose ``applies_to`` does not match this case.

    An entry that is real, well-formed and about the right product but written
    for a different build is indistinguishable from a correct one by similarity
    — and similarity was all that stood between it and a Confirmed, which is the
    outcome these rules exist to prevent.

    Hardware diagnostics are exempt: a reading taken from this machine has no
    applicability left to establish.
    """
    from pathlib import Path

    from vmware_debug.ops.cases.knowledge import applies_to_scope, load_knowledge

    if scope is None or not any(e.source_skill in _KNOWLEDGE_SOURCES for e in hits):
        return hits

    entries = {e.entry_id: e for e in load_knowledge()}
    by_filename = {Path(e.path).name: e for e in entries.values()}

    kept: list = []
    dropped: list[str] = []
    for item in hits:
        if item.source_skill not in _KNOWLEDGE_SOURCES:
            kept.append(item)
            continue
        cited = item.knowledge_entry_id
        if not cited:
            dropped.append(
                f"{item.evidence_id} does not say which entry it is, so its "
                f"applicability could not be checked"
            )
            continue
        entry = entries.get(cited) or by_filename.get(cited)
        if entry is None:
            dropped.append(
                f"{item.evidence_id} cites {cited!r}, which is not mounted under the knowledge root"
            )
            continue
        verdict = applies_to_scope(entry, scope)
        if verdict.decisive:
            kept.append(item)
        else:
            dropped.append(f"{item.evidence_id} cites {cited}: {verdict.reason}")

    if dropped:
        reasons.append(
            "Knowledge evidence not counted as decisive — "
            + "; ".join(dropped)
            + ". Matching is by applies_to against the case scope, never by "
            "similarity: an entry written for the wrong build reads exactly "
            "like the right one."
        )
    return tuple(kept)


def _meets_confirmed(
    rule: dict[str, Any],
    evidence: tuple,
    blocking: tuple,
    reasons: list[str],
    prerequisite_met: bool,
    scope=None,
) -> bool:
    """Decisive evidence, no hole of either kind, and the prerequisite grade.

    ``prerequisite_met`` says whether the case already reached the grade named
    by ``confirmed.requires``. The packaged rules say ``probable``, so a single
    vendor SR cannot carry a case alone; a site that lowers it to ``candidate``
    genuinely gets the looser behaviour, which is the point of it being in a
    file the customer can read.
    """
    requires = str(rule.get("requires", "probable")).lower()
    valid = {"candidate", "probable"}
    if requires not in valid:
        raise ValueError(
            f"grading_rules.yaml: confirmed.requires is {requires!r}; expected "
            f"one of {sorted(valid)}. A prerequisite nobody recognises would be "
            f"silently ignored, and the file would describe rules that are not "
            f"the ones in force."
        )
    if requires == "probable" and not prerequisite_met:
        return False
    if rule.get("blocked_by_gaps", True) and blocking:
        reasons.append(
            "Held at Probable rather than Confirmed: "
            f"{', '.join(g.gap_id for g in blocking)} still open. Confirmed "
            "asserts nothing is missing."
        )
        return False
    decisive_names = set(rule.get("decisive_sources") or ())
    hits = tuple(e for e in evidence if e.source_skill in decisive_names)
    hits = _applicable(hits, scope, reasons)
    if len(hits) < int(rule.get("min_decisive_sources", 1)):
        return False
    reasons.append(
        "Decisive evidence: " + ", ".join(f"{e.evidence_id} ({e.source_skill})" for e in hits) + "."
    )
    return True


def _check_excluded(
    rule: dict[str, Any], evidence: tuple, sources: set[str], reasons: list[str]
) -> bool:
    """Exclusion requires an observation that rules the hypothesis out.

    Not a missing observation. "We looked and found nothing" is a gap, and
    reading a gap as an exclusion is the family's empty-result-means-no-problem
    failure in the one place where it would change the answer.
    """
    if not rule.get("requires_falsifying_evidence", True):
        return False
    falsifying = tuple(e for e in evidence if e.falsifies)
    if not falsifying:
        return False
    if len(sources) < int(rule.get("min_independent_sources", 2)):
        return False
    reasons.append(
        "Falsifying observation(s): "
        + ", ".join(f"{e.evidence_id} rules out {', '.join(e.falsifies)}" for e in falsifying)
        + "."
    )
    return True
