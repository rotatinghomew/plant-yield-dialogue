"""多周期接续：同一期间的后续确认与下个关账周期都能在同一引擎上处理。"""

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


class MultiPeriodTests(unittest.TestCase):
    def test_next_period_and_follow_up_confirmation(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [
                close_rule("2026-03", "2026-04-02"),
                close_rule("2026-05", "2026-06-03", delta=2, until="2026-06-19"),
            ],
            "transfers": [
                transfer("T1", "2026-03", "L0", "L1", "O", "N", "join", 10, equiv=10),
                transfer("T2", "2026-05", "M0", "M1", "O", "S", "cut", 20, equiv=20),
            ],
            "acceptances": [
                # 3 月回执：关账后还有一次后续确认（rev2）
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-25", 10, 9, 1),
                acceptance("R1", 2, "L1", "CX", "2026-03-20", "2026-04-25", 10, 7, 3),
                # 5 月新周期
                acceptance("R2", 1, "M1", "CY", "2026-05-10", "2026-05-11", 20, 18, 2),
            ],
            "attribution": [
                attribution("R1", 2, "2026-04-25", "S", "1", "signed"),
                attribution("R2", 1, "2026-05-12", "S", "1", "signed"),
            ],
        }
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        month_end = MonthEnd(load_dataset(write_dataset(Path(tmp.name), tables)))

        self.assertEqual(month_end.periods(), ["2026-03", "2026-05"])

        # 3 月：已签发停留在 rev1；后续确认落在重发窗口（04-20）之后，只进复盘
        issued_mar = build_issued_report(month_end, "2026-03")
        self.assertEqual(issued_mar["revision_set"], {"R1": 1})
        notice = issued_mar["late_corrections"][0]
        self.assertEqual(notice["new_revision"], 2)
        self.assertFalse(notice["qualifies_for_reissue"])
        working_mar = build_working_package(month_end, "2026-03")
        self.assertEqual(working_mar["revision_set"], {"R1": 2})

        # 5 月：新周期独立成表，不串期间
        working_may = build_working_package(month_end, "2026-05")
        self.assertEqual(working_may["revision_set"], {"R2": 1})
        cell = working_may["production_pivots"]["region"][0]
        self.assertEqual((cell["key"], cell["qty"], cell["first_pass"]), ("south", 20, 18))
        self.assertEqual(
            working_may["responsibility_detail"][0]["signed"], 2
        )
        # 3 月的回执不出现在 5 月透视里
        self.assertNotIn("R1", str(working_may["production_pivots"]))

    def test_unknown_period_rejected(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule("2026-03", "2026-04-02")],
            "transfers": [
                transfer("T1", "2026-03", "L0", "L1", "O", "N", "cut", 10, equiv=10),
            ],
            "acceptances": [
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-25", 10, 9, 1),
            ],
        }
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        month_end = MonthEnd(load_dataset(write_dataset(Path(tmp.name), tables)))
        with self.assertRaises(KeyError):
            build_working_package(month_end, "2099-01")


if __name__ == "__main__":
    unittest.main()
