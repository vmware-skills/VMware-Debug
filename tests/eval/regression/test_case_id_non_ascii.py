"""A case opened in Chinese got no identity of its own.

``slugify`` reduces on ``[^a-z0-9]+``, so a summary written entirely in Chinese
leaves nothing behind and the id falls back to the literal word ``case``:
``20260830-061014-case``. Two completely different investigations opened in the
same second then collide — and the collision message said they were "the same
summary", which was false, and sent the reporter looking for a duplicate that
did not exist.

The id has to stay inside its character set: it is a directory name, and
``validate_case_id`` is the only thing between a model-chosen string and
``open(root / case_id)``. So the fix is not to widen the alphabet. It is that an
id must distinguish summaries that differ, and that the collision message must
describe what actually collided.

The control is readability: an ASCII summary must still produce the readable
slug the listing was designed around, not a digest for everybody.
"""

from __future__ import annotations

import pytest

from vmware_debug.ops.cases.ids import new_case_id, validate_case_id
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import CaseExists, create_case

AT = "2026-08-30T06:10:14Z"

#: Both must be free of Latin characters. An earlier draft wrote the first one
#: as "四台 ESXi 主机…", and "esxi" survived slugify — so the two ids differed
#: whatever the fallback did, and the test passed against the defect it was
#: written to catch.
ZH_A = "四台主机同时进入维护模式后关机"
ZH_B = "存储集群延迟升高导致虚拟机无响应"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))


class TestANonAsciiSummaryStillGetsAnIdentity:
    def test_two_different_chinese_summaries_do_not_collide(self):
        assert new_case_id(ZH_A, AT) != new_case_id(ZH_B, AT)

    def test_the_id_is_still_a_safe_directory_name(self):
        validate_case_id(new_case_id(ZH_A, AT))

    def test_the_same_summary_still_yields_the_same_id(self):
        """Ids are replayable: a case folder re-opened from its scope has to
        reconstruct the id it already has."""
        assert new_case_id(ZH_A, AT) == new_case_id(ZH_A, AT)

    def test_both_cases_can_actually_be_opened_in_the_same_second(self):
        create_case(Scope(summary=ZH_A, determined_by="user report"), at=AT)
        create_case(Scope(summary=ZH_B, determined_by="user report"), at=AT)


class TestTheCollisionMessageIsTrue:
    def test_it_does_not_claim_the_summaries_were_identical(self):
        """Two summaries that differ only outside the id character set still
        collide, and telling the reporter they submitted the same summary sends
        them looking for something that is not there."""
        create_case(Scope(summary="vsan latency!", determined_by="a"), at=AT)
        with pytest.raises(CaseExists) as exc:
            create_case(Scope(summary="vsan latency?", determined_by="a"), at=AT)
        assert "same summary" not in str(exc.value)

    def test_it_says_what_to_do_about_it(self):
        create_case(Scope(summary="vsan latency", determined_by="a"), at=AT)
        with pytest.raises(CaseExists) as exc:
            create_case(Scope(summary="vsan latency", determined_by="a"), at=AT)
        assert "case_get" in str(exc.value)


class TestControlReadabilityIsNotSacrificed:
    def test_an_ascii_summary_still_gets_its_readable_slug(self):
        """A digest for every case would pass every test above and destroy the
        one property the slug exists for — a directory listing you can read."""
        assert new_case_id("vSAN datastore latency on cluster-01", AT).endswith(
            "-vsan-datastore-latency-on-cluster-01"
        )

    def test_a_mixed_summary_keeps_the_part_that_survives(self):
        assert "esxi05" in new_case_id("主机 esxi05 关机", AT)
