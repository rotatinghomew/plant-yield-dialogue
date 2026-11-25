"""复盘单生命周期：覆盖不重发、结案扩大再开、改派、记录保留。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monthend.loader import load_dataset
from monthend.reports import MonthEnd

try:
    from .fixtures import (
        DEFAULT_SITES,
        acceptance,
        attribution,
        close_rule,
        review_voucher,
        transfer,
        write_dataset,
    )
except ImportError:
    from fixtures import (
        DEFAULT_SITES,
        acceptance,
        attribution,
        close_rule,
        review_voucher,
        transfer,
        write_dataset,
    )

P = "2026-03"


def tables_with(acceptance_rows, attribution_rows=(), review_rows=()):
    return {
        "sites": DEFAULT_SITES,
        "close": [close_rule(P, "2026-04-02")],
        "transfers": [
            transfer("T1", P, "L0", "L1", "O", "N", "join", 100, equiv=100),
        ],
        "acceptances": acceptance_rows,
        "attribution": list(attribution_rows),
        "review": list(review_rows),
    }


class ReviewTests(unittest.TestCase):
    def _build(self, tables):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        month_end = MonthEnd(load_dataset(write_dataset(Path(tmp.name), tables)))
        revisions = month_end.book.working_revision_set(P)
        return month_end, month_end.board.evaluate_period(P, revisions)

    def test_open_difference_covered_by_new_evidence_is_not_reopened(self):
        tables = tables_with(
            [acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-22", 10, 9, 1)],
            [attribution("R1", 1, "2026-03-25", "N", "1", "signed")],
            [review_voucher("V1", "R1", "N", "2026-03-22", "", 1)],
        )
        _, result = self._build(tables)
        state = result["voucher_states"][0]
        self.assertEqual(state["state"], "RESOLVED_BY_EVIDENCE")
        self.assertEqual(result["new_review_items"], [])

    def test_closed_voucher_reopens_when_quantity_expands(self):
        tables = tables_with(
            [
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-22", 10, 9, 1),
                acceptance("R1", 2, "L1", "CX", "2026-03-20", "2026-04-08", 10, 5, 5),
            ],
            [attribution("R1", 2, "2026-04-08", "N", "1", "signed")],
            [review_voucher("V1", "R1", "N", "2026-03-22", "2026-03-30", 1)],
        )
        _, result = self._build(tables)
        state = result["voucher_states"][0]
        self.assertEqual(state["state"], "REOPEN")
        # 原记录保留，新单只补残余 4 件，不重复覆盖原 1 件
        new_items = result["new_review_items"]
        self.assertEqual(len(new_items), 1)
        self.assertEqual(new_items[0]["qty"], 4)
        self.assertEqual(state["covered_qty"], 1)

    def test_responsibility_moved_to_other_owner_reopens_and_redirects(self):
        tables = tables_with(
            [
                acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-22", 10, 9, 1),
                acceptance("R1", 2, "L1", "CX", "2026-03-20", "2026-04-08", 10, 9, 1),
            ],
            [
                attribution("R1", 1, "2026-03-22", "N", "1", "provisional"),
                attribution("R1", 2, "2026-04-08", "S", "1", "signed"),
            ],
            [review_voucher("V1", "R1", "N", "2026-03-22", "2026-03-30", 1)],
        )
        _, result = self._build(tables)
        state = result["voucher_states"][0]
        self.assertEqual(state["state"], "REOPEN")
        self.assertIn("改交另一厂", state["reason"])
        new_owners = {item["owner"]: item["qty"] for item in result["new_review_items"]}
        self.assertEqual(new_owners, {"S": 1})

    def test_unclaimed_and_unallocatable_each_open_own_items(self):
        tables = tables_with(
            [acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-22", 10, 0, 10)],
            [attribution("R1", 1, "2026-03-25", "N", "0.5", "signed")],
        )
        _, result = self._build(tables)
        buckets = {item["bucket"]: item["qty"] for item in result["new_review_items"]}
        self.assertEqual(buckets["signed"], 5)
        self.assertEqual(buckets["unclaimed"], 5)
        self.assertNotIn("unallocatable", buckets)

    def test_no_review_without_any_attribution(self):
        tables = tables_with(
            [acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-22", 10, 8, 2)],
        )
        _, result = self._build(tables)
        items = result["new_review_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["bucket"], "unallocatable")
        self.assertEqual(items[0]["qty"], 2)

    def test_new_item_points_back_to_pivot_cell(self):
        tables = tables_with(
            [acceptance("R1", 1, "L1", "CX", "2026-03-20", "2026-03-22", 10, 8, 2)],
            [attribution("R1", 1, "2026-03-25", "N", "1", "signed")],
        )
        _, result = self._build(tables)
        ref = result["new_review_items"][0]["cell_ref"]
        self.assertEqual(ref["customer"], "CX")
        self.assertEqual(ref["region"], "north")
        self.assertEqual(ref["batch"], "L1")


if __name__ == "__main__":
    unittest.main()
