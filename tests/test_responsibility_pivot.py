"""责任四类口径与透视重算的测试。"""

from __future__ import annotations

import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from monthend.loader import load_dataset
from monthend.reports import MonthEnd

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


def base_tables():
    return {
        "sites": DEFAULT_SITES,
        "close": [close_rule(P, "2026-04-02")],
        "transfers": [
            transfer("T1", P, "L0", "L1", "O", "N", "join", 100, equiv=100),
        ],
        "acceptances": [
            acceptance("R1", 1, "L1", "CX", "2026-03-22", "2026-03-22", 100, 90, 10),
        ],
    }


class ResponsibilityTests(unittest.TestCase):
    def _build(self, tables):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return MonthEnd(load_dataset(write_dataset(Path(tmp.name), tables)))

    def test_four_buckets_sum_to_rework(self):
        tables = base_tables()
        tables["attribution"] = [
            attribution("R1", 1, "2026-03-25", "N", "0.5", "signed"),
            attribution("R1", 1, "2026-03-25", "S", "0.2", "provisional"),
        ]
        view = self._build(tables).ledger.latest_view("R1")
        self.assertEqual(view.signed, 5)
        self.assertEqual(view.provisional, 2)
        self.assertEqual(view.unclaimed, 3)
        self.assertEqual(view.unallocatable, 0)
        self.assertEqual(
            view.signed + view.provisional + view.unclaimed + view.unallocatable,
            view.rework_qty,
        )

    def test_no_attribution_is_fully_unallocatable(self):
        view = self._build(base_tables()).ledger.latest_view("R1")
        self.assertEqual(view.unallocatable, 10)
        self.assertEqual(view.allocations, ())

    def test_provisional_is_not_signed(self):
        tables = base_tables()
        tables["attribution"] = [
            attribution("R1", 1, "2026-03-25", "N", "1", "provisional"),
        ]
        view = self._build(tables).ledger.latest_view("R1")
        self.assertEqual(view.provisional, 10)
        self.assertEqual(view.signed, 0)

    def test_share_over_one_rejected_at_load(self):
        tables = base_tables()
        tables["attribution"] = [
            attribution("R1", 1, "2026-03-25", "N", "0.7", "signed"),
            attribution("R1", 1, "2026-03-25", "S", "0.5", "signed"),
        ]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with self.assertRaisesRegex(ValueError, "份额合计超过 1"):
            load_dataset(write_dataset(Path(tmp.name), tables))

    def test_responsibility_pivot_has_no_fpy_but_keeps_gap(self):
        tables = base_tables()
        tables["attribution"] = [
            attribution("R1", 1, "2026-03-25", "N", "0.7", "signed"),
        ]
        month_end = self._build(tables)
        cells = month_end.engine.responsibility_pivot(P, {"R1": 1})
        by_key = {cell.key: cell for cell in cells}
        self.assertIsNone(by_key["N"].fpy)
        self.assertEqual(by_key["N"].signed, 7)
        self.assertEqual(by_key["UNASSIGNED"].unclaimed, 3)
        self.assertEqual(by_key["UNASSIGNED"].gap, 3)
        # 责任方的交付份额与其责任份额是两件事
        production = {
            cell.key: cell
            for cell in month_end.engine.production_pivot(P, "site", {"R1": 1})
        }
        self.assertEqual(production["N"].qty, 100)
        self.assertEqual(production["N"].fpy, Fraction(90, 100))


