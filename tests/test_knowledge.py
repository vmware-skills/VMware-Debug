"""The knowledge layer — what can be mounted, and what makes an entry decisive.

Until now `applies_to` existed only in prose. Nothing parsed a knowledge file
and nothing checked applicability, so any file at all under the knowledge root
raised the reported ceiling, and anything submitted as `knowledge-kb` counted as
decisive evidence. That is precisely the route to a wrong Confirmed the design
was written to close: a knowledge-base entry that looks right for the wrong
build is indistinguishable from a correct one by similarity, and similarity was
all that stood in the way.

The rule, stated once: **an entry is decisive only if its `applies_to` was
checked against this case's scope and passed.** Everything else — no
`applies_to`, a mismatched build, an unparseable file — is at most supporting.
"""

from __future__ import annotations

import json

import pytest

from vmware_debug.ops.cases.knowledge import (
    SUPPORTED_FORMATS,
    KnowledgeEntry,
    applies_to_scope,
    knowledge_status,
    load_knowledge,
)
from vmware_debug.ops.cases.model import Scope


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))
    return tmp_path / "vmware" / "knowledge"


def write(home, rel, body):
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


SCOPE = Scope(
    summary="vsan latency",
    determined_by="alarm",
    product_versions={"vsphere": "8.0.3", "driver.nvme_pcie": "1.2.6"},
)


class TestFormats:
    def test_the_table_is_not_empty_and_每个都有说明(self):
        assert SUPPORTED_FORMATS
        for f in SUPPORTED_FORMATS:
            assert f["extensions"] and f["how_metadata_travels"] and f["note"]

    def test_markdown_front_matter(self, home):
        write(
            home, "kb/KB-1.md", "---\nid: KB-1\napplies_to:\n  product: vsphere\n---\nbody text\n"
        )
        [e] = load_knowledge()
        assert e.entry_id == "KB-1"
        assert e.applies_to == {"product": "vsphere"}

    def test_yaml_entry(self, home):
        write(home, "kb/KB-2.yaml", "id: KB-2\napplies_to:\n  product: vsphere\n")
        assert load_knowledge()[0].entry_id == "KB-2"

    def test_json_entry(self, home):
        write(
            home, "sr/SR-9.json", json.dumps({"id": "SR-9", "applies_to": {"product": "vsphere"}})
        )
        assert load_knowledge()[0].entry_id == "SR-9"

    def test_jsonl_is_one_entry_per_line(self, home):
        write(
            home,
            "sr/export.jsonl",
            json.dumps({"id": "SR-1"}) + "\n" + json.dumps({"id": "SR-2"}) + "\n",
        )
        assert {e.entry_id for e in load_knowledge()} == {"SR-1", "SR-2"}

    def test_csv_is_one_entry_per_row(self, home):
        write(home, "kb/index.csv", "id,mechanism\nKB-7,firmware bug\nKB-8,driver bug\n")
        assert {e.entry_id for e in load_knowledge()} == {"KB-7", "KB-8"}

    def test_plain_text_takes_metadata_from_a_sibling_yaml(self, home):
        write(home, "runbook/reboot.txt", "1. drain\n2. reboot\n")
        write(home, "runbook/reboot.yaml", "id: RB-1\napplies_to:\n  product: vsphere\n")
        [e] = load_knowledge()
        assert e.entry_id == "RB-1" and "drain" in e.body

    def test_an_unreadable_file_is_reported_not_skipped(self, home):
        write(home, "kb/broken.yaml", "id: [unclosed\n")
        status = knowledge_status()
        assert status["unreadable"], "a corrupt entry vanished silently"
        assert "broken.yaml" in str(status["unreadable"])

    def test_an_unsupported_extension_is_reported_with_the_conversion_advice(self, home):
        write(home, "kb/vendor.pdf", "%PDF-1.4")
        status = knowledge_status()
        assert any("vendor.pdf" in str(u) for u in status["unsupported"])
        assert "markdown" in str(status["unsupported"]).lower()


