"""Two writers on one case must not lose each other's entries.

``OPS_HOME`` is honoured precisely so a team can point it at a share, which
makes two agents working the same case a supported situation rather than an
edge case. The gap list, the hypothesis ledger and the grade history are each
read, extended and written back whole, so without a lock the second writer
silently erases the first one's entry — and a ledger that drops a gap reads as
a better-supported case than the one investigated.

Each race is forced deterministically: writer A is paused right after it has
read the ledger, writer B runs in that window, then A is released. Without a
lock B finishes inside the window and A writes back a stale copy; with one, B
waits for A and both entries survive.
"""

from __future__ import annotations

import threading
from pathlib import Path
import pytest

from vmware_debug.ops.cases import api, conclusion, evidence, hypotheses, store, timeline
from vmware_debug.ops.cases.conclusion import grade_history, record_grade
from vmware_debug.ops.cases.evidence import (
    Evidence,
    EvidenceConflict,
    Gap,
    load_evidence,
    load_gaps,
    record_evidence,
    record_gap,
)
from vmware_debug.ops.cases.grading import grade_case
from vmware_debug.ops.cases.hypotheses import add_hypothesis, load_hypotheses
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import CaseLocked, case_dir, create_case
from vmware_debug.ops.cases.timeline import build_case_timeline, close_case

AT = "2026-09-11T09:00:00Z"

#: How long writer A holds its stale read open. Long enough for an unguarded
#: writer B to finish inside it; short enough to keep the suite quick.
HOLD_S = 0.5


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def case():
    cid = create_case(Scope(summary="vsan latency", determined_by="alarm 42"), at=AT).case_id
    add_hypothesis(cid, "failing device")
    return cid


def ev(skill: str) -> Evidence:
    return Evidence(
        source_skill=skill,
        source_tool="t",
        query={},
        fetched_at="2026-09-11T09:05:00Z",
        summary="s",
    )


def race(monkeypatch, module, name: str, first, second) -> list[BaseException]:
    """Run ``first`` in thread A, paused just after A calls ``module.name``.

    ``second`` runs in thread B while A is paused. Returns any exceptions the
    two raised, so a test can tell "one writer was refused" from "both landed".
    """
    real = getattr(module, name)
    a_has_read, b_done = threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        result = real(*args, **kwargs)
        if threading.current_thread().name == "A" and not a_has_read.is_set():
            a_has_read.set()
            b_done.wait(HOLD_S)
        return result

    monkeypatch.setattr(module, name, paused)
    errors: list[BaseException] = []

    def run(fn, done=None):
        try:
            fn()
        except BaseException as exc:  # collected and asserted on by the caller
            errors.append(exc)
        finally:
            if done is not None:
                done.set()

    a = threading.Thread(target=run, args=(first,), name="A")
    b = threading.Thread(target=run, args=(second, b_done), name="B")
    a.start()
    if not a_has_read.wait(5):
        a.join(5)
        raise AssertionError(f"writer A never reached the pause point; it raised {errors!r}")
    b.start()
    a.join(10)
    b.join(10)
    assert not a.is_alive() and not b.is_alive(), "a writer hung"
    return errors


