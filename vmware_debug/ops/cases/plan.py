"""What to fetch next — step 02 of the evidence loop, recomputed each time.

Not a checklist. The plan is derived from the case as it stands: the symptom
category, the evidence classes already covered, and which sources this
installation can actually reach. Submit something and the next plan is shorter;
lose a source and the next plan routes around it.

Three shapes this module refuses:

* **Prose.** Every step is ``{skill, tool, purpose, ...}``. A model handed a
  paragraph improvises; a model handed a tool name calls the tool.
* **A silent empty list.** "Nothing left to fetch" and "nothing here can be
  fetched" are opposite situations, so the note says which one happened.
* **Omitting what cannot be reached.** A source this install lacks is reported
  with how to supply it, not quietly left out of the plan — otherwise the gap
  never becomes visible until the conclusion refuses to firm up.
"""

from __future__ import annotations

from typing import Any

from vmware_debug.ops.cases.evidence import load_evidence
from vmware_debug.ops.cases.readiness import readiness
from vmware_debug.ops.cases.sources import load_catalogue
from vmware_debug.ops.cases.store import load_case
from vmware_debug.ops.timeline import classify_symptom_matches

#: What to fetch when the symptom category is not yet known. Broad state from
#: the one skill that has it, which is what turns "something is weird" into a
#: category on the next pass rather than a dead end.
_UNCLASSIFIED_CLASSES = ("virtualization_state", "logs")


def _infer_category(scope_summary: str, routed: set[str]) -> tuple[str | None, list[dict]]:
    """Classify from the scope text, and return what decided it.

    Reuses the keyword taxonomy debug already has. A category the catalogue does
    not route is treated as unknown rather than returned — routing nowhere and
    saying "storage" would be worse than admitting we do not know.

    The signals come back with the answer because everything downstream runs off
    this one word. The same incident described five ways used to route five
    ways, each decided by whichever noun happened to be in the sentence, and the
    plan said only which category it had chosen — so the person best placed to
    notice the choice was wrong had nothing to notice it from.
    """
    signals = [
        {"category": m["category"], "matched_keywords": m["matched_keywords"]}
        for m in classify_symptom_matches(scope_summary)
        if m["category"] in routed
    ]
    return (signals[0]["category"] if signals else None), signals


#: How many steps a plan offers by default. A storage case has fourteen
#: reachable tools; handing all fourteen to a model gets all fourteen called.
#: Six is two rounds across three classes — enough to reach the two independent
#: sources Probable costs, small enough to stay a next step rather than a menu.
DEFAULT_MAX_STEPS = 6


