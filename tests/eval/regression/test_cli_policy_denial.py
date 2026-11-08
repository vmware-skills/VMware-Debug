"""CLI policy denial must remain a denial in audit and be readable by an operator."""

import pytest
from typer.testing import CliRunner

from vmware_debug.cli import app
from vmware_policy import cli_guard
from vmware_policy.policy import PolicyDenied, PolicyResult


@pytest.mark.parametrize("args", [["categories"], ["triage"]])
def test_denied_cli_command_prints_reason_and_records_one_denial(monkeypatch, args):
    rows = []
    def deny(*args, **kwargs):
        raise PolicyDenied(PolicyResult(allowed=False, rule="ops-review", reason="offline checks denied"))
    monkeypatch.setattr(cli_guard, "guard", deny)
    monkeypatch.setattr(cli_guard, "audit_call", lambda *a, **kw: rows.append(kw))
    result = CliRunner().invoke(app, args, input="[]")
    assert result.exit_code == 1
    assert "Denied by policy" in result.output
    assert "offline checks denied" in result.output and "ops-review" in result.output
    assert "Traceback" not in result.output
    assert len(rows) == 1 and rows[0]["status"] == "denied"
