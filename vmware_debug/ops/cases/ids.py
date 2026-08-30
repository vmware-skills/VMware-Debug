"""Case identifiers, and the boundary check that keeps one from becoming a path.

A case id arrives as an MCP tool argument, which means the model picks it, and
the model picks it while reading text it did not write. ``validate_case_id`` is
the only thing between that and ``open(case_root / case_id / ...)``, so it is
allow-list shaped: a fixed character set and a length bound, rather than a list
of the tricks known today.
"""

from __future__ import annotations

import hashlib
import re

#: Generated ids are ``YYYYMMDD-HHMMSS-<slug>``. The pattern accepts that shape
#: and nothing else — no dots (so no ``.`` or ``..``), no separators (so no
#: traversal and no absolute paths), no whitespace, no control characters.
_VALID = re.compile(r"^[a-z0-9][a-z0-9-]{0,126}[a-z0-9]$")

#: Long enough for a timestamp plus a readable slug; short enough to stay under
#: every filesystem's component limit once suffixes are appended.
MAX_LEN = 128

_SLUG_MAX = 48


class CaseIdError(ValueError):
    """A case id that cannot be used as a directory name."""


def validate_case_id(case_id: str) -> str:
    """Return ``case_id`` unchanged, or raise :class:`CaseIdError`.

    Returning the value lets callers write ``root / validate_case_id(cid)``
    instead of validating and then using an unvalidated variable — the gap
    where a check gets skipped by accident.
    """
    if not isinstance(case_id, str) or not case_id.strip():
        raise CaseIdError(
            "Case id is empty. Pass the id returned by case_open, or run "
            "case_list to see the open cases."
        )
    if len(case_id) > MAX_LEN:
        raise CaseIdError(
            f"Case id is {len(case_id)} characters, over the {MAX_LEN} limit. "
            f"Pass the id returned by case_open, or run case_list to see it."
        )
    if not _VALID.match(case_id):
        raise CaseIdError(
            f"Case id {case_id!r} is not a valid id: ids are lowercase letters, "
            f"digits and hyphens only (no slashes, dots or spaces), as produced "
            f"by case_open — for example "
            f"'20260828-091500-vsan-latency-on-cluster-01'. Run case_list to "
            f"see the ids that exist."
        )
    return case_id


def slugify(text: str) -> str:
    """Reduce free text to the id character set, or "" if nothing survives."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if len(slug) > _SLUG_MAX:
        # Cut on a boundary so the slug ends in a whole word rather than a
        # fragment; fall back to the hard cut when there is no boundary to use.
        slug = slug[:_SLUG_MAX].rsplit("-", 1)[0] or slug[:_SLUG_MAX]
    return slug.strip("-")


def new_case_id(summary: str, at: str) -> str:
    """Build a case id from a summary and an ISO-8601 instant.

    ``at`` is passed in rather than read from the clock so that callers own the
    time source: it keeps this function pure, and it is what lets a case be
    reconstructed with its original id when a directory is replayed.

    The timestamp leads so that a plain directory listing is in chronological
    order, and the slug follows so the listing is readable without opening
    anything.
    """
    stamp = re.sub(r"[^0-9]", "", at)[:14]
    if len(stamp) < 14:
        raise CaseIdError(
            f"Timestamp {at!r} is not a usable ISO-8601 instant — expected "
            f"something like '2026-08-28T09:15:00Z'."
        )
    prefix = f"{stamp[:8]}-{stamp[8:14]}"
    return validate_case_id(f"{prefix}-{slugify(summary) or _digest_slug(summary)}")


def _digest_slug(summary: str) -> str:
    """A stable id fragment for a summary the id alphabet cannot hold.

    A summary written entirely in Chinese leaves nothing behind ``slugify``, and
    the previous fallback was the literal word ``case`` — so two unrelated
    investigations opened in the same second got the same id and the second was
    refused as a duplicate of the first. Widening the alphabet is not an option:
    the id becomes a directory name, and its character set is the only thing
    between a model-chosen string and ``open(root / case_id)``.

    So the id carries a digest of the summary instead. It is not readable, and
    the readable version is one file away in scope.json — but distinguishing two
    different investigations is a property the id has to have, and being
    pronounceable is not.
    """
    if not (summary or "").strip():
        # Nothing to distinguish. Two blank summaries in one second really are
        # the same case, and the store's refusal to overwrite is the right
        # answer rather than a manufactured difference.
        return "case"
    return "case-" + hashlib.sha256(summary.encode("utf-8")).hexdigest()[:8]
