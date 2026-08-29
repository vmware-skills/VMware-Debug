"""Case identifiers: generation, and the validation that keeps a case id from
becoming a path.

A case id reaches the store from an MCP tool argument, so it is attacker-shaped
input in the family's sense: the model chooses it, and the model can be talked
into choosing something by the text it is reading. Everything below is the
boundary check that stands between that and the filesystem.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.ids import CaseIdError, new_case_id, validate_case_id


class TestGeneration:
    def test_is_stable_for_the_same_inputs(self):
        a = new_case_id("vSAN latency on cluster-01", at="2026-08-28T09:15:00Z")
        b = new_case_id("vSAN latency on cluster-01", at="2026-08-28T09:15:00Z")
        assert a == b

    def test_leads_with_the_timestamp_so_ls_sorts_chronologically(self):
        assert new_case_id("anything", at="2026-08-28T09:15:00Z").startswith("20260828-091500-")

    def test_carries_a_readable_slug_of_the_summary(self):
        cid = new_case_id("vSAN latency on cluster-01", at="2026-08-28T09:15:00Z")
        assert "vsan-latency" in cid

    def test_distinct_summaries_at_the_same_instant_do_not_collide(self):
        a = new_case_id("host psod", at="2026-08-28T09:15:00Z")
        b = new_case_id("datastore full", at="2026-08-28T09:15:00Z")
        assert a != b

    def test_generated_ids_always_pass_validation(self):
        for summary in ["../../etc/passwd", "a" * 400, "日志分析", "", "  ", "!!!"]:
            validate_case_id(new_case_id(summary, at="2026-08-28T09:15:00Z"))

    def test_a_summary_with_no_usable_characters_still_yields_an_id(self):
        cid = new_case_id("!!! ???", at="2026-08-28T09:15:00Z")
        validate_case_id(cid)
        assert cid.startswith("20260828-091500-")


class TestValidationRejectsPaths:
    @pytest.mark.parametrize(
        "bad",
        [
            "..",
            "../escape",
            "a/../../etc",
            "nested/case",
            "back\\slash",
            "/absolute",
            "~/home",
            ".hidden",
            "",
            "   ",
            "has space",
            "semi;colon",
            "null\x00byte",
            "newline\ncase",
            "a" * 200,
        ],
    )
    def test_rejects(self, bad):
        with pytest.raises(CaseIdError):
            validate_case_id(bad)

    def test_rejection_says_what_a_valid_id_looks_like(self):
        """A teaching error, per the family's error rules — the model that
        passed a bad id has to be able to fix it from the message alone."""
        with pytest.raises(CaseIdError) as e:
            validate_case_id("nested/case")
        msg = str(e.value)
        assert "nested/case" in msg
        assert "case_list" in msg

    def test_accepts_what_generation_produces(self):
        validate_case_id("20260828-091500-vsan-latency-on-cluster-01")

    def test_returns_the_id_so_it_can_be_used_inline(self):
        cid = "20260828-091500-vsan-latency"
        assert validate_case_id(cid) == cid