class TestNoLostUpdates:
    def test_two_gaps_recorded_at_once_both_survive(self, case, monkeypatch):
        errors = race(
            monkeypatch,
            evidence,
            "load_gaps",
            lambda: record_gap(
                case, Gap(what="SMART data", why="no access", how_to_close="ask the storage team")
            ),
            lambda: record_gap(
                case,
                Gap(
                    what="switch logs",
                    why="not collected",
                    how_to_close="pull them from the ToR switch",
                ),
            ),
        )
        assert errors == []
        gaps = load_gaps(case)
        assert sorted(g.what for g in gaps) == ["SMART data", "switch logs"]
        assert sorted(g.gap_id for g in gaps) == ["G001", "G002"]

    def test_two_hypotheses_registered_at_once_both_survive(self, case, monkeypatch):
        errors = race(
            monkeypatch,
            hypotheses,
            "load_hypotheses",
            lambda: add_hypothesis(case, "controller firmware"),
            lambda: add_hypothesis(case, "network congestion"),
        )
        assert errors == []
        statements = [h.statement for h in load_hypotheses(case)]
        assert sorted(statements) == ["controller firmware", "failing device", "network congestion"]
        assert sorted(h.hypothesis_id for h in load_hypotheses(case)) == ["H1", "H2", "H3"]

    def test_two_gradings_recorded_at_once_both_stay_in_the_history(self, case, monkeypatch):
        errors = race(
            monkeypatch,
            conclusion,
            "grade_history",
            lambda: record_grade(case, grade_case(case), at="2026-09-11T09:30:00Z"),
            lambda: record_grade(case, grade_case(case), at="2026-09-11T09:31:00Z"),
        )
        assert errors == []
        history = grade_history(case)
        assert len(history) == 2
        # Both entries survive even unguarded, because the write re-reads the
        # file. What the race corrupts is the chain: both writers read an empty
        # history, so both recorded themselves as the "initial" grading.
        first, second = history
        assert [first.direction, second.direction].count("initial") == 1
        assert second.previous == first.grade

    def test_two_evidence_items_submitted_at_once_both_land(self, case, monkeypatch):
        # Before the lock the second writer was refused ("re-run the
        # submission") — loud, but a refusal two cooperating agents did nothing
        # to deserve. Serialised, both simply land.
        errors = race(
            monkeypatch,
            evidence,
            "_next_id",
            lambda: record_evidence(case, ev("vmware-monitor")),
            lambda: record_evidence(case, ev("vmware-log-insight")),
        )
        assert errors == []
        assert sorted(e.source_skill for e in load_evidence(case)) == [
            "vmware-log-insight",
            "vmware-monitor",
        ]

    def test_a_grade_is_recorded_for_the_ledger_it_was_computed_from(self, case, monkeypatch):
        # case_grade computes, then records. A falsifying gap landing between
        # the two used to leave "probable" on record while the ledger at that
        # moment supported only "candidate".
        for skill in ("vmware-monitor", "vmware-log-insight"):
            record_evidence(case, ev(skill))
        assert grade_case(case).grade == "probable"
        mismatches = []
        real_record = api.record_grade

        def checked(case_id, result, at):
            supported = grade_case(case_id).grade
            if supported != result.grade:
                mismatches.append((result.grade, supported))
            return real_record(case_id, result, at=at)

        monkeypatch.setattr(api, "record_grade", checked)
        errors = race(
            monkeypatch,
            api,
            "grade_case",
            lambda: api.grade(case, at="2026-09-11T09:30:00Z"),
            lambda: record_gap(
                case,
                Gap(
                    what="SMART data",
                    why="no access",
                    how_to_close="ask the storage team",
                    blocks=("H1",),
                    could_falsify=True,
                ),
            ),
        )
        assert errors == []
        assert mismatches == []

    def test_a_timeline_is_not_replaced_by_one_built_from_older_evidence(self, case, monkeypatch):
        record_evidence(case, ev("vmware-monitor"))

        def add_and_rebuild():
            record_evidence(case, ev("vmware-log-insight"))
            build_case_timeline(case)

        errors = race(
            monkeypatch,
            timeline,
            "load_evidence",
            lambda: build_case_timeline(case),
            add_and_rebuild,
        )
        assert errors == []
        assert "E002" in (case_dir(case) / "timeline.md").read_text(encoding="utf-8")


class TestEvidenceNeverOverwrites:
    def test_a_file_that_appears_after_the_check_is_not_replaced(self, case, monkeypatch):
        """The existence check and the write were two steps; the write itself must refuse."""
        record_evidence(case, ev("vmware-monitor"))
        first = case_dir(case) / "evidence" / "E001.json"
        original = first.read_text(encoding="utf-8")

        # A writer that does not take the lock (an older version on the same
        # share) has already landed E001; hand out that id again.
        monkeypatch.setattr(evidence, "_next_id", lambda existing, prefix: "E001")

        with pytest.raises(EvidenceConflict):
            record_evidence(case, ev("vmware-log-insight"))
        assert first.read_text(encoding="utf-8") == original