def plan_next(
    case_id: str,
    category: str | None = None,
    available_skills: list[str] | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> dict[str, Any]:
    """The next batch of fetch instructions for one case.

    Args:
        case_id: The case to plan for.
        category: Force a symptom category. Omit to infer it from the scope.
        available_skills: Narrow to the skills actually installed.
        max_steps: Cap on steps returned, at least 1. Whatever is held back is
            counted in the result and named in the note — a silently truncated
            plan reads as a complete one.

    Raises:
        ValueError: if ``max_steps`` is below 1. Slicing would otherwise accept
            it and answer plausibly: ``[:0]`` gives an empty plan, which the
            caller reads as "nothing left to fetch", and ``[:-3]`` quietly drops
            the last three steps. Both are wrong answers wearing the shape of
            right ones.
    """
    if max_steps < 1:
        raise ValueError(
            f"max_steps must be at least 1, got {max_steps}. Omit it for the "
            f"default of {DEFAULT_MAX_STEPS}, or raise it to see the steps "
            f"counted in 'held_back'."
        )
    case = load_case(case_id)
    cat = load_catalogue()
    routing = cat["routing"]

    if category is not None and category not in routing:
        raise ValueError(
            f"No symptom category {category!r}. Known categories: "
            f"{', '.join(sorted(routing))}. Omit the argument to infer it from "
            f"the case scope."
        )

    matched, signals = _infer_category(case.scope.summary, set(routing))
    inferred = category or matched
    ready = readiness(available_skills=available_skills)
    classes = ready["classes"]

    if inferred:
        wanted = list(routing[inferred].get("supporting", []))
        decisive = list(routing[inferred].get("decisive", []))
    else:
        wanted = [c for c in _UNCLASSIFIED_CLASSES if c in classes]
        decisive = []

    covered = {e.source_skill for e in load_evidence(case_id)}
    already, unavailable = [], []
    per_class: list[list[dict]] = []

    for name in wanted:
        spec = classes[name]
        if not spec["available"]:
            unavailable.append(_unavailable(name, spec))
            continue
        # A class is "covered" when a skill that could serve it has already
        # produced evidence. Re-asking the same skill for the same class is how
        # a plan turns into busywork the model dutifully performs.
        if spec.get("backed_by") in covered:
            already.append(name)
            continue
        per_class.append(_steps_for(name, cat["classes"][name], spec, case))

    for name in decisive:
        spec = classes[name]
        if not spec["available"]:
            unavailable.append(_unavailable(name, spec, decisive=True))
        elif spec.get("backed_by") not in covered:
            per_class.append(_steps_for(name, cat["classes"][name], spec, case))

    ordered = _round_robin(per_class)
    steps, held_back = ordered[:max_steps], max(0, len(ordered) - max_steps)

    return {
        "case_id": case_id,
        "category": inferred,
        "category_signals": signals,
        "steps": steps,
        "already_covered": already,
        "held_back": held_back,
        "unavailable": unavailable,
        "ceiling": (
            ready["categories"][inferred]["ceiling"]
            if inferred
            else ready["categories"]["platform"]["ceiling"]
        ),
        "note": _note(inferred, steps, already, unavailable, held_back, signals),
    }


def _signal_sentence(category: str | None, signals: list[dict]) -> str:
    """Say which words chose the category, and which categories lost.

    Named rather than merely counted: a competing category is the one thing that
    tells a reader the routing was a judgement rather than a fact.
    """
    if not signals or signals[0]["category"] != category:
        return ""
    words = ", ".join(f"'{k}'" for k in signals[0]["matched_keywords"])
    tail = ""
    if len(signals) > 1:
        tail = (
            f" {', '.join(s['category'] for s in signals[1:])} also matched, "
            f"less strongly — pass category= to force one if this is the wrong "
            f"reading."
        )
    return f" Inferred from {words}.{tail}"


def _steps_for(name: str, catalogue_spec: dict, ready_spec: dict, case) -> list[dict]:
    """Turn one evidence class into executable fetch instructions."""
    skill = ready_spec.get("backed_by")
    window = (
        {"start": case.scope.window_start, "end": case.scope.window_end}
        if case.scope.window_start or case.scope.window_end
        else None
    )
    tools = catalogue_spec.get("tools") or []
    if ready_spec.get("degraded"):
        alt = catalogue_spec.get("degraded_alternative") or {}
        tools = [{"tool": alt.get("tool"), "gives": alt.get("note", "")}]
    return [
        {
            "evidence_class": name,
            "skill": skill,
            "tool": entry["tool"],
            "purpose": entry.get("gives", ""),
            "objects": list(case.scope.objects),
            "window": window,
            "degraded": bool(ready_spec.get("degraded")),
            "submit_with": "case_submit_evidence",
        }
        for entry in tools
        if entry.get("tool")
    ]


def _round_robin(groups: list[list[dict]]) -> list[dict]:
    """Interleave the per-class lists so every class is offered before any
    repeats. Two independent sources is what Probable costs, so breadth moves a
    case and depth in one skill does not.
    """
    out: list[dict] = []
    for i in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if i < len(group):
                out.append(group[i])
    return out


def _unavailable(name: str, spec: dict, decisive: bool = False) -> dict:
    return {
        "evidence_class": name,
        "label": spec.get("label", name),
        "decisive": decisive or bool(spec.get("decisive")),
        "why": spec.get("absent_because") or "not available here",
        "how_to_supply": spec.get("how_to_supply", ""),
        "record_as_gap": (
            "If this blocks a hypothesis, record it with case_record_gap so the "
            "grade reflects what is actually missing."
        ),
    }


def _note(category, steps, already, unavailable, held_back, signals=()) -> str:
    more = (
        f" {held_back} further step(s) are available for this category — pass a "
        f"larger max_steps to see them."
        if held_back
        else ""
    )
    if category is None:
        return (
            "No symptom category matched the scope summary, so this plan fetches "
            "broad state first. Re-run case_plan once that evidence is in — the "
            "category usually falls out of it. You can also pass one explicitly, "
            "or run list_symptom_categories to see what is recognised."
        )
    if steps:
        return (
            f"Category {category}.{_signal_sentence(category, list(signals))} Run each "
            f"step with the named skill's tool, then "
            f"submit the result with case_submit_evidence. Anything you cannot "
            f"get goes to case_record_gap — an unrecorded gap makes the case look "
            f"better supported than it is." + more
        )
    if already and not unavailable:
        return (
            f"Every reachable source for {category} has already produced evidence. "
            f"Run case_grade to see where that leaves the conclusion."
        )
    return (
        f"No further steps for {category} from this installation. What remains is "
        f"listed under 'unavailable', each with how_to_supply. Run case_grade for "
        f"the grade this evidence supports."
    )
