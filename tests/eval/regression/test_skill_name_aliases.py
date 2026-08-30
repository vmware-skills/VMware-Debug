"""One skill, two spellings, and nothing reconciling them.

``case_readiness(available_skills=["monitor", "aria"])`` reported every evidence
class unavailable and advised installing vmware-monitor — which was installed.
The names come from this repo's own documents: references/event-envelope.md
spells the sources ``monitor`` / ``aria`` / ``loginsight``, while
rules/evidence_sources.yaml spells them ``vmware-monitor`` / ``vmware-aria`` /
``vmware-log-insight``. A caller who reads one and calls the other is told to
install what they already have, and following that advice does not help.

The same mismatch is worth more than a bad hint one layer down. The grader
counts independent sources as distinct ``source_skill`` strings, so an agent
that submits ``monitor`` for one item and ``vmware-monitor`` for another buys a
promotion to Probable with a single skill — corroboration invented out of
spelling. That is the outcome the whole grading layer exists to prevent, so it
is pinned here too.

Controls: a name that really is unknown must not be quietly absorbed into "not
installed" — it has to be reported — and two genuinely different skills must
still count as two.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.evidence import Evidence, record_evidence
from vmware_debug.ops.cases.grading import grade_case
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.readiness import readiness
from vmware_debug.ops.cases.store import create_case
from vmware_debug.ops.skill_names import canonical_skill_key, resolve_available_skills

AT = "2026-08-30T09:00:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


def _submit(case, skill):
    record_evidence(
        case,
        Evidence(
            source_skill=skill, source_tool="get_events", query={}, fetched_at=AT, summary="s"
        ),
    )


class TestTheEnvelopeSpellingIsUnderstood:
    def test_the_short_names_from_the_envelope_doc_resolve(self):
        for short, full in (
            ("monitor", "vmware-monitor"),
            ("aria", "vmware-aria"),
            ("loginsight", "vmware-log-insight"),
            ("nsx-security", "vmware-nsx-security"),
        ):
            assert canonical_skill_key(short) == canonical_skill_key(full), short

    def test_readiness_accepts_them(self):
        r = readiness(available_skills=["monitor", "aria"])
        assert r["classes"]["virtualization_state"]["available"]
        assert r["classes"]["metrics_and_anomalies"]["available"]

    def test_the_ceiling_matches_the_full_spelling(self):
        short = readiness(available_skills=["monitor", "aria"])
        full = readiness(available_skills=["vmware-monitor", "vmware-aria"])
        assert short["categories"] == full["categories"]


class TestSpellingDoesNotBuyCorroboration:
    def test_two_spellings_of_one_skill_count_as_one_source(self):
        case = create_case(Scope(summary="four hosts down", determined_by="alarm"), at=AT).case_id
        _submit(case, "monitor")
        _submit(case, "vmware-monitor")
        result = grade_case(case)
        assert result.grade == "candidate", result.reasons

    def test_it_says_that_is_what_happened(self):
        case = create_case(Scope(summary="four hosts down", determined_by="alarm"), at=AT).case_id
        _submit(case, "monitor")
        _submit(case, "vmware-monitor")
        joined = " ".join(grade_case(case).reasons)
        assert "monitor" in joined and "one source" in joined

    def test_control_two_different_skills_still_count_as_two(self):
        case = create_case(Scope(summary="four hosts down", determined_by="alarm"), at=AT).case_id
        _submit(case, "vmware-monitor")
        _submit(case, "aria")
        assert grade_case(case).grade == "probable"


class TestAnUnknownNameIsReportedNotAbsorbed:
    def test_a_typo_comes_back_named(self):
        """Otherwise it reads as 'that skill is not installed', which is the
        empty-result shape: a name nobody recognised and a name nobody has are
        the same answer."""
        r = readiness(available_skills=["vmware-moniter"])
        assert "vmware-moniter" in r["unrecognised_skills"]

    def test_the_note_points_at_it(self):
        assert "unrecognised" in readiness(available_skills=["vmware-moniter"])["note"].lower()

    def test_control_a_recognised_name_is_not_listed_as_unrecognised(self):
        assert readiness(available_skills=["monitor"])["unrecognised_skills"] == []

    def test_control_omitting_the_argument_still_assumes_everything(self):
        assert readiness()["unrecognised_skills"] == []
        assert readiness()["classes"]["storage"]["available"]


class TestResolutionIsExplicit:
    def test_it_returns_both_halves(self):
        resolved, unknown = resolve_available_skills(
            ["monitor", "vmware-vdi"], ("vmware-monitor", "vmware-aria")
        )
        assert resolved == {"vmware-monitor"}
        assert unknown == ("vmware-vdi",)

    def test_none_means_no_narrowing_at_all(self):
        resolved, unknown = resolve_available_skills(None, ("vmware-monitor",))
        assert resolved is None
        assert unknown == ()
