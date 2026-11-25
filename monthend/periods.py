"""期间关账口径：已签发视角与处理中视角分开，迟到更正单独判定。

- 处理中（working）：无视 cutoff，取每张回执的最新修订版，供生产组复盘；
- 已签发（issued）：只取 ``seen <= cutoff`` 的最新修订版，对外月报只能
  引用关账时点和约定允许的发布单元；
- 迟到更正：关账后才被看到的验收修订版。若更正足以改变首过资格
  （标准件首过绝对变化达到 ``late_min_delta``）且在 ``reissue_until``
  内被看到，才符合该期重发条件；责任签字变化不改变对外已签产量，
  只进对内复盘。
"""

from __future__ import annotations

from dataclasses import dataclass

from .loader import Dataset


@dataclass(frozen=True)
class CorrectionNotice:
    receipt: str
    period: str
    issued_revision: int | None
    new_revision: int
    seen: str
    first_pass_issued: int | None
    first_pass_new: int
    delta_first_pass: int
    rework_delta: int
    affects_first_pass_qualification: bool
    qualifies_for_reissue: bool
    reissue_window_open: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "receipt": self.receipt,
            "period": self.period,
            "issued_revision": self.issued_revision,
            "new_revision": self.new_revision,
            "seen": self.seen,
            "first_pass_under_issued_basis": self.first_pass_issued,
            "first_pass_under_current_basis": self.first_pass_new,
            "delta_first_pass": self.delta_first_pass,
            "rework_delta": self.rework_delta,
            "affects_first_pass_qualification": self.affects_first_pass_qualification,
            "qualifies_for_reissue": self.qualifies_for_reissue,
            "reissue_window_open": self.reissue_window_open,
            "reason": self.reason,
        }


class PeriodBook:
    def __init__(self, dataset: Dataset):
        self._dataset = dataset
        self._by_receipt: dict[str, list] = {}
        for acceptance in dataset.acceptances:
            self._by_receipt.setdefault(acceptance.receipt, []).append(acceptance)
        for rows in self._by_receipt.values():
            rows.sort(key=lambda item: item.revision)

    def working_revision_set(self, period: str) -> dict[str, int]:
        return {
            receipt: max(row.revision for row in rows)
            for receipt, rows in self._by_receipt.items()
            if self._dataset.period_of_receipt(receipt) == period
        }

    def issued_revision_set(self, period: str) -> dict[str, int]:
        cutoff = self._rule(period).cutoff
        selected: dict[str, int] = {}
        for receipt, rows in self._by_receipt.items():
            if self._dataset.period_of_receipt(receipt) != period:
                continue
            visible = [row for row in rows if row.seen <= cutoff]
            if visible:
                selected[receipt] = max(row.revision for row in visible)
        return selected

    def late_corrections(self, period: str) -> list[CorrectionNotice]:
        rule = self._rule(period)
        issued = self.issued_revision_set(period)
        notices: list[CorrectionNotice] = []
        for receipt, rows in sorted(self._by_receipt.items()):
            if self._dataset.period_of_receipt(receipt) != period:
                continue
            late = [row for row in rows if row.seen > rule.cutoff]
            if not late:
                continue
            current = max(rows, key=lambda row: row.revision)
            issued_revision = issued.get(receipt)
            issued_row = (
                next(row for row in rows if row.revision == issued_revision)
                if issued_revision is not None
                else None
            )
            fp_issued = issued_row.first_pass if issued_row else None
            fp_new = current.first_pass
            delta = fp_new - (fp_issued if fp_issued is not None else 0)
            material = abs(delta) >= rule.late_min_delta
            in_window = current.seen <= rule.reissue_until
            qualifies = material and in_window
            if issued_row is None:
                reason = "关账时点该回执尚不可见，属于新增确认而非已签报表的更正"
                qualifies = False
            elif not material:
                reason = (
                    f"首过标准件绝对变化 {abs(delta)} 小于重发门槛 "
                    f"{rule.late_min_delta}，不触发重发"
                )
            elif not in_window:
                reason = (
                    f"更正确认日 {current.seen} 晚于重发窗口 {rule.reissue_until}"
                )
            else:
                reason = (
                    f"首过标准件绝对变化 {abs(delta)} 达到门槛 "
                    f"{rule.late_min_delta}，且在重发窗口内，符合该期重发条件"
                )
            notices.append(
                CorrectionNotice(
                    receipt=receipt,
                    period=period,
                    issued_revision=issued_revision,
                    new_revision=current.revision,
                    seen=current.seen.isoformat(),
                    first_pass_issued=fp_issued,
                    first_pass_new=fp_new,
                    delta_first_pass=delta,
                    rework_delta=current.rework
                    - (issued_row.rework if issued_row else 0),
                    affects_first_pass_qualification=material,
                    qualifies_for_reissue=qualifies,
                    reissue_window_open=in_window,
                    reason=reason,
                )
            )
        return notices

    def attribution_only_changes(self, period: str) -> list[dict]:
        """关账后的责任签字：影响复盘分派，不改对外已签产量。"""
        rule = self._rule(period)
        findings: list[dict] = []
        for row in self._dataset.attribution:
            if self._dataset.period_of_receipt(row.receipt) != period:
                continue
            if row.seen <= rule.cutoff:
                continue
            findings.append(
                {
                    "receipt": row.receipt,
                    "revision": row.revision,
                    "owner": row.owner,
                    "share": str(row.share),
                    "status": row.status,
                    "seen": row.seen.isoformat(),
                    "reason": "关账后责任确认，只进对内复盘，不改变对外已签产量",
                }
            )
        return findings

    def _rule(self, period: str):
        if period not in self._dataset.close:
            raise KeyError(f"期间 {period} 没有关账约定")
        return self._dataset.close[period]
