"""Session-wide sandbox: the suite must not touch the operator's real files.

Installed at *import* time, not in a fixture: a fixture runs after collection
has imported every test module and, with them, the package — too late to move
a path something already resolved.

``OPS_HOME`` moves ``vmware_policy``'s shared ``audit.db`` (and the policy,
budget and undo state beside it) and the case ledger under ``OPS_HOME/cases``. ``HOME`` moves anything resolved
from ``~``.

This repo got the sandbox on 2026-09-11, when a sibling (VMware-VDI) was found
writing test rows into the operator's real ``~/.vmware/audit.db`` because it
had none. This suite was not leaking — every case test sets ``OPS_HOME`` itself with ``monkeypatch``, so one that forgets would write into the operator's real ``~/.vmware/cases`` — but that was a property of which
tests exist today, not a protection. See
``tests/eval/regression/test_audit_isolation.py``.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from vmware_policy.audit import reset_engine

# The operator's real home, captured before the redirect.
REAL_HOME = Path(os.path.expanduser("~"))

SANDBOX_HOME = Path(tempfile.mkdtemp(prefix="vmware-debug-tests-"))

os.environ["HOME"] = str(SANDBOX_HOME)
os.environ["OPS_HOME"] = str(SANDBOX_HOME / ".vmware")
# expanduser() consults USERPROFILE on Windows; keep every spelling pointing here.
os.environ["USERPROFILE"] = str(SANDBOX_HOME)

# The audit engine is a lazily built singleton bound to the path it first
# resolved; clear any stale binding so writes cannot go back to the real file.
reset_engine()

atexit.register(shutil.rmtree, SANDBOX_HOME, True)
