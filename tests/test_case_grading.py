"""Conclusion grading — design section 3.

The design's central decision is mechanical, not procedural, so the first test
below asserts it by introspection: **`grade_case` has no grade parameter.** The
model cannot propose a conclusion level. It submits evidence and records gaps;
the level is recomputed from the ledger on every call.

That shape comes from vmware-harden v1.9.0, where 76 of 99 rules reported
compliance without ever having judged the host. The lesson there was that a
program left any route to announce its own verdict will take it. This closes
the route instead of asking the model not to use it.
"""

from __future__ import annotations

import inspect

import pytest

from vmware_debug.ops.cases.evidence import Evidence, Gap, record_evidence, record_gap
from vmware_debug.ops.cases.grading import grade_case, load_rules
from vmware_debug.ops.cases.hypotheses import add_hypothesis
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import create_case

AT = "2026-08-28T09:15:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def mounted_kb(tmp_path):
    """A knowledge entry that genuinely applies to the case fixture's scope.

    Submitting evidence labelled `knowledge-sr` used to be enough to confirm a
    case; the label did all the work. Now the entry has to exist and its
    applies_to has to match, so a test that wants Confirmed has to construct one
    — which is the point.
    """
    p = tmp_path / "vmware" / "knowledge" / "kb" / "KB-fixture.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nid: KB-fixture\napplies_to:\n  product: vsphere\n---\nbody\n")
    return p


@pytest.fixture
def case():
    """A case with H1 already registered.

    The gaps and observations below reference it, and a reference to an
    unregistered hypothesis is now refused — a dangling id used to block and
    falsify nothing while silently costing a grade level.
    """
    cid = create_case(
        Scope(
            summary="vsan latency", determined_by="alarm 42",
            product_versions={"vsphere": "8.0.3"},
        ),
        at=AT,
    ).case_id
    add_hypothesis(cid, "failing device")
    return cid


def evidence(skill="vmware-monitor", tool="list_events", **kw):
    # Knowledge evidence must say which entry it is; without that its
    # applicability cannot be checked and it is not decisive.
    if skill in ("knowledge-kb", "knowledge-sr") and "knowledge_entry_id" not in kw:
        kw["knowledge_entry_id"] = "KB-fixture"
    return Evidence(
        source_skill=skill,
        source_tool=tool,
        query={},
        fetched_at="2026-08-28T09:20:00Z",
        summary="s",
        **kw,
    )


def blocking_gap(**kw):
    """A missing observation that would *support* a hypothesis if obtained."""
    return Gap(
        what="SMART counters",
        why="no BMC path",
        blocks=kw.get("blocks", ("H1",)),
        could_falsify=kw.get("could_falsify", False),
        how_to_close="pull an iDRAC bundle",
    )


class TestTheModelCannotSetTheGrade:
    def test_grade_case_takes_no_grade_argument(self):
        params = set(inspect.signature(grade_case).parameters)
        assert not params & {"grade", "level", "conclusion", "verdict", "rca"}, (
            "grade_case grew a way to be told the answer. The grade must stay a "
            "function of the ledger."
        )

    def test_the_result_cites_the_evidence_it_used(self):
        r = grade_case(create_case(Scope(summary="x", determined_by="y"), at=AT).case_id)
        assert isinstance(r.reasons, tuple) and r.reasons


