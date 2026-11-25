"""复盘单的现有回执与新凭证之间的生命周期管理。

四种处置，原始记录一律保留不清空：
- ``KEEP_CLOSED``：已结案且责任量与归属仍被覆盖；
- ``REOPEN``：已结案，但返修数量影响扩大，或责任已改交另一厂；
- ``RESOLVED_BY_EVIDENCE``：未结案差异正好被新凭证覆盖，确认不再待办，
  不重复发复盘单；
- ``STILL_OPEN``：仍有残余量待处理。

新复盘单按"责任方残余量"和"未认领/无法分摊缺口"生成；
已发生的覆盖量始终抵在原责任方头上，不因单据再开而抹掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .loader import Dataset, ReviewVoucher
from .numbers import exact_num
from .responsibility import ResponsibilityLedger
from .trace import Trace


@dataclass(frozen=True)
class VoucherStatus:
    id: str
    receipt: str
    owner: str
    state: str
    covered_qty: int
    reason: str
    evidence: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "receipt": self.receipt,
            "owner": self.owner,
            "state": self.state,
            "covered_qty": self.covered_qty,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class NewReviewItem:
    receipt: str
    revision: int
    owner: str
    qty: Fraction
    bucket: str
    reason: str
    cell_ref: dict
    evidence: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "receipt": self.receipt,
            "revision": self.revision,
            "owner": self.owner,
            "qty": exact_num(self.qty),
            "bucket": self.bucket,
            "reason": self.reason,
            "cell_ref": self.cell_ref,
            "evidence": list(self.evidence),
        }


class ReviewBoard:
    def __init__(
        self,
        dataset: Dataset,
        traces: dict[str, Trace],
        ledger: ResponsibilityLedger,
    ):
        self._dataset = dataset
        self._traces = traces
        self._ledger = ledger

    def evaluate_period(
        self, period: str, working_revision_set: dict[str, int]
    ) -> dict:
        voucher_states: list[VoucherStatus] = []
        new_items: list[NewReviewItem] = []

        for receipt, revision in sorted(working_revision_set.items()):
            if self._dataset.period_of_receipt(receipt) != period:
                continue
            view = self._ledger.for_revision(receipt, revision)
            vouchers = [
                v for v in self._dataset.review if v.receipt == receipt
            ]
            covered_by_owner: dict[str, Fraction] = {}
            for voucher in vouchers:
                covered_by_owner[voucher.owner] = (
                    covered_by_owner.get(voucher.owner, Fraction(0))
                    + Fraction(voucher.covered_qty)
                )
            allocation_by_owner = {
                item.owner: item.qty for item in view.allocations
            }
            total_covered = sum(
                (Fraction(v.covered_qty) for v in vouchers), Fraction(0)
            )

            for voucher in vouchers:
                voucher_states.append(
                    self._evaluate_voucher(
                        voucher, view.rework_qty, total_covered, allocation_by_owner
                    )
                )

            cell_ref = self._cell_ref(receipt)
            # 责任方残余量：现有覆盖抵在原责任方，仍不足才发新单
            for item in view.allocations:
                residual = item.qty - covered_by_owner.get(item.owner, Fraction(0))
                if residual > 0:
                    new_items.append(
                        NewReviewItem(
                            receipt=receipt,
                            revision=revision,
                            owner=item.owner,
                            qty=residual,
                            bucket=item.status,
                            reason=(
                                "正式确认责任未被既有复盘单覆盖"
                                if item.status == "signed"
                                else "共担比例（临时确认）未被既有复盘单覆盖"
                            ),
                            cell_ref=cell_ref,
                            evidence=(
                                f"attribution:{receipt}#r{revision}:{item.owner}",
                            ),
                        )
                    )
            if view.unclaimed > 0:
                new_items.append(
                    NewReviewItem(
                        receipt=receipt,
                        revision=revision,
                        owner="UNASSIGNED",
                        qty=view.unclaimed,
                        bucket="unclaimed",
                        reason="份额未覆盖的返修量，尚未有责任方认领",
                        cell_ref=cell_ref,
                        evidence=(f"attribution:{receipt}#r{revision}",),
                    )
                )
            if view.unallocatable > 0:
                new_items.append(
                    NewReviewItem(
                        receipt=receipt,
                        revision=revision,
                        owner="UNASSIGNED",
                        qty=view.unallocatable,
                        bucket="unallocatable",
                        reason="无责任确认或缺换算凭据，返修量无法分摊",
                        cell_ref=cell_ref,
                        evidence=(f"acceptance:{receipt}#r{revision}",),
                    )
                )

        return {
            "period": period,
            "voucher_states": [state.as_dict() for state in voucher_states],
            "new_review_items": [item.as_dict() for item in new_items],
        }

    def _evaluate_voucher(
        self,
        voucher: ReviewVoucher,
        current_rework: Fraction,
        total_covered: Fraction,
        allocation_by_owner: dict[str, Fraction],
    ) -> VoucherStatus:
        evidence = (f"review:{voucher.id}",)
        owner_now = allocation_by_owner.get(voucher.owner)
        if voucher.closed is not None:
            if owner_now is None and allocation_by_owner:
                return VoucherStatus(
                    voucher.id,
                    voucher.receipt,
                    voucher.owner,
                    "REOPEN",
                    voucher.covered_qty,
                    "责任签字后复盘改交另一厂，原责任方已无该回执责任，需再开并改派",
                    evidence,
                )
            if current_rework > total_covered:
                return VoucherStatus(
                    voucher.id,
                    voucher.receipt,
                    voucher.owner,
                    "REOPEN",
                    voucher.covered_qty,
                    f"复盘已结案但返修数量影响扩大（现 {exact_num(current_rework)}，"
                    f"已覆盖 {exact_num(total_covered)}），需再开；原记录保留",
                    evidence,
                )
            return VoucherStatus(
                voucher.id,
                voucher.receipt,
                voucher.owner,
                "KEEP_CLOSED",
                voucher.covered_qty,
                "结案范围与当前责任量一致，继续保留",
                evidence,
            )
        # 未结案
        if owner_now is not None and owner_now <= Fraction(voucher.covered_qty):
            return VoucherStatus(
                voucher.id,
                voucher.receipt,
                voucher.owner,
                "RESOLVED_BY_EVIDENCE",
                voucher.covered_qty,
                "未处理差异已被新凭证完整覆盖，确认不再待办，不重复发单",
                evidence,
            )
        return VoucherStatus(
            voucher.id,
            voucher.receipt,
            voucher.owner,
            "STILL_OPEN",
            voucher.covered_qty,
            "仍有残余返修量待处理",
            evidence,
        )

    def _cell_ref(self, receipt: str) -> dict:
        acceptance = next(
            row
            for row in self._dataset.acceptances
            if row.receipt == receipt
            and row.revision
            == max(
                r.revision
                for r in self._dataset.acceptances
                if r.receipt == receipt
            )
        )
        trace = self._traces[acceptance.batch]
        stages = sorted({edge.stage for edge in trace.inbound})
        return {
            "period": self._dataset.period_of_receipt(receipt),
            "region": trace.delivery_region,
            "site": trace.delivery_site,
            "stage": "|".join(stages),
            "customer": acceptance.customer,
            "batch": acceptance.batch,
        }
