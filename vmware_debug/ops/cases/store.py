"""Reading and writing the case directory.

The directory is the source of truth and the deliverable. ``case.json`` is an
index for fast listing; if it ever disagrees with the text files, the text files
win, because those are what a human audits.

Nothing here touches a VMware environment or holds a credential. That is the
point of the design: a case folder can be reopened and re-argued on a laptop
with no access to anything.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from vmware_policy.paths import ops_path

from vmware_debug.ops.cases.ids import validate_case_id
from vmware_debug.ops.cases.model import Case, Scope

_SKELETON_MD = {
    "timeline.md": (
        "# Timeline\n\n"
        "_Empty. Populated at step 04/05, once evidence has been submitted._\n\n"
        "## Trigger\n\n## Symptom\n\n## Propagation\n\n## Recovery\n"
    ),
    "hypotheses.md": (
        "# Hypotheses\n\n"
        "_Empty. Each hypothesis records its supporting evidence, its "
        "counter-evidence, the gaps that block it, and the next step._\n"
    ),
    "conclusion.md": (
        "# Conclusion\n\n"
        "_Not graded yet._\n\n"
        "The grade is computed from the ledger by `case_grade`; it is not "
        "written here by hand. Grade history, including any demotion, is "
        "appended below and never rewritten.\n"
    ),
}


class CaseError(Exception):
    """Base class for case-store failures."""


class CaseNotFound(CaseError):
    """No case with that id. Deliberately not an empty case."""


class CaseExists(CaseError):
    """A case with that id is already on disk."""


class CaseLocked(CaseError):
    """Another writer holds the case's ledger lock and did not let go in time."""


#: One lock file per case, taken by every write to that case's ledger.
#: ``OPS_HOME`` is honoured so a team can share a cases folder, which makes two
#: writers on one case a supported situation. The gap list, the hypothesis
#: ledger and the grade history are each read, extended and written back
#: whole; unserialised, the second writer erases the first one's entry.
#:
#: Exclusive create (``O_CREAT | O_EXCL``) is atomic on POSIX and Windows alike,
#: which is why this is a file and not ``fcntl``. Dot-prefixed so nothing that
#: lists a folder's content counts it.
LEDGER_LOCK = ".ledger.lock"
LOCK_TIMEOUT_S = 10.0
_LOCK_POLL_S = 0.05
_THREAD = threading.local()
_log = logging.getLogger(__name__)


def _held() -> dict[Path, tuple[int, str]]:
    """This thread's held locks: path -> (depth, holder note)."""
    held = getattr(_THREAD, "held", None)
    if held is None:
        held = _THREAD.held = {}
    return held


@contextmanager
def ledger_lock(case_id: str) -> Iterator[None]:
    """Hold ``case_id``'s ledger lock for the duration of the block.

    Re-entrant within one thread: closing a case records a grade, and both take
    the lock. A lock that cannot be taken within :data:`LOCK_TIMEOUT_S` raises
    :class:`CaseLocked` naming its holder. It is never broken automatically —
    removing a lock someone still holds is exactly how an entry gets lost.
    """
    path = case_dir(case_id) / LEDGER_LOCK
    held = _held()
    if path in held:
        depth, note = held[path]
        held[path] = (depth + 1, note)
        try:
            yield
        finally:
            depth, note = held[path]
            held[path] = (depth - 1, note)
        return

    note = _acquire(path, case_id)
    held[path] = (1, note)
    try:
        yield
    finally:
        del held[path]
        _release(path, note)


