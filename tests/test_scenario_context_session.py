"""Scope/budget failure controls; these do not certify an external gateway."""

import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from vmware_debug.ops.scenario_context import ContextUnavailable, prepare_context
from vmware_debug.ops.scenario_session import ScenarioSession, SessionStopped


def configuration():
    tool = {
        "name": "read_state",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}}},
    }
    return (
        {
            "id": "vm-performance",
            "procedure": "vm-performance.md",
            "tools": {"monitor": ["read_state"]},
        },
        {"monitor": {"read_state": tool}},
        {"monitor": ["read_state"]},
        {"monitor": "lab"},
        {"monitor": ["lab"]},
    )


def test_selection_is_scoped_and_does_not_mutate_live_manifest():
    args = configuration()
    original = copy.deepcopy(args)
    selected = prepare_context(*args)
    assert len(selected["tools"]) == 1
    selected["tools"][0]["tool"]["inputSchema"]["properties"].clear()
    assert args == original


@pytest.mark.parametrize("failure", ["permission", "target", "missing", "write", "reference"])
def test_unavailable_context_refuses_instead_of_broadening(failure):
    p, m, a, t, at = configuration()
    if failure == "permission":
        a["monitor"] = []
    if failure == "target":
        t["monitor"] = "production"
    if failure == "missing":
        m["monitor"] = {}
    if failure == "write":
        m["monitor"]["read_state"]["annotations"]["readOnlyHint"] = False
    if failure == "reference":
        p["procedure"] = "../../.env"
    with pytest.raises(ContextUnavailable):
        prepare_context(p, m, a, t, at)


def test_cross_backend_attempts_and_unknown_failures_consume_one_host_budget():
    s = ScenarioSession({("monitor", "read"), ("aria", "metrics")}, 2)
    first = s.begin("monitor", "read")
    s.finish(first, "unknown")
    second = s.begin("aria", "metrics")
    s.finish(second, "ok", output_tokens=7)
    with pytest.raises(SessionStopped):
        s.begin("monitor", "read")
    with pytest.raises(SessionStopped):
        s.finish(first, "ok")
    with pytest.raises(SessionStopped):
        s.begin("other", "read")
    rows = s.snapshot()["steps"]
    assert rows[0]["input_tokens"] is None and rows[1]["output_tokens"] == 7
    assert rows[0]["step_id"].split(":")[0] == rows[1]["step_id"].split(":")[0]
    rows.clear()
    assert s.snapshot()["attempted_calls"] == 2


def test_concurrent_begins_never_overspend_call_limit():
    s = ScenarioSession({("monitor", "read")}, 3)

    def begin(_):
        try:
            return s.begin("monitor", "read")
        except SessionStopped:
            return None

    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(begin, range(20)))
    assert len([x for x in ids if x]) == s.snapshot()["attempted_calls"] == 3


@pytest.mark.parametrize(
    "failure", ["unhashable", "wrong_name", "missing_target_schema", "bad_annotations"]
)
def test_malformed_live_metadata_fails_closed(failure):
    p, m, a, t, at = configuration()
    if failure == "unhashable":
        p["tools"]["monitor"] = [{}]
    elif failure == "wrong_name":
        m["monitor"]["read_state"]["name"] = "write_state"
    elif failure == "missing_target_schema":
        m["monitor"]["read_state"]["inputSchema"]["properties"] = {}
    elif failure == "bad_annotations":
        m["monitor"]["read_state"]["annotations"] = None
    with pytest.raises(ContextUnavailable):
        prepare_context(p, m, a, t, at)


@pytest.mark.parametrize("field", ["allowed", "allowed_targets"])
def test_string_permission_declarations_cannot_grant_by_substring(field):
    p, m, a, t, at = configuration()
    if field == "allowed":
        a["monitor"] = "read_state_extra"
    else:
        at["monitor"] = "lab-production"
    with pytest.raises(ContextUnavailable):
        prepare_context(p, m, a, t, at)