class TestApplicability:
    def _entry(self, applies):
        return KnowledgeEntry(
            entry_id="KB-1", path="kb/KB-1.md", body="", applies_to=applies, source="kb"
        )

    def test_no_applies_to_is_never_decisive(self):
        verdict = applies_to_scope(self._entry({}), SCOPE)
        assert verdict.decisive is False
        assert "applies_to" in verdict.reason

    def test_a_matching_product_and_build_applies(self):
        v = applies_to_scope(self._entry({"product": "vsphere", "build": ">=8.0.0, <9.0"}), SCOPE)
        assert v.decisive is True

    def test_a_build_outside_the_range_does_not(self):
        v = applies_to_scope(self._entry({"product": "vsphere", "build": ">=9.0"}), SCOPE)
        assert v.decisive is False
        assert "8.0.3" in v.reason and "9.0" in v.reason

    def test_the_wrong_product_does_not(self):
        v = applies_to_scope(self._entry({"product": "nsx"}), SCOPE)
        assert v.decisive is False

    def test_a_driver_constraint_is_honoured(self):
        assert (
            applies_to_scope(
                self._entry(
                    {"product": "vsphere", "driver": {"name": "nvme_pcie", "version": ">=1.2.4"}},
                ),
                SCOPE,
            ).decisive
            is True
        )
        assert (
            applies_to_scope(
                self._entry(
                    {"product": "vsphere", "driver": {"name": "nvme_pcie", "version": ">=2.0"}},
                ),
                SCOPE,
            ).decisive
            is False
        )

    def test_a_constraint_the_scope_cannot_answer_is_not_a_match(self):
        """The scope says nothing about firmware. Treating silence as a pass is
        how a KB for the wrong hardware becomes decisive."""
        v = applies_to_scope(
            self._entry(
                {"product": "vsphere", "firmware": {"vendor": "dell", "version": ">=52.26"}}
            ),
            SCOPE,
        )
        assert v.decisive is False
        assert "firmware" in v.reason

    def test_a_non_matching_entry_is_still_offered_as_supporting(self):
        v = applies_to_scope(self._entry({"product": "nsx"}), SCOPE)
        assert v.decisive is False
        assert v.supporting is True, (
            "a mismatched entry may still be worth reading; it just cannot carry a conclusion"
        )


class TestStatus:
    def test_an_empty_library_says_so_and_lists_the_formats(self, home):
        s = knowledge_status()
        assert s["entries"] == 0
        assert s["formats"] == list(SUPPORTED_FORMATS)
        assert "applies_to" in s["note"]

    def test_it_counts_only_entries_that_could_be_decisive(self, home):
        write(home, "kb/ok.md", "---\nid: A\napplies_to:\n  product: vsphere\n---\nx\n")
        write(home, "kb/bare.md", "---\nid: B\n---\nx\n")
        s = knowledge_status()
        assert s["entries"] == 2
        assert s["with_applies_to"] == 1
        assert "1" in s["note"]


class TestAnUncheckedConstraintIsNeverAPass:
    """Caught in review.

    The checker understood product, build, driver and firmware, and treated
    every other key as satisfied. `build: '>=9.0'` with no product, a
    `hardware_model` list, and an outright typo all came back decisive — the
    entry stated a condition, nothing verified it, and the answer was yes.

    "Unknown constraint" and "satisfied constraint" must never be the same
    answer. That is the whole failure mode this layer exists to close, and the
    first version reintroduced it for every key it had not been taught.
    """

    def _e(self, applies):
        return KnowledgeEntry(entry_id="K", path="k.md", body="", applies_to=applies, source="kb")

    def test_a_build_constraint_without_a_product_is_not_a_pass(self):
        v = applies_to_scope(self._e({"build": ">=9.0"}), SCOPE)
        assert v.decisive is False
        assert "build" in v.reason

    def test_an_unimplemented_constraint_is_not_a_pass(self):
        v = applies_to_scope(self._e({"product": "vsphere", "hardware_model": ["R750"]}), SCOPE)
        assert v.decisive is False
        assert "hardware_model" in v.reason

    def test_an_unrecognised_key_is_not_a_pass(self):
        v = applies_to_scope(self._e({"product": "vsphere", "moon_phase": "waxing"}), SCOPE)
        assert v.decisive is False
        assert "moon_phase" in v.reason

    def test_the_reason_says_it_was_unchecked_not_that_it_failed(self):
        """The distinction matters to whoever wrote the entry: their constraint
        was not contradicted, it was not evaluated."""
        v = applies_to_scope(self._e({"product": "vsphere", "moon_phase": "x"}), SCOPE)
        assert "could not" in v.reason.lower() or "not checked" in v.reason.lower()

    def test_the_understood_keys_still_pass(self):
        assert (
            applies_to_scope(
                self._e(
                    {
                        "product": "vsphere",
                        "build": ">=8.0, <9.0",
                        "driver": {"name": "nvme_pcie", "version": ">=1.2.4"},
                    }
                ),
                SCOPE,
            ).decisive
            is True
        )
