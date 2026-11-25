"""关账口径、已签发/处理中双视角与迟到更正重发判定的测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monthend.loader import load_dataset
from monthend.reports import MonthEnd, build_issued_report, build_working_package

try:
    from .fixtures import (
        DEFAULT_SITES,
        acceptance,
        attribution,
        close_rule,
        transfer,
        write_dataset,
    )
except ImportError:
    from fixtures import (
        DEFAULT_SITES,
        acceptance,
        attribution,
        close_rule,
        transfer,
        write_dataset,
    )

P = "2026-03"


def two_revision_tables(**rule_overrides):
    return {
        "sites": DEFAULT_SITES,
        "close": [close_rule(P, "2026-04-02", **rule_overrides)],
        "transfers": [
            transfer("T1", P, "L0", "L1", "O", "N", "join", 100, equiv=100),
        ],
        "acceptances": [
            acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-25", 100, 90, 10),
            acceptance("R1", 2, "L1", "CX", "2026-03-20", "2026-04-08", 100, 84, 16),
        ],
        "attribution": [
            attribution("R1", 2, "2026-04-04", "N", "0.4", "signed"),
            attribution("R1", 2, "2026-04-04", "S", "0.6", "signed"),
        ],
    }


class PeriodTests(unittest.TestCase):
    def _build(self, tables):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return MonthEnd(load_dataset(write_dataset(Path(tmp.name), tables)))

    def test_issued_uses_cutoff_working_uses_latest(self):
        month_end = self._build(two_revision_tables())
        self.assertEqual(month_end.book.issued_revision_set(P), {"R1": 1})
        self.assertEqual(month_end.book.working_revision_set(P), {"R1": 2})
        issued = build_issued_report(month_end, P)
        working = build_working_package(month_end, P)
        self.assertEqual(issued["pivots"]["region"][0]["first_pass"], 90)
        self.assertEqual(working["production_pivots"]["region"][0]["first_pass"], 84)
        # 对外只有约定维度，没有责任方
        self.assertEqual(set(issued["pivots"]), {"region", "customer"})
        self.assertNotIn("responsibility_pivot", issued)

    def test_material_correction_qualifies_for_reissue_in_window(self):
        month_end = self._build(two_revision_tables())  # 门槛 3，变化 6
        notice = month_end.book.late_corrections(P)[0]
        self.assertEqual(notice.delta_first_pass, -6)
        self.assertTrue(notice.affects_first_pass_qualification)
        self.assertTrue(notice.qualifies_for_reissue)
        self.assertEqual(notice.first_pass_issued, 90)
        self.assertEqual(notice.first_pass_new, 84)

    def test_subthreshold_correction_does_not_qualify(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(P, "2026-04-02", delta=10)],
            "transfers": [
                transfer("T1", P, "L0", "L1", "O", "N", "join", 100, equiv=100),
            ],
            "acceptances": [
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-25", 100, 90, 10),
                acceptance("R1", 2, "L1", "CX", "2026-03-20", "2026-04-08", 100, 84, 16),
            ],
        }
        month_end = self._build(tables)
        notice = month_end.book.late_corrections(P)[0]
        self.assertFalse(notice.qualifies_for_reissue)
        self.assertIn("小于重发门槛", notice.reason)

    def test_correction_after_reissue_window_is_flagged(self):
        tables = two_revision_tables(until="2026-04-05")
        month_end = self._build(tables)
        notice = month_end.book.late_corrections(P)[0]
        self.assertTrue(notice.affects_first_pass_qualification)
        self.assertFalse(notice.qualifies_for_reissue)
        self.assertFalse(notice.reissue_window_open)

    def test_attribution_signature_after_cutoff_keeps_issued_volume(self):
        month_end = self._build(two_revision_tables())
        changes = month_end.book.attribution_only_changes(P)
        self.assertEqual({row["owner"] for row in changes}, {"N", "S"})
        issued = build_issued_report(month_end, P)
        # 对外产量仍是旧口径 90，责任签字没有改它
        self.assertEqual(issued["pivots"]["region"][0]["first_pass"], 90)

    def test_unseen_receipt_at_cutoff_is_not_an_issued_reissue(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(P, "2026-04-02")],
            "transfers": [
                transfer("T1", P, "L0", "L1", "O", "N", "join", 10, equiv=10),
            ],
            "acceptances": [
                acceptance("R9", 1, "L1", "CX", "2026-03-20", "2026-04-10", 10, 8, 2),
            ],
        }
        month_end = self._build(tables)
        self.assertEqual(month_end.book.issued_revision_set(P), {})
        notice = month_end.book.late_corrections(P)[0]
        self.assertIsNone(notice.issued_revision)
        self.assertFalse(notice.qualifies_for_reissue)


if __name__ == "__main__":
    unittest.main()