class TestPromotion:
    def test_an_empty_case_is_a_candidate(self, case):
        assert grade_case(case).grade == "candidate"

    def test_one_source_is_not_enough_for_probable(self, case):
        record_evidence(case, evidence(tool="list_events"))
        record_evidence(case, evidence(tool="list_alarms"))
        assert grade_case(case).grade == "candidate"

    def test_two_independent_sources_reach_probable(self, case):
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="vmware-log-insight"))
        assert grade_case(case).grade == "probable"

    def test_a_missing_confirmation_caps_the_grade_but_does_not_demote_it(self, case):
        """The PPT's own vSAN case is graded Probable while its SMART/NVMe gap
        is open — the gap is what stops it reaching Confirmed, not something
        that pushes it back to Candidate.

        Getting this wrong would make recording a gap cost two grades, which
        turns the honest act into the expensive one. The ledger only works if
        writing down what you could not get is free.
        """
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="vmware-log-insight"))
        record_gap(case, blocking_gap())
        r = grade_case(case)
        assert r.grade == "probable"
        assert any("G001" in reason for reason in r.reasons)

    def test_a_gap_that_could_overturn_the_hypothesis_does_hold_it_at_candidate(self, case):
        """The other kind: an observation that might prove the hypothesis
        wrong. Claiming Probable while that is outstanding claims a
        corroboration nobody has tested."""
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="vmware-log-insight"))
        record_gap(case, blocking_gap(could_falsify=True))
        assert grade_case(case).grade == "candidate"

    def test_an_informational_gap_does_not_hold_it_back(self, case):
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="vmware-log-insight"))
        record_gap(case, blocking_gap(blocks=()))
        assert grade_case(case).grade == "probable"


class TestConfirmedNeedsADecisiveSource:
    def test_ordinary_evidence_never_reaches_confirmed(self, case):
        for skill in ("vmware-monitor", "vmware-log-insight", "vmware-aria", "vmware-storage"):
            record_evidence(case, evidence(skill=skill))
        assert grade_case(case).grade == "probable"

    def test_a_decisive_source_reaches_confirmed(self, case, mounted_kb):
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="knowledge-sr"))
        assert grade_case(case).grade == "confirmed"

    def test_any_open_blocking_gap_stops_confirmed(self, case):
        """Confirmed is the grade that says no hole is left. Either kind of
        blocking gap is a hole."""
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="knowledge-sr"))
        record_gap(case, blocking_gap())
        assert grade_case(case).grade == "probable"

    def test_a_decisive_source_alone_is_still_not_confirmed(self, case):
        """Decisive is necessary, not sufficient — corroboration is still
        required, so a single vendor SR cannot carry a case on its own."""
        record_evidence(case, evidence(skill="knowledge-sr"))
        assert grade_case(case).grade == "candidate"

    def test_the_result_says_confirmed_is_unreachable_when_no_source_exists(self, case):
        """Today the family has no hardware-diagnostic channel and an empty
        knowledge library, so the ceiling is Probable. The grader has to say so
        rather than let the user wonder why it never goes higher."""
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="vmware-log-insight"))
        r = grade_case(case)
        assert r.ceiling == "probable"
        assert any("knowledge" in reason.lower() for reason in r.ceiling_reasons)


class TestExclusionNeedsAFalsifyingObservation:
    def test_absence_of_evidence_does_not_exclude(self, case):
        """'We looked and found nothing' is a gap. Reading it as an exclusion
        is the family's empty-result-means-no-problem shape, in the one place
        where it would change a conclusion."""
        record_gap(case, blocking_gap())
        assert grade_case(case).grade != "excluded"

    def test_a_falsifying_observation_excludes(self, case):
        record_evidence(case, evidence(skill="vmware-monitor", falsifies=("H1",)))
        record_evidence(case, evidence(skill="vmware-log-insight"))
        r = grade_case(case)
        assert r.grade == "excluded"
        assert any("E001" in reason for reason in r.reasons)


class TestDemotion:
    def test_a_new_gap_takes_confirmed_back_down_to_probable(self, case, mounted_kb):
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="knowledge-sr"))
        assert grade_case(case).grade == "confirmed"
        record_gap(case, blocking_gap())
        assert grade_case(case).grade == "probable"

    def test_a_falsifiable_gap_takes_probable_back_down_to_candidate(self, case):
        record_evidence(case, evidence(skill="vmware-monitor"))
        record_evidence(case, evidence(skill="vmware-log-insight"))
        assert grade_case(case).grade == "probable"
        record_gap(case, blocking_gap(could_falsify=True))
        assert grade_case(case).grade == "candidate"


