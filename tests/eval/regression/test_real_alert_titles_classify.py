"""Alert titles from a real estate, and what the taxonomy made of them.

On 2026-09-14 eight alerts were read off the lab vCenter 8.0.3 and Aria 8.18.7
and passed through ``triage``. Five came back ``uncategorized`` and the top
hypothesis was "uncategorized" itself. Unlike the residue measured in
``test_classification_against_vsphere_vocabulary`` — events whose subsystem the
taxonomy already had, spelled differently — these named subsystems it had no
category for at all:

    Root user password expired            credential lifecycle; auth read only
                                          login/token/certificate words
    License will soon expire              licensing — no category
    Expired vCenter Server license         licensing — no category
    Host TPM attestation alarm            hardware — routed in the catalogue,
                                          but the classifier could never emit it
    Objects are not receiving data        the monitoring pipeline itself — no
      from adapter instance               category

So the change is new categories with their own routing, not more synonyms for
the old ones. One title stays unread on purpose, and is pinned below.
"""

from __future__ import annotations

import pytest

from vmware_debug.envelope import normalize_event
from vmware_debug.ops.cases.readiness import readiness
from vmware_debug.ops.cases.sources import load_catalogue
from vmware_debug.ops.timeline import classification_coverage, classify_symptom

#: (alert title, source, the category it has to land in first)
LAB_ALERTS = (
    ("Root user password expired", "monitor", "auth"),
    ("License will soon expire", "aria", "licensing"),
    ("Expired vCenter Server license", "monitor", "licensing"),
    ("Host TPM attestation alarm", "monitor", "hardware"),
    ("Objects are not receiving data from adapter instance", "aria", "data_collection"),
    ("vCenter appliance health service is down", "aria", "platform"),
    ("Host connection and power state", "monitor", "host_lifecycle"),
)

#: A roll-up of its members' health. It names no subsystem: whatever is wrong is
#: wrong with one of the members, and any category given to the title would be
#: a guess dressed as a routing decision.
ROLLUP_ALERT = "Group population health is degraded"


def _event(title: str, source: str) -> object:
    return normalize_event(
        {"ts": "2026-09-14T08:00:00Z", "source": source, "severity": "warning", "text": title}
    )


@pytest.mark.parametrize(("title", "source", "category"), LAB_ALERTS)
def test_each_lab_alert_lands_in_its_category_first(title, source, category):
    event = _event(title, source)
    found = classify_symptom(event.text, event.entity, event.fields)
    assert found and found[0] == category, f"{title!r} -> {found}"


def test_the_population_rollup_stays_unread():
    """Pinned so a later keyword does not quietly start routing it somewhere."""
    assert classify_symptom(ROLLUP_ALERT) == []


def test_the_lab_stream_is_now_mostly_readable():
    events = [_event(t, s) for t, s, _c in LAB_ALERTS] + [_event(ROLLUP_ALERT, "aria")]
    coverage = classification_coverage(events)
    assert coverage["uncategorized"] == 1
    assert coverage["unmatched_samples"] == [ROLLUP_ALERT]


def test_a_new_category_does_not_steal_an_existing_match():
    """Controls: the words added for the new categories must not outrank what
    these texts were already about."""
    assert classify_symptom("datastore latency high on vsan-01")[0] == "storage"
    assert classify_symptom("Permission denied for user root@192.168.60.10")[0] == "auth"
    assert classify_symptom("Host esx01 entered maintenance mode")[0] == "host_lifecycle"


class TestReadiness:
    def test_aiops_is_a_recognised_skill(self):
        """It used to land in unrecognised_skills while routing pointed
        power_lifecycle cases at it."""
        out = readiness(["vmware-monitor", "vmware-aiops"])
        assert out["unrecognised_skills"] == []

    def test_aiops_lifts_power_lifecycle_to_probable(self):
        without = readiness(["vmware-monitor"])["categories"]["power_lifecycle"]
        with_aiops = readiness(["vmware-monitor", "vmware-aiops"])["categories"]["power_lifecycle"]
        assert without["ceiling"] == "candidate"
        assert with_aiops["ceiling"] == "probable"
        assert with_aiops["independent_sources"] == ["vmware-aiops", "vmware-monitor"]

    def test_aiops_classes_name_only_reads_aiops_does_itself(self):
        """AIops's cluster summary and investigation bundles call vmware-monitor's
        code. Listed under aiops they would be monitor's data counted as a second
        source — a Probable bought with one source under two names."""
        delegated = {
            "cluster_health_summary",
            "cross_vcenter_attention",
            "vm_investigation_bundle",
            "host_investigation_bundle",
            "datastore_investigation_bundle",
        }
        for name, spec in load_catalogue()["classes"].items():
            if spec.get("skill") == "vmware-aiops":
                tools = {e["tool"] for e in spec["tools"]}
                assert not tools & delegated, f"{name} lists delegated tools {tools & delegated}"

    @pytest.mark.parametrize("category", ["licensing", "data_collection"])
    def test_new_categories_reach_probable_with_monitor_and_aria(self, category):
        out = readiness(["vmware-monitor", "vmware-aria"])["categories"][category]
        assert out["ceiling"] == "probable"

    @pytest.mark.parametrize("category", ["licensing", "data_collection"])
    def test_one_skill_alone_is_still_one_source(self, category):
        out = readiness(["vmware-aria"])["categories"][category]
        assert out["ceiling"] == "candidate"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))
