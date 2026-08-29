"""Recording a grade — append-only, including when it goes down.

A conclusion that quietly rewrites itself is worth less than one that never
claimed anything. The value of the ledger is that it can show it was wrong
earlier and say what changed its mind, so every write here is an append and the
demotions are as visible as the promotions.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.conclusion import grade_history, record_grade
from vmware_debug.ops.cases.evidence import Evidence, Gap, record_evidence, record_gap
from vmware_debug.ops.cases.grading import grade_case
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import case_dir, create_case, load_case

AT = "2026-08-28T09:15:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def case():
    return create_case(Scope(summary="vsan latency", determined_by="alarm 42"), at=AT).case_id


def ev(skill):
    return Evidence(
        source_skill=skill,
        source_tool="t",
        query={},
        fetched_at="2026-08-28T09:20:00Z",
        summary="s",
    )


def corroborate(case):
    record_evidence(case, ev("vmware-monitor"))
    record_evidence(case, ev("vmware-log-insight"))


class TestRecording:
    def test_the_grade_lands_in_the_case_index(self, case):
        record_grade(case, grade_case(case), at="2026-08-28T09:30:00Z")
        assert load_case(case).grade == "candidate"

    def test_conclusion_md_gains_an_entry(self, case):
        record_grade(case, grade_case(case), at="2026-08-28T09:30:00Z")
        body = (case_dir(case) / "conclusion.md").read_text()
        assert "candidate" in body.lower()
        assert "2026-08-28T09:30:00Z" in body

    def test_the_entry_names_the_rules_that_produced_it(self, case):
        record_grade(case, grade_case(case), at="2026-08-28T09:30:00Z")
        assert "grading_rules.yaml" in (case_dir(case) / "conclusion.md").read_text()

    def test_the_placeholder_is_gone_once_a_grade_exists(self, case):
        assert "Not graded yet" in (case_dir(case) / "conclusion.md").read_text()
        record_grade(case, grade_case(case), at="2026-08-28T09:30:00Z")
        assert "Not graded yet" not in (case_dir(case) / "conclusion.md").read_text()


class TestHistoryIsAppendOnly:
    def test_a_second_grade_does_not_replace_the_first(self, case):
        record_grade(case, grade_case(case), at="2026-08-28T09:30:00Z")
        corroborate(case)
        record_grade(case, grade_case(case), at="2026-08-28T10:00:00Z")
        assert [h.grade for h in grade_history(case)] == ["candidate", "probable"]

    def test_a_demotion_is_recorded_as_a_demotion(self, case):
        corroborate(case)
        record_grade(case, grade_case(case), at="2026-08-28T10:00:00Z")
        record_gap(
            case,
            Gap(
                what="firmware change log",
                why="no access",
                blocks=("H1",),
                could_falsify=True,
                how_to_close="ask the site for vLCM history",
            ),
        )
        record_grade(case, grade_case(case), at="2026-08-28T10:30:00Z")
        latest = grade_history(case)[-1]
        assert latest.grade == "candidate"
        assert latest.previous == "probable"
        assert latest.direction == "down"

    def test_an_unchanged_grade_is_still_recorded_but_marked_unchanged(self, case):
        record_grade(case, grade_case(case), at="2026-08-28T09:30:00Z")
        record_grade(case, grade_case(case), at="2026-08-28T09:40:00Z")
        assert grade_history(case)[-1].direction == "unchanged"

    def test_the_first_grade_has_no_previous(self, case):
        record_grade(case, grade_case(case), at="2026-08-28T09:30:00Z")
        first = grade_history(case)[0]
        assert first.previous is None
        assert first.direction == "initial"

    def test_every_entry_survives_in_the_file(self, case):
        for i, at in enumerate(["09:30", "09:40", "09:50"]):
            record_grade(case, grade_case(case), at=f"2026-08-28T{at}:00Z")
        body = (case_dir(case) / "conclusion.md").read_text()
        for at in ["09:30", "09:40", "09:50"]:
            assert f"2026-08-28T{at}:00Z" in body

    def test_history_is_empty_before_anything_is_graded(self, case):
        assert grade_history(case) == ()
