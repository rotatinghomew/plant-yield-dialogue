"""返修责任台账。

同一范围（一张验收回执的某个修订版）的返修标准件量，按证据状态分四类，
四类相加等于该回执的返修量，任何一类都不会被静默吞掉：

- ``signed``       正式确认：status=signed 的份额对应的量；
- ``provisional``  共担比例：status=provisional 的份额对应的量；
- ``unclaimed``    尚未认领：有责任确认但份额未覆盖到的返修量；
- ``unallocatable`` 无法分摊：根本没有责任确认，或来源缺换算凭据、
                    无法把返修量追到可比来源的部分。

责任份额是对"有关返修标准件量"的比例，不是对交付量的比例；
因此责任方层级没有共同交付分母，台账只给量与缺口，不虚算首过率。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .loader import Acceptance, AttributionRow, Dataset
from .numbers import exact_num
from .trace import Trace


@dataclass(frozen=True)
class OwnerAllocation:
    owner: str
    status: str
    share: Fraction
    qty: Fraction
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ResponsibilityView:
    receipt: str
    revision: int
    rework_qty: Fraction
    signed: Fraction
    provisional: Fraction
    unclaimed: Fraction
    unallocatable: Fraction
    allocations: tuple[OwnerAllocation, ...]
    reasons: tuple[str, ...]

    @property
    def covered(self) -> Fraction:
        return self.signed + self.provisional

    @property
    def gap(self) -> Fraction:
        return self.unclaimed + self.unallocatable

    def as_dict(self) -> dict:
        return {
            "receipt": self.receipt,
            "revision": self.revision,
            "rework_qty": exact_num(self.rework_qty),
            "signed": exact_num(self.signed),
            "provisional": exact_num(self.provisional),
            "unclaimed": exact_num(self.unclaimed),
            "unallocatable": exact_num(self.unallocatable),
            "gap": exact_num(self.gap),
            "allocations": [
                {
                    "owner": item.owner,
                    "status": item.status,
                    "share": exact_num(item.share),
                    "qty": exact_num(item.qty),
                    "evidence": list(item.evidence),
                }
                for item in self.allocations
            ],
            "reasons": list(self.reasons),
        }


class ResponsibilityLedger:
    def __init__(self, dataset: Dataset, traces: dict[str, Trace]):
        self._dataset = dataset
        self._traces = traces
        self._rows: dict[tuple[str, int], list[AttributionRow]] = {}
        known_revisions = {(a.receipt, a.revision) for a in dataset.acceptances}
        for row in dataset.attribution:
            key = (row.receipt, row.revision)
            if key not in known_revisions:
                raise ValueError(
                    f"责任确认 {row.receipt} rev{row.revision} 没有对应验收修订版"
                )
            self._rows.setdefault(key, []).append(row)

    def for_revision(self, receipt: str, revision: int) -> ResponsibilityView:
        acceptance = self._acceptance(receipt, revision)
        rework = Fraction(acceptance.rework)
        reasons: list[str] = []

        # 缺换算凭据的来源：对应份额的返修量无法追到可比来源，只能进无法分摊。
        trace = self._traces[acceptance.batch]
        if trace.comparable_equiv < acceptance.qty:
            untraceable = rework * Fraction(
                acceptance.qty - trace.comparable_equiv, acceptance.qty
            )
            reasons.append(
                f"{exact_num(untraceable)} 件返修来源缺换算凭据，列为无法分摊"
            )
        else:
            untraceable = Fraction(0)

        rows = self._rows.get((receipt, revision), [])
        allocations: list[OwnerAllocation] = []
        signed = provisional = Fraction(0)
        if rows:
            apportionable = rework - untraceable
            for row in rows:
                qty = apportionable * row.share
                allocations.append(
                    OwnerAllocation(
                        owner=row.owner,
                        status=row.status,
                        share=row.share,
                        qty=qty,
                        evidence=(f"attribution:{receipt}#r{revision}:{row.owner}",),
                    )
                )
                if row.status == "signed":
                    signed += qty
                else:
                    provisional += qty
            covered_share = sum((row.share for row in rows), Fraction(0))
            unclaimed = apportionable * (Fraction(1) - covered_share)
            if unclaimed > 0:
                reasons.append(
                    f"责任确认份额仅覆盖 {exact_num(covered_share)}，"
                    f"{exact_num(unclaimed)} 件返修尚未认领"
                )
        else:
            unclaimed = Fraction(0)
            if rework - untraceable > 0:
                reasons.append("无任何责任确认，可比来源的返修量无法分摊")

        unallocatable = untraceable + (
            (rework - untraceable) if not rows else Fraction(0)
        )
        view = ResponsibilityView(
            receipt=receipt,
            revision=revision,
            rework_qty=rework,
            signed=signed,
            provisional=provisional,
            unclaimed=unclaimed,
            unallocatable=unallocatable,
            allocations=tuple(allocations),
            reasons=tuple(reasons),
        )
        total = view.signed + view.provisional + view.unclaimed + view.unallocatable
        if total != rework:
            raise AssertionError(f"{receipt} rev{revision} 四类返修量合计不平")
        return view

    def latest_view(self, receipt: str) -> ResponsibilityView:
        revision = max(
            a.revision for a in self._dataset.acceptances if a.receipt == receipt
        )
        return self.for_revision(receipt, revision)

    def view_at(self, receipt: str, cutoff) -> ResponsibilityView | None:
        """关账时点视角：取 seen <= cutoff 的最新修订版；都晚于 cutoff 则不可见。"""
        candidates = [
            a
            for a in self._dataset.acceptances
            if a.receipt == receipt and a.seen <= cutoff
        ]
        if not candidates:
            return None
        revision = max(a.revision for a in candidates)
        return self.for_revision(receipt, revision)

    def _acceptance(self, receipt: str, revision: int) -> Acceptance:
        for row in self._dataset.acceptances:
            if row.receipt == receipt and row.revision == revision:
                return row
        raise KeyError(f"找不到验收 {receipt} rev{revision}")
