"""``case_plan(max_steps=...)`` has to reach the planner that implements it.

The MCP tool declared the parameter, validated nothing, and then called
``api.plan()`` without it. Every plan came back at the ops layer's default of 6
steps no matter what the caller asked for, while ``plan_next`` has always
honoured the cap — a storage case has fourteen reachable tools, so eight of them
were unreachable through the only surface an agent has.

The note the tool returns made it worse rather than better: it ends with "pass a
larger max_steps to see them", which was advice to do the one thing that could
not work. A previous round documented the parameter as inert instead of
connecting it, which left the tool advertising a knob, the note recommending the
knob, and the docstring saying the knob is fake.

The tests below need a case with more reachable steps than the default, or they
prove nothing at all: with five available steps, ``max_steps=14`` and
``max_steps=6`` return the same list and a broken wiring passes. Each one asserts
the fixture is actually capable before asserting the behaviour.
"""

from __future__ import annotations

import pytest

from vmware_debug.mcp_server.server import build_server
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.plan import DEFAULT_MAX_STEPS
from vmware_debug.ops.cases.store import create_case

AT = "2026-08-28T09:15:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def case_id():
    """A storage case — the category with the deepest reachable step list."""
    return create_case(
        Scope(summary="vSAN datastore latency on cluster-01", determined_by="alarm 42"), at=AT
    ).case_id


@pytest.fixture
def case_plan(mcp_tool):
    return mcp_tool("case_plan")


@pytest.fixture
def mcp_tool():
    """Call a tool through the registered MCP function, not the ops layer.

    The defect lived entirely in the MCP wrapper — the ops layer was correct the
    whole time — so a test that calls ``plan_next`` directly cannot see it.
    """
    server = build_server()

    def call(name: str):
        tool = server._tool_manager._tools[name]
        return tool.fn

    return call


class TestMaxStepsReachesThePlanner:
    def test_the_fixture_has_more_steps_than_the_default_shows(self, case_id, case_plan):
        """Positive control. Without this the two tests below are vacuous."""
        default = case_plan(case_id=case_id)
        assert len(default["steps"]) == DEFAULT_MAX_STEPS
        assert default["held_back"] > 0, (
            "this case has nothing held back, so raising the cap could not "
            "change the answer and the tests below would pass on broken wiring"
        )

    def test_raising_the_cap_returns_more_steps(self, case_id, case_plan):
        raised = case_plan(case_id=case_id, max_steps=50)
        assert len(raised["steps"]) > DEFAULT_MAX_STEPS
        assert raised["held_back"] == 0

    def test_omitting_it_still_gets_the_default(self, case_id, case_plan):
        """Control: the default is the default, not 'whatever was passed last'."""
        assert len(case_plan(case_id=case_id)["steps"]) == DEFAULT_MAX_STEPS

    def test_lowering_the_cap_holds_more_back(self, case_id, case_plan):
        lowered = case_plan(case_id=case_id, max_steps=2)
        assert len(lowered["steps"]) == 2
        assert lowered["held_back"] > case_plan(case_id=case_id)["held_back"]


class TestTheDefaultIsOneNumberInOnePlace:
    """Three layers name this default. Only one of them may decide it.

    The MCP tool's signature, ``api.plan``'s signature and ``plan_next``'s
    signature each carry a default, and the tool's was the literal ``6`` typed
    out by hand. A number kept in three places by hand is three numbers.
    """

    def test_the_mcp_tool_default_is_the_planner_constant(self, mcp_tool):
        import inspect

        default = inspect.signature(mcp_tool("case_plan")).parameters["max_steps"].default
        assert default == DEFAULT_MAX_STEPS

    def test_the_api_layer_default_is_the_planner_constant(self, case_id):
        """Exercised directly: the MCP tool always passes an explicit value, so
        a wrong default here is invisible from the tool's side."""
        from vmware_debug.ops.cases import api

        assert len(api.plan(case_id)["steps"]) == DEFAULT_MAX_STEPS


class TestTheNoteTellsTheTruth:
    def test_the_note_advises_raising_the_cap_only_when_that_would_help(
        self, case_id, case_plan
    ):
        held = case_plan(case_id=case_id)
        assert held["held_back"] > 0
        assert "max_steps" in held["note"]

        exhausted = case_plan(case_id=case_id, max_steps=50)
        assert exhausted["held_back"] == 0
        assert "max_steps" not in exhausted["note"], (
            "the note offers a cap raise on a plan that is already complete"
        )

    def test_the_docstring_no_longer_says_the_parameter_is_inert(self, mcp_tool):
        """The schema an agent reads told it not to bother using this.

        `describe_tool_parameters` copies the ``Args:`` text into the JSON schema,
        so a stale "HAS NO EFFECT" sentence is not a comment — it is shipped
        instruction, and a model that reads it will never raise the cap.
        """
        doc = mcp_tool("case_plan").__doc__
        assert "HAS NO EFFECT" not in doc
        assert "never forwarded" not in doc


class TestOutOfRangeIsRefusedNotSilentlyObeyed:
    def test_zero_is_refused_rather_than_returning_an_empty_plan(self, case_id, case_plan):
        """An empty plan means "nothing left to fetch". It must not also mean
        "you passed a nonsense cap", because those need opposite responses."""
        result = case_plan(case_id=case_id, max_steps=0)
        assert "error" in result
        assert "max_steps" in result["error"]

    def test_negative_is_refused(self, case_id, case_plan):
        """``ordered[:-3]`` silently drops the last three steps — a plausible
        list, wrong contents, no error anywhere."""
        assert "error" in case_plan(case_id=case_id, max_steps=-3)

    def test_the_refusal_names_the_range(self, case_id, case_plan):
        error = case_plan(case_id=case_id, max_steps=0)["error"]
        assert "1" in error
