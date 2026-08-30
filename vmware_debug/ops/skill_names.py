"""One spelling for a skill, whichever spelling arrived.

This repo publishes two vocabularies for the same nine things. The event
envelope documents its sources as ``monitor`` / ``aria`` / ``loginsight``,
because that is what the ``source`` field of an event carries; the evidence
catalogue names them ``vmware-monitor`` / ``vmware-aria`` /
``vmware-log-insight``, because that is what a package is called. Both are
correct and neither is going away, so something has to hold them together.

Nothing did. ``case_readiness(available_skills=["monitor"])`` reported every
class unavailable and advised installing vmware-monitor, which was installed.
That is the cheap half. The expensive half is the grader: it counts independent
sources as distinct ``source_skill`` strings, so submitting ``monitor`` for one
item and ``vmware-monitor`` for another bought a promotion to Probable out of
two spellings of one skill.

Reduction rather than a lookup table, deliberately. A table is a list that has
to be maintained alongside the catalogue it mirrors, and the family has been
bitten by hand-copied lists going stale often enough to prefer a rule.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: Every skill in this family is published as ``vmware-<something>``, and the
#: short form drops it. Stripping the prefix — rather than listing the pairs —
#: is what makes a skill added tomorrow work without an edit here.
_FAMILY_PREFIX = "vmware"


def canonical_skill_key(name: str) -> str:
    """Reduce a skill name to the form used for comparison.

    Lower-cased, separators removed, and the family prefix dropped, so
    ``vmware-log-insight``, ``log-insight``, ``log_insight`` and ``loginsight``
    all reduce to ``loginsight``. Returns ``""`` for anything that reduces to
    nothing, which the callers treat as unrecognised rather than as a match.
    """
    text = _NON_ALNUM.sub("", str(name or "").lower())
    if text.startswith(_FAMILY_PREFIX) and len(text) > len(_FAMILY_PREFIX):
        return text[len(_FAMILY_PREFIX):]
    return text


def resolve_available_skills(
    names: list[str] | None, known: tuple[str, ...]
) -> tuple[set[str] | None, tuple[str, ...]]:
    """Map caller-supplied skill names onto the catalogue's own spellings.

    Args:
        names: What the caller said is installed, in any of the family's
            spellings. ``None`` means "do not narrow at all" and is passed
            straight through — it is a different statement from an empty list.
        known: The catalogue's spelling of every skill it routes to.

    Returns:
        ``(resolved, unrecognised)``. ``unrecognised`` is returned rather than
        dropped: a name nobody recognises and a skill nobody has installed are
        different situations, and collapsing them is how a typo reads as
        "install it" advice for something already installed.
    """
    if names is None:
        return None, ()

    by_key = {canonical_skill_key(k): k for k in known}
    resolved: set[str] = set()
    unrecognised: list[str] = []
    for name in names:
        match = by_key.get(canonical_skill_key(name))
        if match is None:
            unrecognised.append(name)
        else:
            resolved.add(match)
    return resolved, tuple(unrecognised)


def group_by_skill(names: list[str]) -> dict[str, tuple[str, ...]]:
    """Group submitted spellings by the skill they name, in arrival order.

    The grader counts the keys and reports the values, so a case that reached
    Probable on ``monitor`` plus ``vmware-monitor`` can be told exactly which
    two strings were treated as one source.
    """
    grouped: dict[str, list[str]] = {}
    for name in names:
        grouped.setdefault(canonical_skill_key(name), [])
        if name not in grouped[canonical_skill_key(name)]:
            grouped[canonical_skill_key(name)].append(name)
    return {key: tuple(spellings) for key, spellings in grouped.items()}
