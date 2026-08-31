"""Design section 7 — the four investigation metrics, as checks that can go red.

The reference deck proposes four: time to first useful evidence, key-evidence
recall, **wrong-Confirmed rate**, and next-step actionability. Three of them are
about the plan and are measurable here because the plan is derived, not
improvised. The fourth is measurable because a grade is a pure function of the
ledger, so a case that must not reach Confirmed can be built and checked with no
environment at all.

Wrong-Confirmed is the one that must be zero. A conclusion that overstates its
support is worse than no conclusion: it ends the investigation.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.evidence import Evidence, Gap, record_evidence, record_gap
from vmware_debug.ops.cases.grading import grade_case
from vmware_debug.ops.cases.hypotheses import add_hypothesis
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.plan import plan_next
from vmware_debug.ops.cases.sources import all_catalogue_tools, load_catalogue
from vmware_debug.ops.cases.store import create_case

AT = "2026-08-30T09:00:00Z"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


_SEQ = iter(range(1, 10_000))


def _case(summary="vSAN datastore latency on cluster-01"):
    """A fresh case per call.

    The summary is suffixed because ids are ``<timestamp>-<slug>`` and these
    loops open several within the same second — the store refuses to overwrite,
    which is right, so the tests have to ask for distinct cases rather than
    lean on a collision being tolerated.
    """
    cid = create_case(Scope(summary=f"{summary} {next(_SEQ)}", determined_by="test"), at=AT).case_id
    add_hypothesis(cid, "failing device")
    return cid


def _ev(cid, skill, **kw):
    return record_evidence(
        cid,
        Evidence(
            source_skill=skill,
            source_tool="probe",
            query={},
            fetched_at=AT,
            summary="s",
            **kw,
        ),
    )


# ── Metric 1: wrong-Confirmed rate — must be zero ──────────────────────────

#: Each entry is a case that MUST NOT reach Confirmed, and why. The similar-KB
#: case is the one worth having: a knowledge entry that looks right for the
#: wrong build is exactly how a wrong Confirmed gets manufactured, and it is
#: indistinguishable from a correct one by similarity alone.
_MUST_NOT_CONFIRM = [
    (
        "corroborated but nothing decisive",
        ["vmware-monitor", "vmware-log-insight", "vmware-aria", "vmware-storage"],
        [],
        False,
    ),
    ("one decisive source and no corroboration", ["knowledge-sr"], [], False),
    (
        "decisive source with a hole still open",
        ["vmware-monitor", "knowledge-sr"],
        [("SMART counters", False)],
        False,
    ),
    (
        "a gap that could overturn it",
        ["vmware-monitor", "knowledge-sr"],
        [("firmware change log", True)],
        False,
    ),
]


@pytest.mark.parametrize(
    ("label", "sources", "gaps", "_unused"),
    _MUST_NOT_CONFIRM,
    ids=[c[0] for c in _MUST_NOT_CONFIRM],
)
def test_wrong_confirmed_rate_is_zero(label, sources, gaps, _unused):
    cid = _case()
    for s in sources:
        _ev(cid, s)
    for what, could_falsify in gaps:
        record_gap(
            cid,
            Gap(
                what=what,
                why="unavailable",
                blocks=("H1",),
                could_falsify=could_falsify,
                how_to_close="ask the site",
            ),
        )
    grade = grade_case(cid).grade
    assert grade != "confirmed", f"{label!r} reached Confirmed — grade was {grade}"


def test_the_only_route_to_confirmed_is_the_intended_one(tmp_path):
    """The counterpart. A metric that can only fail is not measuring anything —
    if nothing can reach Confirmed, 'wrong-Confirmed rate is zero' is vacuous.

    Reaching it now takes a mounted entry whose applies_to matches, which is
    exactly the bar the design sets.
    """
    _kb(tmp_path, "KB-ok.md", "applies_to:\n  product: vsphere\n")
    cid = _scoped_case({"vsphere": "8.0.3"})
    _ev(cid, "vmware-monitor")
    _ev(cid, "knowledge-sr", knowledge_entry_id="KB-ok.md")
    assert grade_case(cid).grade == "confirmed"


def test_absence_of_evidence_never_excludes():
    """Excluded is a verdict, and 'we looked and found nothing' is not one."""
    cid = _case()
    _ev(cid, "vmware-monitor")
    _ev(cid, "vmware-log-insight")
    record_gap(
        cid, Gap(what="anything at all", why="not reachable", blocks=("H1",), how_to_close="ask")
    )
    assert grade_case(cid).grade != "excluded"


# ── Metric 2: key-evidence recall ──────────────────────────────────────────


#: symptom category -> the evidence classes an investigation of it must reach.
#: Taken from the routing table so this cannot drift away from what the planner
#: actually does; the assertion is that every REQUIRED class is offered.
def test_key_evidence_recall_is_total_for_every_category():
    cat = load_catalogue()
    for category, spec in cat["routing"].items():
        cid = _case()
        wanted = set(spec.get("supporting", []))
        offered: set[str] = set()
        # Ask repeatedly, submitting nothing: the plan must eventually offer
        # every reachable supporting class rather than a fixed first page.
        plan = plan_next(cid, category=category, max_steps=99)
        offered |= {s["evidence_class"] for s in plan["steps"]}
        offered |= {u["evidence_class"] for u in plan["unavailable"]}
        missing = wanted - offered
        assert not missing, (
            f"{category}: the plan neither offers nor reports {sorted(missing)} — "
            f"a required evidence class that is silently absent is one nobody "
            f"will think to look for"
        )


# ── Metric 3: time to first useful evidence ────────────────────────────────


def test_the_first_batch_reaches_the_category_specific_source():
    """Not wall-clock. The question is whether the FIRST plan already offers the
    source that actually answers this kind of incident, or buries it behind
    generic inventory."""
    expectations = {
        "storage": "vmware-storage",
        "network": "vmware-nsx",
        "configuration": "vmware-harden",
        "accelerator": "vmware-privateai",
        "kubernetes": "vmware-vks",
    }
    for category, skill in expectations.items():
        cid = _case()
        first = plan_next(cid, category=category)
        skills = [s["skill"] for s in first["steps"]]
        assert skill in skills, (
            f"{category}: the first plan is {skills} — {skill} is the source "
            f"that answers this category and it is not in the opening batch"
        )


def test_an_unclassified_incident_still_gets_a_first_move():
    """Not knowing the category is the normal starting state."""
    cid = _case("something is wrong")
    assert plan_next(cid)["steps"]


# ── Metric 4: next-step actionability ──────────────────────────────────────


def test_every_planned_step_is_structurally_executable():
    """A step an agent cannot carry out is worse than no step: it is followed,
    it fails, and nothing about the failure points back at the plan."""
    known = set(all_catalogue_tools())
    for category in load_catalogue()["routing"]:
        cid = _case()
        for step in plan_next(cid, category=category, max_steps=99)["steps"]:
            assert step["skill"] and step["tool"], f"{category}: incomplete step {step}"
            assert (step["skill"], step["tool"]) in known, (
                f"{category}: plans {step['skill']}.{step['tool']}, which the "
                f"catalogue does not list — family_smoke checks the catalogue "
                f"against the live registries, so this would be a phantom tool"
            )
            assert step["purpose"], f"{category}: {step['tool']} has no stated purpose"


def test_every_unreachable_source_carries_a_remedy():
    """The other half of actionability. Reporting a missing source without
    saying how to supply it is a complaint, not a next step."""
    for category in load_catalogue()["routing"]:
        cid = _case()
        for u in plan_next(cid, category=category)["unavailable"]:
            assert u["how_to_supply"], f"{category}: {u['evidence_class']} has no remedy"


# ── Metric 1, continued: the route the design says must not exist ──────────
#
# Added after the knowledge layer was implemented. Until then `applies_to` was
# prose: any file at all raised the ceiling and anything labelled `knowledge-kb`
# counted as decisive. The wrong-Confirmed metric passed only because no fixture
# exercised the one route a wrong Confirmed actually takes.


def _kb(tmp_path, name, applies_block):
    p = tmp_path / "vmware" / "knowledge" / "kb" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nid: {name}\n{applies_block}---\nbody\n", encoding="utf-8")


def _scoped_case(versions):
    cid = create_case(
        Scope(
            summary=f"scoped {next(_SEQ)}",
            determined_by="test",
            product_versions=versions,
        ),
        at=AT,
    ).case_id
    add_hypothesis(cid, "failing device")
    return cid


def test_a_knowledge_entry_for_the_wrong_build_cannot_confirm(tmp_path):
    """The canonical wrong Confirmed. The entry is real, well-formed, and about
    the right product — and it is for 9.x while this estate runs 8.0.3."""
    _kb(tmp_path, "KB-1.md", "applies_to:\n  product: vsphere\n  build: '>=9.0'\n")
    cid = _scoped_case({"vsphere": "8.0.3"})
    _ev(cid, "vmware-monitor")
    _ev(cid, "knowledge-kb")
    assert grade_case(cid).grade != "confirmed"


def test_a_knowledge_entry_with_no_applies_to_cannot_confirm(tmp_path):
    _kb(tmp_path, "KB-2.md", "")
    cid = _scoped_case({"vsphere": "8.0.3"})
    _ev(cid, "vmware-monitor")
    _ev(cid, "knowledge-kb")
    assert grade_case(cid).grade != "confirmed"


def test_a_matching_entry_does_confirm(tmp_path):
    """The counterpart, so the rule above is a filter and not a wall."""
    _kb(tmp_path, "KB-3.md", "applies_to:\n  product: vsphere\n  build: '>=8.0, <9.0'\n")
    cid = _scoped_case({"vsphere": "8.0.3"})
    _ev(cid, "vmware-monitor")
    _ev(cid, "knowledge-kb", knowledge_entry_id="KB-3.md")
    assert grade_case(cid).grade == "confirmed"


def test_citing_the_wrong_entry_does_not_borrow_a_matching_one(tmp_path):
    """Caught reviewing the applicability filter.

    The first version passed when ANY mounted entry applied, so an agent citing
    the entry written for 9.x could confirm a case about 8.0.3 as long as some
    unrelated entry happened to match. "An entry is decisive only if ITS
    applies_to passed" was true of the docstring and not of the code.

    Knowledge evidence now has to say which entry it is, and that entry is the
    one checked.
    """
    _kb(tmp_path, "KB-right.md", "applies_to:\n  product: vsphere\n  build: '>=8.0, <9.0'\n")
    _kb(tmp_path, "KB-wrong.md", "applies_to:\n  product: vsphere\n  build: '>=9.0'\n")
    cid = _scoped_case({"vsphere": "8.0.3"})
    _ev(cid, "vmware-monitor")
    _ev(cid, "knowledge-kb", knowledge_entry_id="KB-wrong.md")
    assert grade_case(cid).grade != "confirmed"


def test_citing_the_right_entry_confirms(tmp_path):
    _kb(tmp_path, "KB-right.md", "applies_to:\n  product: vsphere\n  build: '>=8.0, <9.0'\n")
    _kb(tmp_path, "KB-wrong.md", "applies_to:\n  product: vsphere\n  build: '>=9.0'\n")
    cid = _scoped_case({"vsphere": "8.0.3"})
    _ev(cid, "vmware-monitor")
    _ev(cid, "knowledge-kb", knowledge_entry_id="KB-right.md")
    assert grade_case(cid).grade == "confirmed"


def test_knowledge_evidence_that_names_no_entry_is_not_decisive(tmp_path):
    """Without the id there is nothing to check, and 'something applicable
    exists somewhere' is not the same claim."""
    _kb(tmp_path, "KB-right.md", "applies_to:\n  product: vsphere\n---\n")
    cid = _scoped_case({"vsphere": "8.0.3"})
    _ev(cid, "vmware-monitor")
    _ev(cid, "knowledge-kb")
    r = grade_case(cid)
    assert r.grade != "confirmed"
    assert any("which entry" in x for x in r.reasons), r.reasons
