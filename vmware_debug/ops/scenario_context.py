"""Prepare task-scoped tool context; dispatch authorization stays at the backend."""

from __future__ import annotations

from copy import deepcopy
import re


class ContextUnavailable(ValueError):
    """The scenario cannot be prepared from the declared capabilities/permissions."""


def prepare_context(
    profile: dict, manifests: dict, allowed: dict, targets: dict, allowed_targets: dict
) -> dict:
    """Intersect an explicit recipe with live schemas and host declarations.

    No backend calls, discovery guesses, file reads, credential handling or tool
    execution. Host permission declarations are not an independent RBAC check.
    """
    if not all(
        isinstance(value, dict) for value in (profile, manifests, allowed, targets, allowed_targets)
    ):
        raise ContextUnavailable("Explicit context dictionaries required")
    scenario = profile.get("id")
    reference = profile.get("procedure")
    if not isinstance(scenario, str) or not re.fullmatch(r"[a-z0-9-]{1,80}", scenario):
        raise ContextUnavailable("Explicit scenario ID required")
    if not isinstance(reference, str) or not re.fullmatch(r"[a-z0-9-]+\.md", reference):
        raise ContextUnavailable("Canonical reference basename required")
    required = profile.get("tools")
    if not isinstance(required, dict) or not required:
        raise ContextUnavailable("Scenario has no required capabilities")
    selected = []
    for backend, names in required.items():
        if (
            not isinstance(backend, str)
            or not re.fullmatch(r"[a-z0-9-]{1,80}", backend)
            or not isinstance(names, list)
            or not names
            or any(
                not isinstance(n, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", n)
                for n in names
            )
            or len(set(names)) != len(names)
        ):
            raise ContextUnavailable("Invalid or duplicate capability list")
        permissions = allowed.get(backend, ())
        permitted_targets = allowed_targets.get(backend, ())
        sequences = (list, tuple, set, frozenset)
        if (
            not isinstance(permissions, sequences)
            or not isinstance(permitted_targets, sequences)
            or any(not isinstance(v, str) for v in permissions)
            or any(not isinstance(v, str) for v in permitted_targets)
            or not isinstance(manifests.get(backend, {}), dict)
        ):
            raise ContextUnavailable("Explicit permission/target collections and manifest required")
        target = targets.get(backend)
        if not isinstance(target, str) or not target or target not in permitted_targets:
            raise ContextUnavailable("Explicit permitted target required for each backend")
        for name in names:
            tool = manifests.get(backend, {}).get(name)
            if name not in permissions or not isinstance(tool, dict):
                raise ContextUnavailable("Required capability unavailable or not permitted")
            annotations = tool.get("annotations")
            if (
                tool.get("name") != name
                or not isinstance(annotations, dict)
                or annotations.get("readOnlyHint") is not True
            ):
                raise ContextUnavailable("Read-only capability declaration required")
            schema = tool.get("inputSchema")
            if (
                not isinstance(schema, dict)
                or schema.get("type") != "object"
                or not isinstance(schema.get("properties"), dict)
                or "target" not in schema["properties"]
            ):
                raise ContextUnavailable("Live object argument schema required")
            selected.append(
                {"backend": backend, "name": name, "target": target, "tool": deepcopy(tool)}
            )
    return {
        "scenario_id": scenario,
        "reference": reference,
        "tools": selected,
        "authorization": "Host declaration only; backend Policy/RBAC/audit still apply",
    }