def _acquire(path: Path, case_id: str) -> str:
    """Create the lock file exclusively, waiting up to the timeout. Returns its note."""
    note = (
        f"pid {os.getpid()} on {socket.gethostname()} since "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"(token {uuid.uuid4().hex})\n"
    )
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileNotFoundError:
            raise CaseNotFound(
                f"No case {case_id!r} under {cases_root()}. Run case_list to see "
                f"the ids that exist, or case_open to start one."
            ) from None
        except (FileExistsError, PermissionError) as exc:
            # Windows answers O_EXCL on a lock that is being deleted with
            # PermissionError: still held. With no lock there, it is a folder
            # that cannot be written, and waiting would misreport it as a lock.
            if isinstance(exc, PermissionError) and not path.exists():
                raise
            if time.monotonic() >= deadline:
                raise CaseLocked(_locked_message(path, case_id)) from None
            time.sleep(_LOCK_POLL_S)
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(note)
        except BaseException:
            # A lock with no note would read as "an unnamed holder" to every
            # later writer until someone deleted it by hand.
            try:
                os.close(fd)
            except OSError:
                pass  # already closed by the with-block
            path.unlink(missing_ok=True)
            raise
        return note


def _release(path: Path, note: str) -> None:
    """Remove the lock only if it is still ours.

    Someone may have deleted it believing it stale, and another writer may
    have taken it since; deleting that one would unserialise two writers.
    Never raises: the write this lock guarded has already landed.
    """
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _log.warning(
            "Ledger lock %s was removed while held; another writer may have overlapped.", path
        )
        return
    if current != note:
        _log.warning("Ledger lock %s now belongs to someone else; leaving it in place.", path)
        return
    # Windows will not delete a file another process has open (a waiter
    # reading it for its message, a virus scanner); it lets go within moments.
    deadline = time.monotonic() + _REPLACE_RETRY_S
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                _log.warning(
                    "Could not remove ledger lock %s (%s). The write it guarded landed; "
                    "delete the lock once nothing is writing this case.",
                    path,
                    exc,
                )
                return
            time.sleep(_LOCK_POLL_S)


def _locked_message(path: Path, case_id: str) -> str:
    try:
        holder = path.read_text(encoding="utf-8").strip() or "an unnamed holder"
    except OSError:
        holder = "a holder that could not be read"
    return (
        f"Case {case_id} is being written by another process: {path} is held "
        f"by {holder}, and it was not released within {LOCK_TIMEOUT_S:g}s. "
        f"Retry once that writer finishes. If nothing else is working this "
        f"case — the holder crashed — delete {path} and retry. It is never "
        f"removed automatically, because removing a lock someone still holds "
        f"is exactly how a ledger entry gets lost."
    )


#: How long a replace waits out a reader holding the target open. Only Windows
#: refuses to replace a file another process has open, and readers hold these
#: files for the length of one read.
_REPLACE_RETRY_S = 1.0


def write_text_atomic(path: Path, text: str, *, exclusive: bool = False) -> None:
    """Write ``text`` to ``path`` so no reader ever sees half of it.

    The lock serialises writers; readers do not take it, and a plain write
    truncates the file before refilling it. Measured with three writers and one
    reader on a single case: 60 torn reads in 5,449, each reported as a
    hand-edited file. Writing a sibling temp file and moving it into place
    means a reader sees the old content or the new, never neither.

    ``exclusive`` publishes with :func:`os.link`, which fails if ``path``
    exists — the new-evidence case, where overwriting is the thing to refuse.
    Raises :class:`FileExistsError` then. On a filesystem without hard links
    it falls back to an exclusive create, which still never overwrites but is
    not atomic: a reader can catch that one new file half-written.
    """
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if exclusive:
            _publish_exclusive(tmp, path, text)
        else:
            _replace_waiting_out_readers(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _publish_exclusive(tmp: Path, path: Path, text: str) -> None:
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise
    except OSError:
        with path.open("x", encoding="utf-8") as fh:
            fh.write(text)


def _replace_waiting_out_readers(tmp: Path, path: Path) -> None:
    deadline = time.monotonic() + _REPLACE_RETRY_S
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_LOCK_POLL_S)


def cases_root() -> Path:
    """Where cases live. Honours ``OPS_HOME`` so a team can point this at a
    share or a ticket-system mount and hand the folder over as-is."""
    return ops_path("cases")


