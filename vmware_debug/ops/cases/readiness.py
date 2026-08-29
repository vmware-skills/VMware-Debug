"""Readiness — what grade can this environment actually reach?

Design section 5. Answered before an investigation starts rather than after it
stalls, and answered **per evidence class**, because a single overall percentage
cannot be acted on. "Storage cases reach Probable, hardware cases reach
Candidate" tells an operator what to expect and what to go fix; "readiness 78%"
tells them nothing.

Every unavailable class carries its own remedy. A report that only lists what is
missing is a complaint; naming the next action is what makes it a plan — even
when that action is outside this system, which for the hardware class it is.
"""

from __future__ import annotations

from typing import Any

from vmware_debug.ops.cases.grading import knowledge_root
from vmware_debug.ops.cases.sources import load_catalogue

#: Classes supplied by something other than an installed skill, and how to see
#: whether they are there. Kept beside the check rather than in the catalogue:
#: the catalogue describes the world, this describes how we look at it.
_KNOWLEDGE = "knowledge"


def _knowledge_available() -> bool:
    root = knowledge_root()
    if not root.is_dir():
        return False
    return any(p.is_file() and not p.name.startswith(".") for p in root.rglob("*"))


def _class_state(name: str, spec: dict, installed: set[str] | None) -> dict[str, Any]:
    """Whether one evidence class can produce anything here, and via what."""
    if name == _KNOWLEDGE:
        ok = _knowledge_available()
        return {
            "label": spec.get("label", name),
            "available": ok,
            "degraded": False,
            "via": [f"{knowledge_root()}"] if ok else [],
            "backed_by": _KNOWLEDGE if ok else None,
            "decisive": bool(spec.get("decisive")),
            "absent_because": None if ok else spec.get("absent_because", ""),
            "how_to_supply": "" if ok else spec.get("how_to_supply", ""),
        }

    skill = spec.get("skill")
    tools = spec.get("tools") or []
    # A class with no skill at all is unavailable regardless of what is
    # installed — the hardware class is not a configuration problem.
    if not skill or not tools:
        return {
            "label": spec.get("label", name),
            "available": False,
            "degraded": False,
            "via": [],
            "backed_by": None,
            "decisive": bool(spec.get("decisive")),
            "absent_because": spec.get("absent_because", ""),
            "how_to_supply": spec.get("how_to_supply", ""),
        }

    if installed is None or skill in installed:
        return {
            "label": spec.get("label", name),
            "available": True,
            "degraded": False,
            "via": [f"{skill}:{e['tool']}" for e in tools],
            "backed_by": skill,
            "decisive": bool(spec.get("decisive")),
            "absent_because": None,
            "how_to_supply": "",
        }

    alt = spec.get("degraded_alternative") or {}
    if alt.get("skill") and alt["skill"] in installed:
        return {
            "label": spec.get("label", name),
            "available": True,
            "degraded": True,
            "via": [f"{alt['skill']}:{alt['tool']}"],
            "backed_by": alt["skill"],
            "decisive": bool(spec.get("decisive")),
            "absent_because": None,
            "how_to_supply": "",
            "note": alt.get("note", ""),
        }

    return {
        "label": spec.get("label", name),
        "available": False,
        "degraded": False,
        "via": [],
        "backed_by": None,
        "decisive": bool(spec.get("decisive")),
        "absent_because": f"{skill} is not among the available skills.",
        "how_to_supply": f"Install and configure {skill}, then re-run case_readiness.",
    }


def readiness(available_skills: list[str] | None = None) -> dict[str, Any]:
    """What each kind of investigation can conclude here.

    Args:
        available_skills: The skills actually installed and configured. ``None``
            means "assume every skill in the catalogue is available", which
            reports the ceiling imposed by the family itself rather than by this
            particular install — useful for understanding the tool, and honest
            because the two classes that cap it are absent either way.

    Returns:
        ``{"classes": {...}, "categories": {...}, "note": ...}``. Deliberately
        no overall score.
    """
    cat = load_catalogue()
    installed = set(available_skills) if available_skills is not None else None
    classes = {name: _class_state(name, spec, installed) for name, spec in cat["classes"].items()}

    categories: dict[str, Any] = {}
    for category, spec in cat["routing"].items():
        supporting = [c for c in spec.get("supporting", []) if classes[c]["available"]]
        decisive = [c for c in spec.get("decisive", []) if classes[c]["available"]]
        missing_decisive = [c for c in spec.get("decisive", []) if not classes[c]["available"]]

        # Probable costs two INDEPENDENT sources, and the grader counts those
        # as distinct source skills. So readiness must count skills too, not
        # available classes: without Log Insight, vmware-monitor supplies both
        # virtualisation state and (degraded) log scanning — two classes, one
        # skill, and the grader will only ever see one source. Counting classes
        # here promised a Probable the grader would refuse, which is the worst
        # thing a readiness report can do.
        backing = {classes[c]["backed_by"] for c in supporting}
        backing.discard(None)
        if len(backing) >= 2:
            ceiling = "confirmed" if decisive else "probable"
        else:
            ceiling = "candidate"

        categories[category] = {
            "ceiling": ceiling,
            "available_supporting": supporting,
            "independent_sources": sorted({classes[c]["backed_by"] for c in supporting} - {None}),
            "missing_supporting": [
                c for c in spec.get("supporting", []) if not classes[c]["available"]
            ],
            "available_decisive": decisive,
            "missing_decisive": missing_decisive,
        }

    return {
        "classes": classes,
        "categories": categories,
        "note": (
            "Ceilings are what the evidence available here can support, not a "
            "prediction about any one case. A category at 'candidate' is short "
            "of corroboration: it needs two independent sources, and two calls "
            "to one skill are one source. Each unavailable class above carries "
            "how_to_supply."
        ),
    }
