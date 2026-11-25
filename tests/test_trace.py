"""领料路由、拆并批与可追溯检验的测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monthend.loader import load_dataset
from monthend.reports import MonthEnd
from monthend.trace import build_traces

try:
    from .fixtures import (
        DEFAULT_SITES,
        acceptance,
        attribution as attr_row,
        check,
        close_rule,
        transfer,
        write_dataset,
    )
except ImportError:  # unittest discover 以顶层模块方式装载
    from fixtures import (
        DEFAULT_SITES,
        acceptance,
        attribution as attr_row,
        check,
        close_rule,
        transfer,
        write_dataset,
    )

PERIOD = "2026-03"


class TraceTests(unittest.TestCase):
    def _build(self, tables):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = write_dataset(Path(tmp.name), tables)
        return MonthEnd(load_dataset(directory))

    def test_split_allocates_qty_to_each_destination(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(PERIOD, "2026-04-02")],
            "transfers": [
                transfer("T1", PERIOD, "L0", "L1", "O", "N", "cut", 60, equiv=60),
                transfer("T2", PERIOD, "L1", "L2", "N", "N", "join", 40, equiv=40),
                transfer("T3", PERIOD, "L1", "L3", "N", "S", "join", 20, equiv=20),
            ],
            "acceptances": [
                acceptance("R1", 1, "L2", "CX", "2026-03-20", "2026-03-20", 40, 38, 2),
                acceptance("R2", 1, "L3", "CY", "2026-03-21", "2026-03-21", 20, 19, 1),
            ],
        }
        month_end = self._build(tables)
        traces = month_end.traces
        self.assertEqual(traces["L2"].comparable_equiv, 40)
        self.assertEqual(traces["L3"].comparable_equiv, 20)
        self.assertEqual(traces["L2"].delivery_site, "N")
        self.assertEqual(traces["L3"].delivery_site, "S")
        # 拆分后两条路径回到共同原批
        self.assertEqual(traces["L2"].inbound[0].path, ("T1", "T2"))
        self.assertEqual(traces["L3"].inbound[0].path, ("T1", "T3"))

    def test_merge_keeps_each_original_lot_failure(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(PERIOD, "2026-04-02")],
            "transfers": [
                transfer("T1", PERIOD, "L0", "L1", "O", "N", "cut", 40, equiv=40),
                transfer("T2", PERIOD, "M0", "L2", "O", "S", "cut", 20, equiv=20),
                transfer("T3", PERIOD, "L1", "L9", "N", "N", "join", 40, equiv=40),
                transfer("T4", PERIOD, "L2", "L9", "S", "N", "join", 20, equiv=20),
            ],
            "checks": [
                check("K1", "T3", "L1", "2026-03-18", 2, 12),
                check("K2", "T4", "L2", "2026-03-18", 3, 20),
            ],
            "acceptances": [
                acceptance("R1", 1, "L9", "CX", "2026-03-22", "2026-03-22", 60, 58, 2),
            ],
        }
        month_end = self._build(tables)
        trace = month_end.traces["L9"]
        failures = {check_id: amount for check_id, amount in [
            (c.id, c.first_failed) for c, _ in trace.traceable_failures
        ]}
        # 两条原批的失败都在，合并没有抹掉任何一条
        self.assertEqual(failures, {"K1": 2, "K2": 3})
        self.assertEqual(trace.comparable_equiv, 60)

    def test_missing_conversion_is_exposed_not_zeroed(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(PERIOD, "2026-04-02")],
            "transfers": [
                transfer("T1", PERIOD, "L0", "L1", "O", "E", "melt", 12, unit="kg"),
            ],
        }
        month_end = self._build(tables)
        trace = month_end.traces["L1"]
        self.assertEqual(trace.comparable_equiv, 0)
        self.assertTrue(trace.has_comparable_volume is False)
        self.assertEqual(len(trace.incomparable), 1)
        exposed = month_end.engine.incomparable_volume(PERIOD)
        self.assertEqual(exposed[0]["qty"], 12)
        self.assertEqual(exposed[0]["unit"], "kg")

    def test_rework_from_incomparable_source_is_unallocatable(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(PERIOD, "2026-04-02")],
            "transfers": [
                # 一半有凭据、一半缺凭据，验收 10 件中 4 件返修
                transfer("T1", PERIOD, "L0", "L1", "O", "N", "cut", 5, equiv=5),
                transfer("T2", PERIOD, "M0", "L1", "O", "N", "melt", 5, unit="kg"),
            ],
            "acceptances": [
                acceptance("R1", 1, "L1", "CX", "2026-03-22", "2026-03-22", 10, 6, 4),
            ],
            "attribution": [
                attr_row("R1", 1, "2026-03-25", "N", "1", "signed"),
            ],
        }
        month_end = self._build(tables)
        view = month_end.ledger.latest_view("R1")
        # 50% 缺凭据 -> 4 件返修里 2 件无法分摊，其余 2 件正式确认
        self.assertEqual(view.unallocatable, 2)
        self.assertEqual(view.signed, 2)
        self.assertEqual(view.unclaimed, 0)

    def test_routing_cycle_is_rejected(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(PERIOD, "2026-04-02")],
            "transfers": [
                transfer("T1", PERIOD, "L0", "L1", "O", "N", "cut", 5, equiv=5),
                transfer("T2", PERIOD, "L1", "L0", "N", "O", "cut", 5, equiv=5),
            ],
        }
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = write_dataset(Path(tmp.name), tables)
        dataset = load_dataset(directory)
        with self.assertRaisesRegex(ValueError, "环"):
            build_traces(dataset)

    def test_routing_gap_when_equiv_does_not_match_acceptance(self):
        tables = {
            "sites": DEFAULT_SITES,
            "close": [close_rule(PERIOD, "2026-04-02")],
            "transfers": [
                transfer("T1", PERIOD, "L0", "L1", "O", "N", "cut", 50, equiv=50),
            ],
            "acceptances": [
                acceptance("R1", 1, "L1", "CX", "2026-03-22", "2026-03-22", 60, 58, 2),
            ],
        }
        month_end = self._build(tables)
        revisions = {"R1": 1}
        gaps = month_end.engine.routing_gaps(PERIOD, revisions)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["delta"], -10)


if __name__ == "__main__":
    unittest.main()
