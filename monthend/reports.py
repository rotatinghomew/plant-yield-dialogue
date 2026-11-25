"""月结两份输出的组装。

对内（working package）：生产组按现有佐证更新责任与返修明细、分派复盘，
包含全部视角、四类责任口径、复盘单处置与未分配量。

对外（issued report）：引用特定关账时间与口径，只发布该期约定允许的维度
（close.dimensions），不含责任方单件；迟到更正给出影响期次、旧口径结果
与重发条件判定。
"""

from __future__ import annotations

from .loader import Dataset
from .periods import PeriodBook
from .pivots import PivotEngine
from .responsibility import ResponsibilityLedger
from .review import ReviewBoard
from .trace import build_traces


class MonthEnd:
    """一次月结的完整口径对象，可重复用于后续关账周期。"""

    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.traces = build_traces(dataset)
        self.ledger = ResponsibilityLedger(dataset, self.traces)
        self.engine = PivotEngine(dataset, self.traces, self.ledger)
        self.book = PeriodBook(dataset)
        self.board = ReviewBoard(dataset, self.traces, self.ledger)

    def periods(self) -> list[str]:
        return sorted(self.dataset.close)


def build_working_package(month_end: MonthEnd, period: str) -> dict:
    dataset = month_end.dataset
    rule = dataset.close[period]
    revisions = month_end.book.working_revision_set(period)
    responsibility_views = [
        month_end.ledger.for_revision(receipt, revision).as_dict()
        for receipt, revision in sorted(revisions.items())
    ]
    return {
        "report_kind": "working_package",
        "period": period,
        "basis": {
            "mode": "处理中口径：无视关账时点，取每张回执最新修订版",
            "cutoff": rule.cutoff.isoformat(),
            "note": "仅用于生产组复盘分派，不作为对外已签数字",
        },
        "revision_set": revisions,
        "production_pivots": {
            dimension: [
                cell.as_dict()
                for cell in month_end.engine.production_pivot(
                    period, dimension, revisions
                )
            ]
            for dimension in PivotEngine.PRODUCTION_DIMENSIONS
        },
        "responsibility_pivot": [
            cell.as_dict()
            for cell in month_end.engine.responsibility_pivot(period, revisions)
        ],
        "responsibility_detail": responsibility_views,
        "traceable_failures": traceable_failures(month_end, revisions),
        "review": month_end.board.evaluate_period(period, revisions),
        "reconciliation": month_end.engine.reconcile(period, revisions),
        "routing_gaps": month_end.engine.routing_gaps(period, revisions),
        "duplicate_acceptances": month_end.engine.duplicate_acceptances(
            period, revisions
        ),
        "incomparable_volume": month_end.engine.incomparable_volume(period),
        "late_corrections": [
            notice.as_dict() for notice in month_end.book.late_corrections(period)
        ],
        "attribution_only_changes": month_end.book.attribution_only_changes(period),
    }


def traceable_failures(month_end: MonthEnd, revision_set: dict[str, int]) -> list[dict]:
    """合并前各原批的首检失败：独立事实，不与返修数相加、不因复验通过而消失。"""
    findings: list[dict] = []
    for receipt, revision in sorted(revision_set.items()):
        acceptance = next(
            row
            for row in month_end.dataset.acceptances
            if row.receipt == receipt and row.revision == revision
        )
        trace = month_end.traces[acceptance.batch]
        for check, path in trace.traceable_failures:
            findings.append(
                {
                    "receipt": receipt,
                    "revision": revision,
                    "check": check.id,
                    "transfer": check.transfer,
                    "original_lot": check.original_lot,
                    "first_failed": check.first_failed,
                    "qty_inspected": check.qty_inspected,
                    "handover_path": list(path),
                    "note": "合并前可追溯检验事实，仅用于追溯与重复验收识别，不重复扣量",
                }
            )
    return findings