class PivotRecomputeTests(unittest.TestCase):
    def _build(self, tables):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return MonthEnd(load_dataset(write_dataset(Path(tmp.name), tables)))

    def test_rates_recomputed_from_numerator_denominator(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(P, "2026-04-02")],
            "transfers": [
                transfer("T1", P, "L0", "L1", "O", "N", "cut", 20, equiv=20),
                transfer("T2", P, "M0", "L2", "O", "S", "cut", 80, equiv=80),
            ],
            "acceptances": [
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-20", 20, 10, 10),
                acceptance("R2", 1, "L2", "CX", "2026-03-21", "2026-03-21", 80, 80, 0),
            ],
        }
        month_end = self._build(tables)
        cells = month_end.engine.production_pivot(P, "region", {"R1": 1, "R2": 1})
        # 两个区域各自独立；合并层级 90/100，而非 50% 与 100% 的平均
        by_key = {cell.key: cell for cell in cells}
        self.assertEqual(by_key["north"].fpy, Fraction(10, 20))
        self.assertEqual(by_key["south"].fpy, Fraction(80, 80))
        customer = month_end.engine.production_pivot(
            P, "customer", {"R1": 1, "R2": 1}
        )
        self.assertEqual(customer[0].fpy, Fraction(90, 100))

    def test_customer_pivot_only_counts_that_customers_units(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(P, "2026-04-02")],
            "transfers": [
                transfer("T1", P, "L0", "L1", "O", "N", "cut", 20, equiv=20),
                transfer("T2", P, "M0", "L2", "O", "N", "cut", 30, equiv=30),
            ],
            "acceptances": [
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-20", 20, 18, 2),
                acceptance("R2", 1, "L2", "CY", "2026-03-21", "2026-03-21", 30, 27, 3),
            ],
        }
        month_end = self._build(tables)
        cells = {
            cell.key: cell
            for cell in month_end.engine.production_pivot(
                P, "customer", {"R1": 1, "R2": 1}
            )
        }
        self.assertEqual(cells["CX"].qty, 20)
        self.assertEqual(cells["CY"].qty, 30)
        self.assertTrue(
            month_end.engine.reconcile(P, {"R1": 1, "R2": 1})["balanced"]
        )

    def test_incomparable_volume_never_enters_denominator(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(P, "2026-04-02")],
            "transfers": [
                transfer("T1", P, "L0", "L1", "O", "E", "melt", 99, unit="kg"),
                transfer("T2", P, "M0", "L2", "O", "N", "cut", 10, equiv=10),
            ],
            "acceptances": [
                acceptance("R1", 1, "L2", "CX", "2026-03-21", "2026-03-21", 10, 9, 1),
            ],
        }
        month_end = self._build(tables)
        cell = month_end.engine.production_pivot(P, "region", {"R1": 1})[0]
        self.assertEqual(cell.qty, 10)  # 99kg 没有混进分母
        self.assertEqual(len(month_end.engine.incomparable_volume(P)), 1)

    def test_duplicate_acceptance_flagged_without_deducting_volume(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(P, "2026-04-02")],
            "transfers": [
                transfer("T1", P, "L0", "L1", "O", "N", "cut", 10, equiv=10),
            ],
            "acceptances": [
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-20", 10, 9, 1),
                acceptance("R2", 1, "L1", "CY", "2026-03-21", "2026-03-21", 10, 8, 2),
            ],
        }
        month_end = self._build(tables)
        flags = month_end.engine.duplicate_acceptances(
            P, {"R1": 1, "R2": 1}
        )
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["receipts"], ["R1", "R2"])
        # 识别之外不扣量：两张回执仍各自完整进透视
        cells = month_end.engine.production_pivot(
            P, "customer", {"R1": 1, "R2": 1}
        )
        self.assertEqual(sum(cell.qty for cell in cells), 20)

    def test_revisions_are_replacements_not_duplicates(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(P, "2026-04-02")],
            "transfers": [
                transfer("T1", P, "L0", "L1", "O", "N", "cut", 10, equiv=10),
            ],
            "acceptances": [
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-20", 10, 9, 1),
                acceptance("R1", 2, "L1", "CX", "2026-03-20", "2026-03-25", 10, 8, 2),
            ],
        }
        month_end = self._build(tables)
        self.assertEqual(
            month_end.engine.duplicate_acceptances(P, {"R1": 2}), []
        )


if __name__ == "__main__":
    unittest.main()
