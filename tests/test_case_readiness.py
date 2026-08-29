"""Readiness — design section 5.

Answers one question, before the investigation rather than after it stalls:
**with what you have, how strong a conclusion can you reach?**

Two design choices are asserted here because both are easy to lose:

* **Per evidence class, never one overall score.** "Readiness 78%" cannot be
  acted on. "Storage cases reach Probable, hardware cases reach Candidate" can.
* **Every unreachable class says how to supply it.** A readiness report that
  only lists what is missing is a complaint; naming the next action is what
  makes it a plan.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.sources import (
    evidence_classes,
    load_catalogue,
    tools_for_class,
)
from vmware_debug.ops.cases.readiness import readiness


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


class TestCatalogue:
    def test_it_parses_and_is_not_empty(self):
        cat = load_catalogue()
        assert cat["classes"] and cat["routing"]

    def test_every_routed_class_is_a_class_that_exists(self):
        """A routing entry naming a class the catalogue does not define would
        silently contribute nothing to readiness — the family's empty-result
        shape, inside a data file."""
        cat = load_catalogue()
        known = set(cat["classes"])
        for category, spec in cat["routing"].items():
            for kind in ("supporting", "decisive"):
                unknown = set(spec.get(kind, [])) - known
                assert not unknown, f"{category}.{kind} names unknown class(es): {unknown}"

    def test_every_class_declares_whether_it_is_decisive(self):
        for name, spec in load_catalogue()["classes"].items():
            assert isinstance(spec.get("decisive"), bool), name

    def test_a_class_with_no_tools_must_explain_itself(self):
        """An empty tool list is a claim about the world, so it has to say why
        and what would close it. Silently empty would read as 'not written yet'."""
        for name, spec in load_catalogue()["classes"].items():
            if not spec.get("tools"):
                assert spec.get("absent_because"), f"{name} is empty with no reason"
                assert spec.get("how_to_supply"), f"{name} is empty with no remedy"

    def test_every_tool_entry_names_a_tool_and_says_what_it_gives(self):
        for name, spec in load_catalogue()["classes"].items():
            for entry in spec.get("tools", []):
                assert entry.get("tool"), f"{name} has a tool entry with no name"
                assert entry.get("gives"), f"{name}.{entry.get('tool')} says nothing"

    def test_tools_for_class_returns_skill_qualified_names(self):
        got = tools_for_class("logs")
        assert ("vmware-log-insight", "log_search") in got

    def test_an_unknown_class_raises_rather_than_returning_nothing(self):
        with pytest.raises(ValueError, match="evidence_classes"):
            tools_for_class("no-such-class")

    def test_evidence_classes_lists_them_all(self):
        assert "hardware" in evidence_classes()
        assert "knowledge" in evidence_classes()


class TestReadiness:
    def test_reports_per_category_not_one_number(self):
        r = readiness()
        assert set(r["categories"]) == set(load_catalogue()["routing"])
        assert "score" not in r

    def test_storage_reaches_probable_but_not_confirmed(self):
        """The reference case: vSphere can corroborate a failing device, and
        cannot confirm one."""
        cat = readiness()["categories"]["storage"]
        assert cat["ceiling"] == "probable"
        assert "hardware" in cat["missing_decisive"]

    def test_configuration_drift_can_reach_confirmed(self):
        """The one category whose decisive source the family actually has:
        vmware-harden judges each node against a baseline itself."""
        assert readiness()["categories"]["configuration"]["ceiling"] == "confirmed"

    def test_a_category_with_no_supporting_source_available_caps_at_candidate(self):
        r = readiness(available_skills=["vmware-harden"])
        assert r["categories"]["network"]["ceiling"] == "candidate"

    def test_narrowing_available_skills_narrows_the_answer(self):
        wide = readiness()["categories"]["storage"]["ceiling"]
        narrow = readiness(available_skills=["vmware-monitor"])["categories"]["storage"]["ceiling"]
        assert wide == "probable" and narrow == "candidate"

    def test_the_degraded_path_is_offered_when_the_primary_is_absent(self):
        """No Log Insight does not mean no logs — vmware-monitor can scan hosts
        one at a time. Slower and narrower, but the difference between a thin
        answer and none."""
        r = readiness(available_skills=["vmware-monitor", "vmware-storage"])
        logs = r["classes"]["logs"]
        assert logs["available"] is True
        assert logs["degraded"] is True
        assert "host_log_scan" in str(logs["via"])

    def test_every_unavailable_class_says_how_to_supply_it(self):
        for name, spec in readiness()["classes"].items():
            if not spec["available"]:
                assert spec["how_to_supply"], f"{name} is unavailable with no remedy"

    def test_the_knowledge_class_is_available_once_entries_are_mounted(self, tmp_path):
        assert readiness()["classes"]["knowledge"]["available"] is False
        kb = tmp_path / "vmware" / "knowledge" / "kb"
        kb.mkdir(parents=True)
        (kb / "KB-1.md").write_text("---\napplies_to: {}\n---\nbody\n")
        assert readiness()["classes"]["knowledge"]["available"] is True

    def test_hardware_stays_unavailable_no_matter_what_is_installed(self):
        """It is not a configuration problem. No skill in the family reaches
        below ESXi, so no combination of installed skills supplies it."""
        every = [c.get("skill") for c in load_catalogue()["classes"].values() if c.get("skill")]
        assert readiness(available_skills=every)["classes"]["hardware"]["available"] is False


class TestReadinessAgreesWithTheGrader:
    """The one property that makes a readiness report worth reading.

    Readiness promises a ceiling; the grader decides whether a case gets there.
    If they count differently, readiness lies — and the first version did, by
    counting evidence *classes* where the grader counts distinct source skills.
    Without Log Insight, vmware-monitor supplies both virtualisation state and
    degraded log scanning: two classes, one skill, and a case built from them
    can never leave Candidate no matter how much evidence it holds.
    """

    def test_a_promised_probable_is_actually_reachable(self, tmp_path):
        from vmware_debug.ops.cases.evidence import Evidence, record_evidence
        from vmware_debug.ops.cases.grading import grade_case
        from vmware_debug.ops.cases.model import Scope
        from vmware_debug.ops.cases.store import create_case

        for category, spec in readiness()["categories"].items():
            if spec["ceiling"] == "candidate":
                continue
            case = create_case(
                Scope(summary=f"reachability probe {category}", determined_by="test"),
                at="2026-08-28T09:15:00Z",
            ).case_id
            for skill in spec["independent_sources"]:
                record_evidence(
                    case,
                    Evidence(
                        source_skill=skill,
                        source_tool="probe",
                        query={},
                        fetched_at="2026-08-28T09:20:00Z",
                        summary="probe",
                    ),
                )
            got = grade_case(case).grade
            assert got != "candidate", (
                f"readiness promised {category} could reach {spec['ceiling']}, "
                f"but a case built from exactly its independent sources "
                f"{spec['independent_sources']} grades {got}."
            )

    def test_one_skill_behind_two_classes_is_reported_as_one_source(self):
        r = readiness(available_skills=["vmware-monitor"])["categories"]["storage"]
        assert r["independent_sources"] == ["vmware-monitor"]
        assert r["ceiling"] == "candidate"


def test_every_classifier_category_has_a_routing_entry():
    """Two taxonomies of the same thing drift, and this pair would drift
    silently: a symptom category the classifier can emit but the catalogue does
    not route produces an EMPTY plan, which reads as "nothing to check" at the
    exact moment an investigation is supposed to begin.

    The reverse is allowed — a routed category with no keywords yet can still be
    selected explicitly.
    """
    from vmware_debug.ops.timeline import known_categories

    routed = set(load_catalogue()["routing"])
    missing = set(known_categories()) - routed
    assert not missing, (
        f"ops/timeline.py can classify {sorted(missing)} but evidence_sources.yaml "
        f"routes nowhere for them. Add a routing entry, or the plan for those "
        f"incidents comes back empty."
    )
