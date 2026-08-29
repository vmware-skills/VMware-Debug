"""The evidence-gathering plan — what to fetch next, and why.

Design section 6's other half. Three properties carry the weight:

* **Executable structure, not prose.** Every step names a skill, a tool, and
  what it is for. A model handed a paragraph will improvise; a model handed
  {skill, tool, purpose} calls the tool.
* **It changes as the case does.** A plan that returns the same list on every
  call is a checklist, and a checklist cannot say what to do about the evidence
  already in hand.
* **An empty plan is never silent.** "Nothing left to fetch" and "nothing here
  can be fetched" are opposite situations that look identical if the answer is
  just an empty list.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.evidence import Evidence, record_evidence
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.plan import plan_next
from vmware_debug.ops.cases.store import create_case

AT = "2026-08-28T09:15:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


def a_case(summary="vSAN datastore latency on cluster-01", **kw):
    return create_case(Scope(summary=summary, determined_by="alarm 42", **kw), at=AT).case_id


def submitted(case, skill, tool="probe"):
    record_evidence(
        case,
        Evidence(
            source_skill=skill,
            source_tool=tool,
            query={},
            fetched_at="2026-08-28T09:20:00Z",
            summary="s",
        ),
    )


class TestCategoryInference:
    def test_the_scope_summary_picks_the_category(self):
        assert plan_next(a_case("vSAN datastore latency"))["category"] == "storage"

    def test_a_network_summary_picks_network(self):
        assert (
            plan_next(a_case("DFW rule dropping traffic to segment web"))["category"] == "network"
        )

    def test_an_unrecognised_summary_says_so_instead_of_guessing(self):
        r = plan_next(a_case("something is weird"))
        assert r["category"] is None
        assert "category" in r["note"].lower()

    def test_an_unrecognised_summary_still_returns_a_useful_first_move(self):
        """Not knowing the category is the normal starting state, not a dead
        end. Broad state is what you fetch when you do not know yet."""
        r = plan_next(a_case("something is weird"))
        assert r["steps"], "no plan at all for an unclassified incident"
        assert any(s["skill"] == "vmware-monitor" for s in r["steps"])

    def test_an_explicit_category_overrides_inference(self):
        r = plan_next(a_case("vSAN datastore latency"), category="network")
        assert r["category"] == "network"

    def test_an_unknown_explicit_category_is_refused_with_the_known_ones(self):
        with pytest.raises(ValueError, match="storage"):
            plan_next(a_case(), category="not-a-category")


class TestStepsAreExecutable:
    def test_each_step_names_a_skill_a_tool_and_a_purpose(self):
        for step in plan_next(a_case())["steps"]:
            assert step["skill"] and step["tool"] and step["purpose"]

    def test_each_step_says_which_evidence_class_it_serves(self):
        for step in plan_next(a_case())["steps"]:
            assert step["evidence_class"]

    def test_storage_plans_reach_the_storage_skill(self):
        skills = {s["skill"] for s in plan_next(a_case())["steps"]}
        assert "vmware-storage" in skills

    def test_steps_carry_the_time_window_from_the_scope(self):
        case = a_case(window_start="2026-08-28T08:00:00Z", window_end="2026-08-28T09:00:00Z")
        for step in plan_next(case)["steps"]:
            assert step["window"] == {
                "start": "2026-08-28T08:00:00Z",
                "end": "2026-08-28T09:00:00Z",
            }


class TestItRespondsToWhatIsAlreadyIn:
    def test_a_covered_class_drops_out_of_the_plan(self):
        case = a_case()
        before = {s["evidence_class"] for s in plan_next(case)["steps"]}
        assert "storage" in before
        submitted(case, "vmware-storage")
        after = {s["evidence_class"] for s in plan_next(case)["steps"]}
        assert "storage" not in after

    def test_covered_classes_are_reported_rather_than_silently_dropped(self):
        case = a_case()
        submitted(case, "vmware-storage")
        assert "storage" in plan_next(case)["already_covered"]

    def test_the_plan_empties_as_the_case_fills_and_says_why(self):
        case = a_case()
        for skill in ("vmware-monitor", "vmware-log-insight", "vmware-storage"):
            submitted(case, skill)
        r = plan_next(case)
        assert r["steps"] == []
        assert "grade" in r["note"].lower() or "case_grade" in r["note"]


class TestUnavailableSourcesAreNamedNotOmitted:
    def test_the_decisive_source_it_cannot_reach_is_reported(self):
        r = plan_next(a_case())
        classes = {u["evidence_class"] for u in r["unavailable"]}
        assert "hardware" in classes

    def test_each_unavailable_entry_says_how_to_supply_it(self):
        for u in plan_next(a_case())["unavailable"]:
            assert u["how_to_supply"]

    def test_narrowing_available_skills_moves_a_class_to_unavailable(self):
        r = plan_next(a_case(), available_skills=["vmware-monitor"])
        classes = {u["evidence_class"] for u in r["unavailable"]}
        assert "storage" in classes
        assert all(s["skill"] == "vmware-monitor" for s in r["steps"])

    def test_the_reachable_ceiling_travels_with_the_plan(self):
        assert plan_next(a_case())["ceiling"] == "probable"


class TestThePlanIsActionableNotExhaustive:
    """A plan is what to do next. Fourteen steps is a menu, and a model handed a
    menu calls every item on it — most of them irrelevant, all of them billed.
    """

    def test_the_default_plan_is_small(self):
        assert len(plan_next(a_case())["steps"]) <= 6

    def test_every_class_is_represented_before_any_class_repeats(self):
        """Round-robin, not depth-first. One tool from each of three skills is
        worth more than three tools from one — corroboration is counted in
        distinct sources, so breadth is what actually moves the grade."""
        steps = plan_next(a_case())["steps"][:3]
        assert len({s["evidence_class"] for s in steps}) == 3

    def test_what_was_held_back_is_stated_not_silently_dropped(self):
        r = plan_next(a_case())
        assert r["held_back"] > 0
        assert str(r["held_back"]) in r["note"]

    def test_the_cap_can_be_raised(self):
        assert len(plan_next(a_case(), max_steps=50)["steps"]) > 6

    def test_nothing_is_held_back_once_the_cap_exceeds_what_exists(self):
        assert plan_next(a_case(), max_steps=50)["held_back"] == 0
