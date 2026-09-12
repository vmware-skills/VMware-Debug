"""The test suite must never write to the operator's real files.

On 2026-09-11 VMware-VDI's suite was found appending rows to the operator's live
``~/.vmware/audit.db`` — it had no sandbox. ``tests/conftest.py`` installs one at
import time; these tests are the assertion half, so a future test cannot quietly
do without it.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import REAL_HOME, SANDBOX_HOME


def _under(path: Path, root: Path) -> bool:
    return root.resolve() in path.resolve().parents or path.resolve() == root.resolve()


def test_sandbox_is_not_the_real_home() -> None:
    """Positive control: the sandbox must actually be somewhere else."""
    assert SANDBOX_HOME.resolve() != REAL_HOME.resolve()
    assert not _under(SANDBOX_HOME, REAL_HOME)


def test_home_and_ops_home_point_into_the_sandbox() -> None:
    assert Path(os.environ["HOME"]).resolve() == SANDBOX_HOME.resolve()
    assert _under(Path(os.environ["OPS_HOME"]), SANDBOX_HOME)
    assert Path.home().resolve() == SANDBOX_HOME.resolve()


def test_policy_resolves_the_shared_audit_db_inside_the_sandbox() -> None:
    from vmware_policy.paths import ops_path

    assert _under(ops_path("audit.db"), SANDBOX_HOME)


def test_the_case_folder_is_inside_the_sandbox() -> None:
    """Debug's own writes: the case ledger lives under OPS_HOME/cases."""
    from vmware_debug.ops.cases.store import cases_root

    assert _under(cases_root(), SANDBOX_HOME)


def test_an_audited_write_lands_in_the_sandbox_and_not_in_the_real_db() -> None:
    """End to end: a recognisable row reaches the sandbox database, not the real one."""
    from vmware_policy import get_engine

    marker = "test_audit_isolation_probe"
    get_engine().log(skill="debug", tool=marker, params={}, result={}, status="ok")

    sandbox_db = Path(get_engine()._path)
    assert _under(sandbox_db, SANDBOX_HOME)
    with sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True) as con:
        found = con.execute("SELECT count(*) FROM audit_log WHERE tool = ?", (marker,)).fetchone()[0]
    assert found >= 1, "the row did not reach the sandbox database"

    real_db = REAL_HOME / ".vmware" / "audit.db"
    if not real_db.exists():
        pytest.skip("no production audit database on this machine to check against")
    with sqlite3.connect(f"file:{real_db}?mode=ro", uri=True) as con:
        leaked = con.execute("SELECT count(*) FROM audit_log WHERE tool = ?", (marker,)).fetchone()[0]
    assert leaked == 0, f"{leaked} row(s) from this test reached {real_db}"