def build_issued_report(month_end: MonthEnd, period: str) -> dict:
    dataset = month_end.dataset
    rule = dataset.close[period]
    revisions = month_end.book.issued_revision_set(period)
    return {
        "report_kind": "issued_monthly_report",
        "period": period,
        "basis": {
            "mode": "已签发口径：仅采用 seen 不晚于关账时点的最新修订版",
            "cutoff": rule.cutoff.isoformat(),
            "unit": rule.unit,
            "allowed_dimensions": list(rule.dimensions),
            "reissue_until": rule.reissue_until.isoformat(),
        },
        "revision_set": revisions,
        "pivots": {
            dimension: [
                cell.as_dict()
                for cell in month_end.engine.production_pivot(
                    period, dimension, revisions
                )
            ]
            for dimension in rule.dimensions
        },
        "reconciliation": month_end.engine.reconcile(period, revisions),
        "incomparable_volume": month_end.engine.incomparable_volume(period),
        "routing_gaps": month_end.engine.routing_gaps(period, revisions),
        "duplicate_acceptances": month_end.engine.duplicate_acceptances(
            period, revisions
        ),
        "late_corrections": [
            notice.as_dict() for notice in month_end.book.late_corrections(period)
        ],
        "publish_constraint": (
            "只允许在 "
            + "、".join(rule.dimensions)
            + " 维度的单元发布；不发布责任方与单件明细"
        ),
    }


def difference_explanations(month_end: MonthEnd, period: str) -> list[dict]:
    """差异说明：每条都能回到透视单元、贡献来源与未分配量。"""
    package = build_working_package(month_end, period)
    explanations: list[dict] = []
    for gap in package["routing_gaps"]:
        explanations.append(
            {
                "type": "routing_gap",
                "detail": gap,
                "trace_back": _trace_back(month_end, gap.get("receipt")),
            }
        )
    for item in package["incomparable_volume"]:
        explanations.append(
            {
                "type": "incomparable_volume",
                "detail": item,
                "trace_back": {
                    "transfer": item["transfer"],
                    "unit": item["unit"],
                    "note": "暴露不可比量，不计零、不跨分母相加",
                },
            }
        )
    for view in package["responsibility_detail"]:
        if view["gap"]:
            explanations.append(
                {
                    "type": "responsibility_gap",
                    "detail": {
                        "receipt": view["receipt"],
                        "revision": view["revision"],
                        "unclaimed": view["unclaimed"],
                        "unallocatable": view["unallocatable"],
                        "reasons": view["reasons"],
                    },
                    "trace_back": _trace_back(month_end, view["receipt"]),
                }
            )
    for notice in package["late_corrections"]:
        explanations.append(
            {
                "type": "late_correction",
                "detail": notice,
                "trace_back": _trace_back(month_end, notice["receipt"]),
            }
        )
    return explanations


def _trace_back(month_end: MonthEnd, receipt: str | None) -> dict | None:
    if receipt is None:
        return None
    latest = max(
        a.revision
        for a in month_end.dataset.acceptances
        if a.receipt == receipt
    )
    acceptance = next(
        a
        for a in month_end.dataset.acceptances
        if a.receipt == receipt and a.revision == latest
    )
    trace = month_end.traces[acceptance.batch]
    return {
        "receipt": receipt,
        "batch": acceptance.batch,
        "cell": {
            "region": trace.delivery_region,
            "site": trace.delivery_site,
            "customer": acceptance.customer,
        },
        "contributions": [
            {
                "transfer": c.transfer_id,
                "source_batch": c.source_batch,
                "qty": c.qty,
                "unit": c.unit,
                "equiv": c.equiv,
                "conversion": c.conversion or None,
                "comparable": c.comparable,
                "path": list(c.path),
                "checks": [check.id for check in c.checks],
            }
            for c in trace.inbound
        ],
        "unallocated_note": "未分配量见 responsibility_gap 与 responsibility_pivot",
    }
