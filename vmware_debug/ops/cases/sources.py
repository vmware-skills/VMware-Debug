"""The evidence-source catalogue: which skill and tool answers which question.

Loaded from ``rules/evidence_sources.yaml`` rather than written into a prompt.
Routing embedded in prose drifts against the tools it names and nothing notices;
a data file can be checked against the live MCP registries, so a tool that stops
existing fails a gate instead of quietly becoming advice nobody can follow.

Two classes in the catalogue have no tools at all — hardware diagnostics and the
knowledge library. That is a fact about this family, not a gap in the file, and
it is what caps a stock install at Probable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CATALOGUE_PATH = Path(__file__).resolve().parents[2] / "rules" / "evidence_sources.yaml"


def load_catalogue() -> dict[str, Any]:
    """Parse the catalogue. A broken file is an error, never an empty routing map."""
    import yaml

    try:
        with CATALOGUE_PATH.open(encoding="utf-8") as fh:
            body = yaml.safe_load(fh)
    except OSError as exc:
        raise ValueError(
            f"Cannot read the evidence-source catalogue at {CATALOGUE_PATH}: "
            f"{exc}. It ships inside the package; reinstall vmware-debug."
        ) from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"evidence_sources.yaml is not valid YAML: {exc}.") from exc
    if not isinstance(body, dict) or not body.get("classes") or not body.get("routing"):
        raise ValueError(
            f"evidence_sources.yaml at {CATALOGUE_PATH} is missing 'classes' or "
            f"'routing'. An empty catalogue would make every readiness answer "
            f"look uniformly hopeless rather than reporting that the file is broken."
        )
    return body


def evidence_classes() -> tuple[str, ...]:
    """Every class the catalogue defines, in file order."""
    return tuple(load_catalogue()["classes"])


def tools_for_class(class_name: str) -> tuple[tuple[str, str], ...]:
    """``(skill, tool)`` pairs for one class. Empty for the two that do not exist."""
    classes = load_catalogue()["classes"]
    if class_name not in classes:
        raise ValueError(
            f"No evidence class {class_name!r}. Known classes: "
            f"{', '.join(classes)}. See evidence_classes()."
        )
    spec = classes[class_name]
    skill = spec.get("skill")
    if not skill:
        return ()
    return tuple((skill, e["tool"]) for e in spec.get("tools", []))


def all_catalogue_tools() -> tuple[tuple[str, str], ...]:
    """Every ``(skill, tool)`` the catalogue names.

    Exported for the family gate, which is the only place that can see all
    fifteen MCP registries at once and check these against them.
    """
    out: list[tuple[str, str]] = []
    for name in load_catalogue()["classes"]:
        out.extend(tools_for_class(name))
        spec = load_catalogue()["classes"][name]
        alt = spec.get("degraded_alternative")
        if alt and alt.get("skill") and alt.get("tool"):
            out.append((alt["skill"], alt["tool"]))
    return tuple(out)
