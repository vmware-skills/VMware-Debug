"""incident_timeline handed the agent whatever exception text it caught.

The tool's failure path built ``{"error": str(exc), "hint": ...}`` directly.
For the rejections this skill authors that was correct — and it is why the gap
stayed invisible, because those are the only failures anyone exercises. Every
test, every eval, and every manual run feeds a malformed event and gets back the
sentence ``envelope.py`` wrote for it, so the payload always looked right.

What no one fed it was an event that made the walk fail for an unplanned reason.
This skill fetches nothing itself: the events are handed to it by the caller,
pulled from vmware-monitor, vmware-aria, vmware-log-insight, vmware-nsx. Their
payloads carry task URLs, and a vCenter task URL can carry credentials in its
userinfo. An unplanned exception raised while walking such an event quotes it,
and ``str(exc)`` put that straight back into the model's context.

So the rule is the family's: ``ValueError`` — the entire vocabulary this skill
raises on purpose — passes through, and everything else is reduced to its type.
``RuntimeError`` is not on that list and must not be added; it is the generic
catch-all, so allowing it would pass any library's raw text through as though
this skill had authored it.
"""

from __future__ import annotations

from vmware_debug.mcp_server.server import _safe_error

TEACHING = (
    "event has no timestamp field: {'source': 'vmware-monitor'} — expected one of "
    "ts/timestamp/time/createTime/startTimeUTC. Add 'ts' (ISO-8601, epoch seconds, "
    "or epoch millis), then re-run incident_timeline."
)


def test_rejected_event_keeps_its_message():
    """The authored rejections are the only text this tool is meant to return."""
    assert _safe_error(ValueError(TEACHING), "incident_timeline") == TEACHING


def test_unplanned_exceptions_are_reduced():
    """A caller-supplied event can carry credentials; an unplanned trace quotes it."""
    out = _safe_error(RuntimeError("https://admin:hunter2@vc.internal/api/x"), "incident_timeline")
    assert out == "RuntimeError: operation failed."
    assert "hunter2" not in out


def test_runtime_error_is_not_a_teaching_error():
    """RuntimeError is the generic catch-all — allowlisting it reopens the leak."""
    assert (
        _safe_error(RuntimeError(TEACHING), "incident_timeline")
        == "RuntimeError: operation failed."
    )


def test_the_tool_actually_uses_the_helper(monkeypatch):
    """The helper is worthless if the failure path still formats str(exc) itself.

    Driven through the registered tool rather than the helper so this fails if
    the two are ever disconnected — the defect being pinned was a call site that
    built its own payload, not a missing helper.
    """
    from vmware_debug.mcp import tools as t
    from vmware_debug.mcp_server.server import build_server

    def _boom(*_a, **_kw):
        raise RuntimeError("https://admin:hunter2@vc.internal/api/x")

    monkeypatch.setattr(t, "incident_timeline", _boom)
    tool = build_server()._tool_manager.get_tool("incident_timeline")

    out = tool.fn(events=[{"ts": 1784908980, "source": "vmware-monitor"}])
    assert out["error"] == "RuntimeError: operation failed."
    assert "hunter2" not in out["error"]
    assert "items" not in out


def test_message_is_truncated():
    """Length capping is the other half of the guard.

    500, not the family's 300: these messages interpolate a repr of the rejected
    event before the remedy, and a four-field event already reaches ~425.
    """
    out = _safe_error(ValueError("x" * 900), "incident_timeline")
    assert len(out) <= 500
    assert len(out) > 300