class TestRules:
    def test_the_result_names_the_rules_file_it_used(self, case):
        r = grade_case(case)
        assert r.rules_source.endswith("grading_rules.yaml")
        assert r.rules_origin == "packaged-default"

    def test_a_site_file_replaces_a_block_wholesale(self, case, tmp_path):
        site = tmp_path / "vmware" / "investigation" / "grading_rules.yaml"
        site.parent.mkdir(parents=True)
        site.write_text(
            "grades:\n  probable:\n    min_independent_sources: 1\n    blocked_by_gaps: false\n"
        )
        record_evidence(case, evidence(skill="vmware-monitor"))
        r = grade_case(case)
        assert r.grade == "probable"
        assert r.rules_origin == "site"

    def test_the_packaged_rules_parse_and_cover_every_grade(self):
        rules, _, _ = load_rules()
        assert set(rules["grades"]) == {"probable", "confirmed", "excluded"}

    def test_a_broken_site_file_is_an_error_not_a_silent_fallback(self, tmp_path):
        site = tmp_path / "vmware" / "investigation" / "grading_rules.yaml"
        site.parent.mkdir(parents=True)
        site.write_text("grades: [this is a list not a mapping")
        with pytest.raises(ValueError, match="grading_rules.yaml"):
            load_rules()


class TestTheCeilingIsMeasuredNotAsserted:
    """The ceiling must move on its own once the missing layer is supplied.

    A ceiling hardcoded to 'probable' would keep reporting the same sentence
    after someone dropped a knowledge library in, and a status line that cannot
    become false is not a status line.
    """

    def test_an_empty_knowledge_library_caps_at_probable(self, case):
        r = grade_case(case)
        assert r.ceiling == "probable"
        assert any("empty" in x.lower() for x in r.ceiling_reasons)

    def test_a_populated_knowledge_library_raises_the_ceiling(self, case, tmp_path):
        kb = tmp_path / "vmware" / "knowledge" / "kb"
        kb.mkdir(parents=True)
        (kb / "KB-2026-0417.md").write_text("---\nid: KB-2026-0417\n---\nbody\n")
        r = grade_case(case)
        assert r.ceiling == "confirmed"

    def test_a_knowledge_directory_that_exists_but_is_empty_still_caps(self, case, tmp_path):
        """Creating the folder is not supplying the content — the family's
        empty-result shape, one directory up."""
        (tmp_path / "vmware" / "knowledge" / "kb").mkdir(parents=True)
        assert grade_case(case).ceiling == "probable"


class TestTheRulesFileHasNoDecorativeKnobs:
    """Found by mutation-testing the section-7 metrics.

    Changing `confirmed.requires` from `probable` to `candidate` in the rules
    file changed nothing: the grader never read the key, and the corroboration
    requirement came from control flow instead. A customer is expected to audit
    that file and may edit it — a setting that looks like a knob and is wired to
    nothing is worse than no setting, because it invites a change that appears
    to take effect.
    """

    def _site(self, tmp_path, body):
        p = tmp_path / "vmware" / "investigation" / "grading_rules.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)

    def test_relaxing_the_prerequisite_actually_relaxes_it(self, case, tmp_path, mounted_kb):
        """One decisive source, no corroboration: Confirmed under the relaxed
        rule, and not under the packaged one."""
        record_evidence(case, evidence(skill="knowledge-sr"))
        assert grade_case(case).grade == "candidate"

        self._site(
            tmp_path,
            (
                "grades:\n"
                "  confirmed:\n"
                "    requires: candidate\n"
                "    min_decisive_sources: 1\n"
                "    blocked_by_gaps: true\n"
                "    decisive_sources: [knowledge-sr]\n"
            ),
        )
        assert grade_case(case).grade == "confirmed"

    def test_the_packaged_prerequisite_still_holds(self, case):
        record_evidence(case, evidence(skill="knowledge-sr"))
        assert grade_case(case).grade != "confirmed"

    def test_an_unknown_prerequisite_is_refused_rather_than_ignored(self, case, tmp_path):
        self._site(
            tmp_path,
            (
                "grades:\n"
                "  confirmed:\n"
                "    requires: definitely\n"
                "    decisive_sources: [knowledge-sr]\n"
            ),
        )
        with pytest.raises(ValueError, match="definitely"):
            grade_case(case)
