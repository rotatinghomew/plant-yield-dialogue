"""透视引擎：同一份业务口径下的多视角重算。

生产视角（区域/客户/厂/工段）以验收回执签订口径里的交付单位为准，
拆分按量归去处、合并不抹原批失败；每个单元的首过率都从该单元适用的
分子分母重新计算，绝不平均各厂比例。

责任视角（责任方）只汇总返修标准件量的四类口径与缺口；
责任份额不对应共同交付分母，因此该视角不产生首过率。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from .loader import Acceptance, Dataset
from .numbers import exact_num
from .responsibility import ResponsibilityLedger
from .trace import Trace


@dataclass
class PivotCell:
    key: str
    qty: int = 0
    first_pass: int = 0
    rework: int = 0
    receipts: list[str] = field(default_factory=list)
    # 责任视角字段
    signed: Fraction = Fraction(0)
    provisional: Fraction = Fraction(0)
    unclaimed: Fraction = Fraction(0)
    unallocatable: Fraction = Fraction(0)
    has_delivery_denominator: bool = True

    @property
    def fpy(self) -> Fraction | None:
        if not self.has_delivery_denominator or self.qty == 0:
            return None
        return Fraction(self.first_pass, self.qty)

    @property
    def gap(self) -> Fraction:
        return self.unclaimed + self.unallocatable

    @property
    def rework_total(self) -> Fraction:
        return self.signed + self.provisional + self.unclaimed + self.unallocatable

    def as_dict(self) -> dict:
        data = {
            "key": self.key,
            "receipts": sorted(set(self.receipts)),
        }
        if self.has_delivery_denominator:
            data.update(
                qty=self.qty,
                first_pass=self.first_pass,
                rework=self.rework,
                fpy=None if self.fpy is None else f"{float(self.fpy):.6f}",
                fpy_exact=None if self.fpy is None else str(self.fpy),
            )
        else:
            data.update(
                rework_qty=exact_num(
                    self.signed
                    + self.provisional
                    + self.unclaimed
                    + self.unallocatable
                ),
                signed=exact_num(self.signed),
                provisional=exact_num(self.provisional),
                unclaimed=exact_num(self.unclaimed),
                unallocatable=exact_num(self.unallocatable),
                gap=exact_num(self.unclaimed + self.unallocatable),
                fpy=None,
                note="责任量层级无共同交付分母，不计算首过率",
            )
        return data


class PivotEngine:
    PRODUCTION_DIMENSIONS = ("region", "customer", "site", "stage")

    def __init__(
        self,
        dataset: Dataset,
        traces: dict[str, Trace],
        ledger: ResponsibilityLedger,
    ):
        self._dataset = dataset
        self._traces = traces
        self._ledger = ledger
        self._acceptances = {(a.receipt, a.revision): a for a in dataset.acceptances}

    def production_pivot(
        self, period: str, dimension: str, revision_set: dict[str, int]
    ) -> list[PivotCell]:
        if dimension not in self.PRODUCTION_DIMENSIONS:
            raise ValueError(f"未知生产视角: {dimension}")
        cells: dict[str, PivotCell] = {}
        for receipt, revision in sorted(revision_set.items()):
            acceptance = self._acceptances[(receipt, revision)]
            if self._dataset.period_of_receipt(receipt) != period:
                continue
            trace = self._traces[acceptance.batch]
            key = self._production_key(dimension, acceptance, trace)
            cell = cells.setdefault(key, PivotCell(key=key))
            cell.qty += acceptance.qty
            cell.first_pass += acceptance.first_pass
            cell.rework += acceptance.rework
            cell.receipts.append(receipt)
        return sorted(cells.values(), key=lambda item: item.key)

    def responsibility_pivot(
        self, period: str, revision_set: dict[str, int]
    ) -> list[PivotCell]:
        """按责任方汇总返修量；未认领/无法分摊单列，不派给任何厂。"""
        owner_cells: dict[str, PivotCell] = {}
        pending = PivotCell(key="UNASSIGNED", has_delivery_denominator=False)
        for receipt, revision in sorted(revision_set.items()):
            if self._dataset.period_of_receipt(receipt) != period:
                continue
            view = self._ledger.for_revision(receipt, revision)
            for allocation in view.allocations:
                cell = owner_cells.setdefault(
                    allocation.owner,
                    PivotCell(key=allocation.owner, has_delivery_denominator=False),
                )
                cell.receipts.append(receipt)
                if allocation.status == "signed":
                    cell.signed += allocation.qty
                else:
                    cell.provisional += allocation.qty
            pending.unclaimed += view.unclaimed
            pending.unallocatable += view.unallocatable
            if view.gap > 0:
                pending.receipts.append(receipt)
        result = sorted(owner_cells.values(), key=lambda item: item.key)
        if pending.gap > 0:
            result.append(pending)
        return result

    def reconcile(self, period: str, revision_set: dict[str, int]) -> dict:
        """跨视角核对：同一回执集合在各生产视角的分母必须一致。"""
        totals: dict[str, tuple[int, int, int]] = {}
        for dimension in self.PRODUCTION_DIMENSIONS:
            cells = self.production_pivot(period, dimension, revision_set)
            totals[dimension] = (
                sum(c.qty for c in cells),
                sum(c.first_pass for c in cells),
                sum(c.rework for c in cells),
            )
        unique = set(totals.values())
        return {
            "period": period,
            "dimension_totals": {
                dimension: {"qty": qty, "first_pass": fp, "rework": rw}
                for dimension, (qty, fp, rw) in totals.items()
            },
            "balanced": len(unique) <= 1,
        }

    def incomparable_volume(self, period: str) -> list[dict]:
        """暴露缺换算凭据的量：不进任何标准件分母，也不按零处理。"""
        findings: list[dict] = []
        for transfer in self._dataset.transfers:
            if transfer.period != period or transfer.equiv is not None:
                continue
            findings.append(
                {
                    "transfer": transfer.id,
                    "batch": transfer.received,
                    "stage": transfer.stage,
                    "qty": transfer.qty,
                    "unit": transfer.unit,
                    "reason": "无换算凭据（equiv 空缺），不可与标准件分母相加",
                }
            )
        return findings

    def routing_gaps(self, period: str, revision_set: dict[str, int]) -> list[dict]:
        """路由当量与验收量对不上、或目的地不一致时显式报差额。"""
        gaps: list[dict] = []
        for receipt, revision in sorted(revision_set.items()):
            if self._dataset.period_of_receipt(receipt) != period:
                continue
            acceptance = self._acceptances[(receipt, revision)]
            trace = self._traces[acceptance.batch]
            delta = trace.comparable_equiv - acceptance.qty
            if delta != 0:
                gaps.append(
                    {
                        "receipt": receipt,
                        "revision": revision,
                        "batch": acceptance.batch,
                        "routed_comparable_equiv": trace.comparable_equiv,
                        "accepted_qty": acceptance.qty,
                        "delta": delta,
                        "reason": "可比来源当量与验收标准件量不一致",
                    }
                )
            for mismatch in trace.site_mismatches:
                gaps.append({"receipt": receipt, "reason": mismatch})
        return gaps

    def duplicate_acceptances(self, period: str, revision_set: dict[str, int]) -> list[dict]:
        """同一交付批出现多张验收回执时提示重复验收风险。

        只做识别与暴露，不把任何量从分母里扣减；修订版（同 receipt 多 revision）
        是完整替代而非重复，不计入。
        """
        by_batch: dict[str, list[str]] = {}
        for receipt, revision in revision_set.items():
            if self._dataset.period_of_receipt(receipt) != period:
                continue
            acceptance = self._acceptances[(receipt, revision)]
            by_batch.setdefault(acceptance.batch, []).append(receipt)
        return [
            {
                "batch": batch,
                "receipts": sorted(receipts),
                "reason": "同一交付批存在多张验收回执，需核对是否重复验收",
            }
            for batch, receipts in sorted(by_batch.items())
            if len(receipts) > 1
        ]

    def _production_key(
        self, dimension: str, acceptance: Acceptance, trace: Trace
    ) -> str:
        if dimension == "customer":
            return acceptance.customer
        if dimension == "site":
            return trace.delivery_site or "UNKNOWN_SITE"
        if dimension == "region":
            return trace.delivery_region or "UNKNOWN_REGION"
        # stage：最终接收批的入边工段（合并批各入边工段应一致，否则并列暴露）
        stages = sorted({edge.stage for edge in trace.inbound})
        return "|".join(stages) if stages else "UNKNOWN_STAGE"
