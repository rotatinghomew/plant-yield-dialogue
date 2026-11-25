"""对仓库自带合成资料的端到端口径锁定。

这些断言把业务叙述里的关键数字固定下来：
- 三张表（区域/产线/责任方）分母为何不同、如何用同一口径解释；
- 3 月已签发 vs 处理中两套数字；
- 迟到更正的重发判定与复盘单再开。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from monthend.loader import load_dataset
from monthend.reports import (
    MonthEnd,
    build_issued_report,
    build_working_package,
    difference_explanations,
)

DATA = Path(__file__).resolve().parents[1] / "data"


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.month_end = MonthEnd(load_dataset(DATA))

    def _cells(self, report, dimension, section="pivots"):
        return {cell["key"]: cell for cell in report[section][dimension]}

    def test_march_issued_report_locks_old_basis(self):
        report = build_issued_report(self.month_end, "2026-03")
        self.assertEqual(report["revision_set"], {"R01": 1, "R02": 1})
        region = self._cells(report, "region")["north"]
        self.assertEqual((region["qty"], region["first_pass"], region["rework"]), (80, 73, 7))
        self.assertEqual(region["fpy_exact"], "73/80")
        customer = self._cells(report, "customer")
        self.assertEqual(customer["CX"]["fpy_exact"], "9/10")
        # 对外维度受关账约定约束
        self.assertEqual(set(report["pivots"]), {"region", "customer"})

    def test_march_working_package_locks_latest_basis(self):
        package = build_working_package(self.month_end, "2026-03")
        self.assertEqual(package["revision_set"], {"R01": 2, "R02": 1})
        region = self._cells(package, "region", "production_pivots")["north"]
        self.assertEqual((region["qty"], region["first_pass"]), (80, 69))
        self.assertEqual(region["fpy_exact"], "69/80")

    def test_responsibility_share_differs_from_delivery_share(self):
        package = build_working_package(self.month_end, "2026-03")
        owners = {cell["key"]: cell for cell in package["responsibility_pivot"]}
        # 交付全部在 N 厂（80 件），返修责任却由 N 4.5 / S 6.5 共担
        self.assertEqual(owners["N"]["signed"], "4.5")
        self.assertEqual(owners["S"]["signed"], "6.5")
        for cell in owners.values():
            self.assertIsNone(cell["fpy"])  # 责任层无共同分母，不算率

    def test_melt_without_conversion_is_flagged_once(self):
        package = build_working_package(self.month_end, "2026-03")
        flagged = package["incomparable_volume"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["transfer"], "T05")
        self.assertEqual(flagged[0]["qty"], 12)
        # 它没有进入任何生产分母
        region = self._cells(package, "region", "production_pivots")["north"]
        self.assertEqual(region["qty"], 80)

    def test_melt_with_conversion_enters_april_denominator(self):
        package = build_working_package(self.month_end, "2026-04")
        self.assertEqual(package["incomparable_volume"], [])
        region = self._cells(package, "region", "production_pivots")["south"]
        self.assertEqual(region["qty"], 65)  # 30 + 35，T09 的 14kg=35 件有 C2 凭据

    def test_april_responsibility_gap_buckets(self):
        package = build_working_package(self.month_end, "2026-04")
        detail = {row["receipt"]: row for row in package["responsibility_detail"]}
        self.assertEqual(detail["R03"]["signed"], "4.9")
        self.assertEqual(detail["R03"]["unclaimed"], "2.1")
        self.assertEqual(detail["R04"]["unallocatable"], 4)

    def test_late_correction_reissue_decision(self):
        report = build_issued_report(self.month_end, "2026-03")
        notice = report["late_corrections"][0]
        self.assertEqual(notice["receipt"], "R01")
        self.assertEqual(notice["first_pass_under_issued_basis"], 54)
        self.assertEqual(notice["first_pass_under_current_basis"], 50)
        self.assertTrue(notice["qualifies_for_reissue"])  # |−4| >= 门槛 3 且在窗口内

    def test_review_v1_reopens_v2_resolved_no_duplicate(self):
        package = build_working_package(self.month_end, "2026-03")
        states = {row["id"]: row for row in package["review"]["voucher_states"]}
        self.assertEqual(states["V1"]["state"], "REOPEN")
        self.assertEqual(states["V2"]["state"], "RESOLVED_BY_EVIDENCE")
        # 只给新责任方 S 补发一张，N 的残余已被 V1 覆盖，不重复发
        items = package["review"]["new_review_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["owner"], items[0]["qty"]), ("S", "6.5"))

    def test_traceable_failures_survive_merge(self):
        package = build_working_package(self.month_end, "2026-03")
        checks = {row["check"]: row for row in package["traceable_failures"]}
        # K03 挂在 T02（A3 合并批的 A2 来源），不能因 R01 验收而消失
        self.assertIn("K01", checks)
        self.assertIn("K02", checks)
        self.assertIn("K03", checks)
        self.assertEqual(checks["K03"]["original_lot"], "A2")

    def test_difference_explanations_trace_back_to_cell_and_contributions(self):
        explanations = difference_explanations(self.month_end, "2026-03")
        kinds = {item["type"] for item in explanations}
        self.assertIn("late_correction", kinds)
        self.assertIn("incomparable_volume", kinds)
        correction = next(
            item for item in explanations if item["type"] == "late_correction"
        )
        trace = correction["trace_back"]
        self.assertEqual(trace["cell"]["customer"], "CX")
        self.assertEqual(
            {c["transfer"] for c in trace["contributions"]}, {"T03", "T04"}
        )

    def test_all_dimension_totals_balance_both_periods(self):
        for period in ("2026-03", "2026-04"):
            package = build_working_package(self.month_end, period)
            self.assertTrue(package["reconciliation"]["balanced"])


if __name__ == "__main__":
    unittest.main()