def case_dir(case_id: str) -> Path:
    """Resolve one case's directory. Validates before touching the path."""
    return cases_root() / validate_case_id(case_id)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path, what: str) -> dict:
    """Read one JSON file, or explain which file is broken.

    A corrupt ledger file is reported, never defaulted away: a case that
    silently reads back as empty is the family's most costly failure shape, and
    it would be at its most costly here.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CaseError(
            f"Cannot read {what} at {path}: {exc}. The case directory may have "
            f"been moved or its permissions changed."
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{what} is not valid JSON ({path}): {exc}. It was hand-edited or "
            f"truncated. Restore it from your copy of the case folder rather "
            f"than deleting it — the rest of the case is still intact."
        ) from exc


def create_case(scope: Scope, at: str) -> Case:
    """Open a case: write the skeleton and return the loaded record.

    Args:
        scope: Step 01's output. Validated by :class:`Scope` itself.
        at: ISO-8601 instant, supplied by the caller so this stays pure.

    Raises:
        CaseExists: if the id is taken. Never overwrites — the second open of
            the same summary in the same second is a mistake worth surfacing,
            and silently replacing a ledger would destroy the evidence it holds.
    """
    from vmware_debug.ops.cases.ids import new_case_id  # local: keeps ids leaf-level

    case_id = new_case_id(scope.summary, at=at)
    d = cases_root() / case_id
    if d.exists():
        raise CaseExists(
            f"Case {case_id} already exists at {d}. The id is the open time "
            f"plus a slug of the summary, so two cases opened in the same "
            f"second whose summaries reduce to the same slug collide — the "
            f"summaries need not be identical, and saying they were sent one "
            f"reporter looking for a duplicate that did not exist. Run "
            f"case_get {case_id} to see which case is already there; if it is "
            f"a different incident, open this one with a summary that names "
            f"what makes it different."
        )

    d.mkdir(parents=True)
    os.chmod(d, 0o700)
    (d / "evidence").mkdir()

    _write_json(d / "scope.json", scope.to_json())
    (d / "plan.jsonl").write_text("", encoding="utf-8")
    _write_json(d / "gaps.json", {"gaps": []})
    for name, body in _SKELETON_MD.items():
        (d / name).write_text(body, encoding="utf-8")
    _write_json(
        d / "case.json",
        {"case_id": case_id, "state": "open", "opened_at": at, "grade": None},
    )
    return Case(case_id=case_id, scope=scope, state="open", opened_at=at)


def load_case(case_id: str) -> Case:
    """Load one case. Raises :class:`CaseNotFound` rather than inventing one."""
    d = case_dir(case_id)
    if not d.is_dir():
        raise CaseNotFound(
            f"No case {case_id!r} under {cases_root()}. Run case_list to see "
            f"the ids that exist, or case_open to start one. (If you expected "
            f"it here, check OPS_HOME — cases follow it.)"
        )
    scope = Scope.from_json(_read_json(d / "scope.json", "scope.json"))
    index = _read_json(d / "case.json", "case.json")
    return Case(
        case_id=case_id,
        scope=scope,
        state=index.get("state", "open"),
        opened_at=index.get("opened_at", ""),
        grade=index.get("grade"),
    )


def list_cases() -> tuple[Case, ...]:
    """Every case, newest first.

    One unreadable entry does not take the listing down, and does not disappear
    from it either: it comes back with ``state="unreadable"`` so that a folder
    someone broke is visible as broken rather than absent.
    """
    root = cases_root()
    if not root.is_dir():
        return ()
    out: list[Case] = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        try:
            out.append(load_case(d.name))
        except (CaseError, ValueError):
            out.append(
                Case(
                    case_id=d.name,
                    scope=Scope(summary=d.name, determined_by="unreadable"),
                    state="unreadable",
                    opened_at="",
                )
            )
    return tuple(out)