class TestTheLock:
    def test_a_held_lock_is_reported_not_broken(self, case, monkeypatch):
        monkeypatch.setattr(store, "LOCK_TIMEOUT_S", 0.2)
        lock = case_dir(case) / store.LEDGER_LOCK
        lock.write_text("pid 4242 on other-host since 2026-09-11T08:00:00Z\n", encoding="utf-8")

        with pytest.raises(CaseLocked) as info:
            record_gap(
                case, Gap(what="SMART data", why="no access", how_to_close="ask the storage team")
            )

        message = str(info.value)
        assert str(lock) in message
        assert "pid 4242 on other-host" in message
        assert "delete" in message.lower()
        assert lock.exists(), "a lock someone else holds must never be removed for them"
        assert load_gaps(case) == ()

    def test_the_lock_is_released_when_the_write_fails(self, case, monkeypatch):
        def broken(_case_id):
            raise OSError("disk went away")

        real_load_gaps = evidence.load_gaps
        monkeypatch.setattr(evidence, "load_gaps", broken)  # runs inside the lock
        with pytest.raises(OSError, match="disk went away"):
            record_gap(case, Gap(what="x", why="y", how_to_close="z"))
        # Restore only this attribute: monkeypatch.undo() would also drop the
        # fixture's OPS_HOME and point the rest of the test at the real home.
        monkeypatch.setattr(evidence, "load_gaps", real_load_gaps)
        assert not (case_dir(case) / store.LEDGER_LOCK).exists()
        assert record_gap(case, Gap(what="x", why="y", how_to_close="z")).gap_id == "G001"

    def test_a_lock_that_changed_hands_is_not_removed_on_release(self, case):
        # Someone deleted our lock believing it stale and another writer took
        # it. Releasing ours must not delete theirs.
        lock = case_dir(case) / store.LEDGER_LOCK
        with store.ledger_lock(case):
            lock.write_text("pid 7 on other-host (token other)\n", encoding="utf-8")
        assert lock.read_text(encoding="utf-8") == "pid 7 on other-host (token other)\n"

    def test_closing_a_case_takes_the_lock_once_and_leaves_none(self, case):
        # close_case records a grade, which takes the lock too. The lock is
        # re-entrant within one thread, so this completes instead of waiting
        # on itself until the timeout.
        result = close_case(case, at="2026-09-11T10:00:00Z")
        assert result["state"] == "closed"
        assert not (case_dir(case) / store.LEDGER_LOCK).exists()

    def test_a_lock_pending_delete_is_waited_for_not_raised(self, case, monkeypatch):
        # Windows answers O_EXCL on a file that is being deleted with
        # PermissionError, not FileExistsError; that is still "held".
        real_open, calls = store.os.open, []

        def pending_delete(path, flags, mode=0o777):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError(13, "Access is denied")
            return real_open(path, flags, mode)

        (case_dir(case) / store.LEDGER_LOCK).write_text("someone\n", encoding="utf-8")
        monkeypatch.setattr(store.os, "open", pending_delete)
        monkeypatch.setattr(store, "_LOCK_POLL_S", 0.01)

        def drop_lock_then_poll(seconds):
            (case_dir(case) / store.LEDGER_LOCK).unlink(missing_ok=True)

        monkeypatch.setattr(store.time, "sleep", drop_lock_then_poll)
        assert record_gap(case, Gap(what="x", why="y", how_to_close="z")).gap_id == "G001"
        assert len(calls) >= 2

    def test_a_case_folder_that_cannot_be_written_is_not_reported_as_locked(
        self, case, monkeypatch
    ):
        def denied(*_args, **_kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(store.os, "open", denied)
        with pytest.raises(PermissionError):
            record_gap(case, Gap(what="x", why="y", how_to_close="z"))

    def test_a_lock_that_windows_will_not_delete_yet_is_retried(self, case, monkeypatch):
        real_unlink, attempts = Path.unlink, []

        def busy_twice(self, *args, **kwargs):
            if self.name == store.LEDGER_LOCK:
                attempts.append(self)
                if len(attempts) <= 2:
                    raise PermissionError(32, "The process cannot access the file")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", busy_twice)
        record_gap(case, Gap(what="x", why="y", how_to_close="z"))
        assert len(attempts) == 3
        assert not (case_dir(case) / store.LEDGER_LOCK).exists()

    def test_a_lock_whose_note_could_not_be_written_is_not_left_behind(self, case, monkeypatch):
        def full_disk(*_args, **_kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(store.os, "fdopen", full_disk)
        with pytest.raises(OSError, match="No space"):
            record_gap(case, Gap(what="x", why="y", how_to_close="z"))
        assert not (case_dir(case) / store.LEDGER_LOCK).exists()


class TestAtomicWrite:
    """Readers take no lock, so a write must never be visible half-done.

    A plain write truncates before refilling: three locked writers and one
    unlocked reader produced 60 torn reads in 5,449, each reported to the user
    as a hand-edited file.
    """

    def test_a_replace_leaves_the_new_content_and_no_temp_file(self, tmp_path):
        target = tmp_path / "gaps.json"
        target.write_text("old\n", encoding="utf-8")
        store.write_text_atomic(target, "new\n")
        assert target.read_text(encoding="utf-8") == "new\n"
        assert [p.name for p in tmp_path.iterdir()] == ["gaps.json"]

    def test_an_exclusive_write_refuses_an_existing_file_and_leaves_it_alone(self, tmp_path):
        target = tmp_path / "E001.json"
        target.write_text("first\n", encoding="utf-8")
        with pytest.raises(FileExistsError):
            store.write_text_atomic(target, "second\n", exclusive=True)
        assert target.read_text(encoding="utf-8") == "first\n"
        assert [p.name for p in tmp_path.iterdir()] == ["E001.json"]

    def test_without_hard_links_an_exclusive_write_still_never_overwrites(
        self, tmp_path, monkeypatch
    ):
        def no_links(*_args, **_kwargs):
            raise OSError(1, "Operation not permitted")

        monkeypatch.setattr(store.os, "link", no_links)
        fresh = tmp_path / "E001.json"
        store.write_text_atomic(fresh, "first\n", exclusive=True)
        assert fresh.read_text(encoding="utf-8") == "first\n"
        with pytest.raises(FileExistsError):
            store.write_text_atomic(fresh, "second\n", exclusive=True)
        assert fresh.read_text(encoding="utf-8") == "first\n"

    def test_a_reader_holding_the_file_open_is_waited_out(self, tmp_path, monkeypatch):
        # Windows refuses to replace a file another process has open; the
        # reader lets go within one read, so a short retry is enough.
        target = tmp_path / "gaps.json"
        target.write_text("old\n", encoding="utf-8")
        real_replace, attempts = store.os.replace, []

        def busy_twice(src, dst):
            attempts.append(dst)
            if len(attempts) <= 2:
                raise PermissionError(32, "The process cannot access the file")
            real_replace(src, dst)

        monkeypatch.setattr(store.os, "replace", busy_twice)
        store.write_text_atomic(target, "new\n")
        assert len(attempts) == 3
        assert target.read_text(encoding="utf-8") == "new\n"
        assert [p.name for p in tmp_path.iterdir()] == ["gaps.json"]

    def test_ledger_writes_leave_no_temp_files_in_the_case(self, case):
        record_gap(case, Gap(what="w", why="y", how_to_close="z"))
        add_hypothesis(case, "second")
        record_evidence(case, ev("vmware-monitor"))
        close_case(case, at="2026-09-11T10:00:00Z")
        leftovers = [p.name for p in case_dir(case).rglob("*.tmp")]
        assert leftovers == []
