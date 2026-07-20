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

import pytest

from vmware_debug.envelope import normalize_events
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


# ---------------------------------------------------------------------------
# The cap is only a guard if the messages fit inside it
# ---------------------------------------------------------------------------
#
# Passing the allowlist is not the same as arriving intact. Every rejection here
# nests — normalize_events wraps normalize_event's text to add the entry index —
# so two remedies and the evidence share one 500-char budget. The rejections
# used to interpolate the whole caller-supplied event *before* the remedy, so a
# real vCenter event (~430 characters on its own) pushed every instruction past
# the cut. The agent received a truncated dict dump and no next step, through
# the one path designed to teach it something.
#
# This is asserted mechanically rather than described in a comment, because the
# budget is the kind of fact that drifts the moment someone adds a clause.

_FAT_EVENT = {
    "eventTypeId": "vim.event.VmPoweredOffEvent",
    "chainId": 90210,
    "userName": "CORP\\svc-vcops",
    "datacenter": "DC-Frankfurt-01",
    "computeResource": "Prod-Cluster-A",
    "host": "esx-047.corp.example.com",
    "vm": "web-prod-014",
    "fullFormattedMessage": "web-prod-014 on esx-047 in DC-Frankfurt-01 is powered off",
    "severity": "info",
    "key": 4471182,
}

#: Every way an event can be rejected: name -> (the 'ts' value that triggers
#: it, a fragment of the *inner* remedy that has to survive the cap).
#:
#: The fragment must be inner-specific. Asserting on "incident_timeline" alone
#: proves nothing: the batch wrapper says it in the first sentence, so that
#: assertion passes even when the entire inner remedy has been truncated away —
#: which is the exact failure these tests exist to catch. ``...`` means "omit
#: 'ts' entirely".
_REJECTIONS = {
    "missing": (..., "Add 'ts'"),
    "non_numeric_string": ("not-a-time", "Copy 'ts' from the source event"),
    "empty_string": ("   ", "Copy 'ts' from the source event"),
    "bool": (True, "a bool is not a time"),
    "bare_year": ("2020", "Set 'ts' to full epoch"),
    "wrong_type": ({"nested": ["a"] * 400}, "Convert that value"),
    "pathological_string": ("n" * 3000, "Copy 'ts' from the source event"),
}


def _reject(ts) -> str:
    """Return what the agent would see for one rejection, cap applied."""
    event = dict(_FAT_EVENT) if ts is ... else {**_FAT_EVENT, "ts": ts}
    with pytest.raises(ValueError) as exc:
        normalize_events([event])
    return _safe_error(exc.value, "incident_timeline")


def test_every_rejection_survives_the_cap_with_its_remedy():
    for name, (ts, inner_remedy) in _REJECTIONS.items():
        out = _reject(ts)
        assert len(out) <= 500, name
        # The remedy is what the agent acts on; it must never be the part cut.
        assert inner_remedy in out, f"{name}: inner remedy lost to truncation"
        assert "event[0]" in out, f"{name}: caller cannot tell which entry"


def test_no_rejection_is_silently_truncated():
    """A cut that announces itself, or no cut at all — never an invisible one.

    ``sanitize()`` truncates without an ellipsis, so a clipped message reads as
    a complete one. Bounding the evidence ourselves is what keeps the composed
    message under the cap, which is what keeps the cut visible.
    """
    for name, (ts, _) in _REJECTIONS.items():
        out = _reject(ts)
        assert len(out) < 500, f"{name}: sat exactly on the cap — assume it was cut"


def test_a_credential_bearing_event_is_not_echoed_whole():
    """The events came from other skills' read tools; they can carry secrets.

    This is the allowlisted path, so whatever the message interpolates reaches
    the agent verbatim. Bounding the repr is what keeps that to a fragment.
    """
    event = {**_FAT_EVENT, "task": "https://admin:hunter2@vc.internal/api/task-42"}
    del event["eventTypeId"]  # keep 'task' out of the surviving prefix
    with pytest.raises(ValueError) as exc:
        normalize_events([event])
    out = _safe_error(exc.value, "incident_timeline")
    assert "hunter2" not in out


def test_a_non_numeric_timestamp_gets_an_authored_message():
    """Regression: a bare ``float()`` let its own ValueError out.

    ``parse_timestamp`` fell through to ``float(text)`` for anything that was
    not ISO-8601. For 'not-a-time' that raised Python's own ValueError —
    ``could not convert string to float: 'not-a-time'`` — which is on the
    allowlist by type, so it reached the agent having bypassed all four
    authored messages in this module: a diagnosis with no remedy, in a wrapper
    whose entire purpose is to attach one.
    """
    out = _reject("not-a-time")
    assert "could not convert string to float" not in out
    assert "ISO-8601" in out and "epoch" in out, "must state the accepted formats"
    assert "'not-a-time'" in out, "must still name the value it rejected"
