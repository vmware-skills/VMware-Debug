"""The knowledge layer: what can be mounted, and what makes an entry decisive.

An entry is decisive **only if its ``applies_to`` was checked against this
case's scope and passed.** That single rule is the reason this module exists.
Before it, ``applies_to`` lived in prose: nothing parsed a knowledge file and
nothing checked applicability, so any file at all raised the reported ceiling
and anything submitted as ``knowledge-kb`` counted as decisive. A knowledge-base
entry that looks right for the wrong build is indistinguishable from a correct
one by similarity — and similarity was all that stood in the way of a wrong
Confirmed.

Silence is not a match. A constraint the scope cannot answer — a firmware
version nobody recorded — leaves the entry supporting, never decisive. Reading
an unanswerable constraint as satisfied is how a KB for different hardware ends
a case.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vmware_policy.paths import ops_path

#: Every format the layer reads, and how each carries its metadata. Data rather
#: than prose so a tool can hand the list to whoever is mounting a library —
#: "which formats do you take" is the first question anyone asks, and an answer
#: that lives only in a design document does not reach them.
SUPPORTED_FORMATS: tuple[dict[str, Any], ...] = (
    {
        "extensions": [".md", ".markdown"],
        "how_metadata_travels": "YAML front-matter between --- fences, body below",
        "note": "Preferred. Keeps the prose readable and the constraints machine-checkable.",
    },
    {
        "extensions": [".yaml", ".yml"],
        "how_metadata_travels": "the whole file is one entry",
        "note": "Best for rule-shaped entries with little prose.",
    },
    {
        "extensions": [".json"],
        "how_metadata_travels": "the whole file is one entry",
        "note": "One entry per file.",
    },
    {
        "extensions": [".jsonl"],
        "how_metadata_travels": "one JSON object per line",
        "note": "What ticketing systems usually export. One entry per line.",
    },
    {
        "extensions": [".csv", ".tsv"],
        "how_metadata_travels": "one entry per row; column names become fields",
        "note": (
            "Good for a tabular index. Nested constraints (driver, firmware) "
            "cannot be expressed — use dotted columns such as driver.version."
        ),
    },
    {
        "extensions": [".txt", ".log"],
        "how_metadata_travels": "a sibling <name>.yaml alongside the text file",
        "note": (
            "For text that cannot carry front-matter. Without the sibling the "
            "entry has no applies_to and can never be decisive."
        ),
    },
)

#: Formats that must be converted first. Named so the answer to "why is my PDF
#: ignored" is in the tool output rather than in someone's memory.
NEEDS_CONVERSION: tuple[str, ...] = (
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".html",
    ".htm",
    ".rtf",
    ".odt",
)

_READABLE = {e for f in SUPPORTED_FORMATS for e in f["extensions"]}

#: Subdirectories the layer expects. Anything else is still read; these are what
#: `how_to_supply` tells people to create.
SECTIONS: tuple[str, ...] = ("kb", "runbook", "sr", "cases")

_RANGE = re.compile(r"(>=|<=|>|<|==|=)?\s*([0-9][0-9A-Za-z.\-]*)")

#: Constraint keys the checker can actually evaluate. Anything else in an
#: ``applies_to`` block leaves the entry non-decisive.
#:
#: The first version checked these four and treated every other key as
#: satisfied, so `build` without a `product`, a `hardware_model` list, and an
#: outright typo all came back decisive: the entry stated a condition, nothing
#: verified it, and the answer was yes. "Unknown constraint" and "satisfied
#: constraint" must never be the same answer — that is the failure this layer
#: exists to close, and enumerating the keys is what keeps a new one from
#: reopening it by default.
_UNDERSTOOD = frozenset({"product", "build", "driver", "firmware"})

#: Keys that carry no constraint and so need no evaluation.
_METADATA_KEYS = frozenset({"note", "notes", "source", "url", "reference"})


def knowledge_root() -> Path:
    return ops_path("knowledge")


@dataclass(frozen=True)
class KnowledgeEntry:
    """One mounted item."""

    entry_id: str
    path: str
    body: str
    applies_to: dict[str, Any] = field(default_factory=dict)
    source: str = "kb"


@dataclass(frozen=True)
class Applicability:
    """Whether an entry may carry a conclusion here, and why."""

    decisive: bool
    supporting: bool
    reason: str


def _version_tuple(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"[.\-]", str(v)))


def _satisfies(actual: str, constraint: str) -> bool:
    """Does ``actual`` satisfy a comma-separated range like ``>=8.0, <9.0``?"""
    for clause in str(constraint).split(","):
        clause = clause.strip()
        if not clause:
            continue
        m = _RANGE.fullmatch(clause)
        if not m:
            return False
        op, want = m.group(1) or "==", m.group(2)
        a, b = _version_tuple(actual), _version_tuple(want)
        try:
            ok = {
                ">=": a >= b,
                "<=": a <= b,
                ">": a > b,
                "<": a < b,
                "==": a == b,
                "=": a == b,
            }[op]
        except TypeError:
            # Mixed int/str components cannot be ordered; fall back to equality
            # rather than guessing, so an odd version string is a non-match
            # instead of an accidental pass.
            ok = str(actual) == str(want)
        if not ok:
            return False
    return True


def applies_to_scope(entry: KnowledgeEntry, scope) -> Applicability:
    """Check one entry's constraints against a case's recorded versions.

    A mismatched entry stays *supporting*: it may still be worth reading, it
    just cannot carry a conclusion.
    """
    constraints = entry.applies_to or {}
    if not constraints:
        return Applicability(
            decisive=False,
            supporting=True,
            reason=(
                "No applies_to block, so its applicability was never checked. "
                "It can support a hypothesis but cannot make a case Confirmed — "
                "add product/build/driver/firmware constraints to change that."
            ),
        )

    unchecked = sorted(set(constraints) - _UNDERSTOOD - _METADATA_KEYS)
    if unchecked:
        return Applicability(
            decisive=False,
            supporting=True,
            reason=(
                f"The entry constrains {', '.join(unchecked)}, which this "
                f"checker could not evaluate — not contradicted, not checked. "
                f"An unverified constraint cannot count as satisfied. "
                f"Understood keys: {', '.join(sorted(_UNDERSTOOD))}."
            ),
        )

    versions = dict(getattr(scope, "product_versions", {}) or {})
    product = constraints.get("product")
    if "build" in constraints and not product:
        return Applicability(
            decisive=False,
            supporting=True,
            reason=(
                "The entry constrains 'build' without naming a product, so "
                "there is nothing to compare it against — the constraint could "
                "not be checked."
            ),
        )
    if product and product not in versions:
        return Applicability(
            decisive=False,
            supporting=True,
            reason=(
                f"Entry applies to {product!r}, and this case records no version "
                f"for it (scope has: {', '.join(sorted(versions)) or 'nothing'})."
            ),
        )

    if product and "build" in constraints:
        actual = versions[product]
        if not _satisfies(actual, constraints["build"]):
            return Applicability(
                decisive=False,
                supporting=True,
                reason=(
                    f"{product} {actual} is outside the entry's range {constraints['build']!r}."
                ),
            )

    for kind in ("driver", "firmware"):
        spec = constraints.get(kind)
        if not isinstance(spec, dict):
            continue
        key = f"{kind}.{spec.get('name') or spec.get('vendor') or ''}".rstrip(".")
        actual = versions.get(key)
        if actual is None:
            return Applicability(
                decisive=False,
                supporting=True,
                reason=(
                    f"The entry constrains {kind} {key.split('.', 1)[-1]!r}, and "
                    f"this case records no version for it. A constraint the "
                    f"scope cannot answer is not a match — reading silence as a "
                    f"pass is how an entry for different hardware ends a case."
                ),
            )
        want = spec.get("version")
        if want and not _satisfies(actual, want):
            return Applicability(
                decisive=False,
                supporting=True,
                reason=f"{key} {actual} is outside the entry's range {want!r}.",
            )

    return Applicability(
        decisive=True,
        supporting=True,
        reason="applies_to matched every constraint this case records.",
    )


def _parse(path: Path, section: str) -> tuple[list[KnowledgeEntry], str | None]:
    """Parse one file. Returns ``(entries, error)`` — never raises for content."""
    import yaml

    ext = path.suffix.lower()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"{path.name}: unreadable ({exc})"

    def one(d: dict, body: str = "") -> KnowledgeEntry:
        return KnowledgeEntry(
            entry_id=str(d.get("id") or path.stem),
            path=str(path),
            body=body,
            applies_to=dict(d.get("applies_to") or {}),
            source=section,
        )

    try:
        if ext in (".md", ".markdown"):
            meta, _, body = (raw[4:] if raw.startswith("---\n") else "").partition("\n---")
            d = yaml.safe_load(meta) if meta else {}
            return [one(d or {}, body.strip())], None
        if ext in (".yaml", ".yml"):
            d = yaml.safe_load(raw)
            return ([one(d)] if isinstance(d, dict) else []), None
        if ext == ".json":
            d = json.loads(raw)
            return ([one(d)] if isinstance(d, dict) else []), None
        if ext == ".jsonl":
            return [one(json.loads(ln)) for ln in raw.splitlines() if ln.strip()], None
        if ext in (".csv", ".tsv"):
            delim = "\t" if ext == ".tsv" else ","
            return [one(row) for row in csv.DictReader(raw.splitlines(), delimiter=delim)], None
        if ext in (".txt", ".log"):
            sidecar = path.with_suffix(".yaml")
            d = yaml.safe_load(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
            return [one(d or {}, raw.strip())], None
    except Exception as exc:
        return [], f"{path.name}: {type(exc).__name__}: {exc}"
    return [], None


def _walk() -> tuple[list[KnowledgeEntry], list[str], list[str]]:
    root = knowledge_root()
    entries: list[KnowledgeEntry] = []
    unreadable: list[str] = []
    unsupported: list[str] = []
    if not root.is_dir():
        return entries, unreadable, unsupported
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        ext = path.suffix.lower()
        section = path.relative_to(root).parts[0] if path.relative_to(root).parts else "kb"
        if ext in NEEDS_CONVERSION:
            unsupported.append(
                f"{path.name}: {ext} is not read directly — convert it to "
                f"Markdown (with an applies_to front-matter block) and mount that."
            )
            continue
        if ext not in _READABLE:
            # A .yaml sidecar for a .txt is consumed with its partner, not alone.
            continue
        if ext in (".yaml", ".yml") and path.with_suffix(".txt").is_file():
            continue
        found, err = _parse(path, section)
        if err:
            unreadable.append(err)
        entries.extend(found)
    return entries, unreadable, unsupported


def load_knowledge() -> tuple[KnowledgeEntry, ...]:
    """Every readable entry. Parse failures are visible via :func:`knowledge_status`."""
    return tuple(_walk()[0])


def knowledge_status() -> dict[str, Any]:
    """What is mounted, what could not be read, and what formats are accepted."""
    entries, unreadable, unsupported = _walk()
    with_applies = sum(1 for e in entries if e.applies_to)
    root = knowledge_root()
    if not entries:
        note = (
            f"No knowledge entries under {root}. Until one is mounted, no case "
            f"can reach Confirmed by the knowledge route. Create "
            f"{root}/{{{','.join(SECTIONS)}}}/ and add entries in any format "
            f"below — each needs an applies_to block (product, build, driver, "
            f"firmware, hardware_model) to be decisive."
        )
    else:
        note = (
            f"{len(entries)} entry/entries under {root}; {with_applies} carry an "
            f"applies_to block and can therefore be decisive. The rest can "
            f"support a hypothesis but cannot make a case Confirmed — matching "
            f"is by version applicability, never by similarity, because a "
            f"similar entry for the wrong build is how a wrong Confirmed is made."
        )
    return {
        "root": str(root),
        "sections": list(SECTIONS),
        "entries": len(entries),
        "with_applies_to": with_applies,
        "by_source": {s: sum(1 for e in entries if e.source == s) for s in SECTIONS},
        "unreadable": unreadable,
        "unsupported": unsupported,
        "formats": list(SUPPORTED_FORMATS),
        "needs_conversion": list(NEEDS_CONVERSION),
        "note": note,
    }
