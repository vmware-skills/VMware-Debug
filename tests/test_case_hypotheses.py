"""The hypothesis ledger — step 06, and the registry the rest already assumed.

Gaps carry `blocks=["H1"]` and evidence carries `falsifies=["H1"]`, and until now
nothing created H1. A typo'd id silently blocked nothing and silently falsified
nothing: the grade came out one level higher than the investigator intended, and
no output said why. Registering hypotheses closes that.

Two properties do the work:

* **A reference to an unknown hypothesis is refused, not ignored.** That is the
  whole reason this exists — a dangling id is the family's empty-result shape
  wearing an identifier.
* **Support and refutation are counted from the ledger, never asserted.** A
  hypothesis does not get to say it is well-supported; the evidence pointing at
  it says so, the same way a case does not get to state its own grade.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.evidence import Evidence, Gap, record_evidence, record_gap
from vmware_debug.ops.cases.hypotheses import (
    HypothesisNotFound,
    add_hypothesis,
    hypothesis_ledger,
    load_hypotheses,
)
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import case_dir, create_case

AT = "2026-08-30T09:00:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


@pytest.fixture
def case():
    return create_case(Scope(summary="vsan latency", determined_by="alarm 42"), at=AT).case_id


def ev(case, skill="vmware-monitor", **kw):
    return record_evidence(
        case,
        Evidence(
            source_skill=skill,
            source_tool="t",
            query={},
            fetched_at=AT,
            summary="s",
            **kw,
        ),
    )


class TestRegistering:
    def test_ids_are_sequential_and_readable(self, case):
        assert add_hypothesis(case, "failing NVMe device").hypothesis_id == "H1"
        assert add_hypothesis(case, "noisy neighbour VM").hypothesis_id == "H2"

    def test_a_hypothesis_must_state_something(self, case):
        with pytest.raises(ValueError, match="statement"):
            add_hypothesis(case, "   ")

    def test_it_lands_in_hypotheses_md_as_readable_text(self, case):
        add_hypothesis(case, "failing NVMe device")
        body = (case_dir(case) / "hypotheses.md").read_text(encoding="utf-8")
        assert "H1" in body and "failing NVMe device" in body
        assert "_Empty." not in body

    def test_they_survive_a_reload(self, case):
        add_hypothesis(case, "one")
        add_hypothesis(case, "two")
        assert [h.statement for h in load_hypotheses(case)] == ["one", "two"]


class TestDanglingReferencesAreRefused:
    def test_a_gap_cannot_block_a_hypothesis_that_does_not_exist(self, case):
        with pytest.raises(HypothesisNotFound, match="H9"):
            record_gap(case, Gap(what="x", why="y", blocks=("H9",), how_to_close="z"))

    def test_evidence_cannot_falsify_a_hypothesis_that_does_not_exist(self, case):
        with pytest.raises(HypothesisNotFound, match="H9"):
            ev(case, falsifies=("H9",))

    def test_the_refusal_says_which_ids_exist(self, case):
        add_hypothesis(case, "failing NVMe device")
        with pytest.raises(HypothesisNotFound) as e:
            ev(case, falsifies=("H7",))
        assert "H1" in str(e.value)
        assert "case_hypotheses" in str(e.value)

    def test_a_registered_id_is_accepted(self, case):
        add_hypothesis(case, "failing NVMe device")
        record_gap(case, Gap(what="x", why="y", blocks=("H1",), how_to_close="z"))
        assert ev(case, falsifies=("H1",)).evidence_id == "E001"

    def test_referencing_nothing_stays_legal(self, case):
        """Most evidence supports no particular hypothesis and blocks nothing."""
        assert ev(case).evidence_id == "E001"
        record_gap(case, Gap(what="x", why="y", blocks=(), how_to_close="z"))


class TestTheLedgerIsComputed:
    def test_a_new_hypothesis_has_nothing_pointing_at_it(self, case):
        add_hypothesis(case, "failing NVMe device")
        [h] = hypothesis_ledger(case)
        assert h["refuted_by"] == [] and h["blocked_by"] == []
        assert h["status"] == "open"

    def test_a_falsifying_observation_marks_it_refuted(self, case):
        add_hypothesis(case, "failing NVMe device")
        ev(case, falsifies=("H1",))
        [h] = hypothesis_ledger(case)
        assert h["refuted_by"] == ["E001"]
        assert h["status"] == "refuted"

    def test_a_blocking_gap_is_reported_against_it(self, case):
        add_hypothesis(case, "failing NVMe device")
        record_gap(
            case,
            Gap(what="SMART", why="no BMC", blocks=("H1",), how_to_close="pull an iDRAC bundle"),
        )
        [h] = hypothesis_ledger(case)
        assert h["blocked_by"] == ["G001"]
        assert h["status"] == "blocked"

    def test_refuted_outranks_blocked(self, case):
        """An observation that rules it out settles the question; a missing
        measurement no longer matters."""
        add_hypothesis(case, "failing NVMe device")
        record_gap(case, Gap(what="SMART", why="no BMC", blocks=("H1",), how_to_close="x"))
        ev(case, falsifies=("H1",))
        assert hypothesis_ledger(case)[0]["status"] == "refuted"

    def test_each_entry_carries_its_next_step(self, case):
        add_hypothesis(case, "failing NVMe device")
        record_gap(
            case,
            Gap(
                what="SMART counters",
                why="no BMC path",
                blocks=("H1",),
                how_to_close="pull an iDRAC bundle",
            ),
        )
        [h] = hypothesis_ledger(case)
        assert "iDRAC" in " ".join(h["next_steps"])

    def test_an_open_hypothesis_with_nothing_missing_says_what_to_do(self, case):
        add_hypothesis(case, "failing NVMe device")
        [h] = hypothesis_ledger(case)
        assert h["next_steps"], "an open hypothesis with no next step is a dead end"
