"""资料装载：复用交换约定校验，再做业务层解析。

只做可机械核对的结构校验（表头、身份键、引用、数值自洽），
不在这里产生任何业务结论；空字段保持 ``None``，绝不解释为零。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from check_sources import load_sources  # noqa: E402


def _int(row: dict, column: str) -> int | None:
    value = row[column]
    return int(value) if value != "" else None


def _fraction(value: str):
    return Fraction(value) if value != "" else None


def _day(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass(frozen=True)
class Transfer:
    id: str
    period: str
    sent: str
    received: str
    origin: str
    destination: str
    stage: str
    qty: int
    unit: str
    equiv: int | None
    conversion: str

    @property
    def comparable(self) -> bool:
        """有换算凭据才能进入标准件（piece）分母。"""
        return self.equiv is not None


@dataclass(frozen=True)
class Acceptance:
    receipt: str
    revision: int
    batch: str
    customer: str
    occurred: date
    seen: date
    qty: int
    first_pass: int
    rework: int


@dataclass(frozen=True)
class Check:
    id: str
    transfer: str
    original_lot: str
    seen: date
    first_failed: int
    qty_inspected: int


@dataclass(frozen=True)
class AttributionRow:
    receipt: str
    revision: int
    seen: date
    owner: str
    share: object
    status: str


@dataclass(frozen=True)
class CloseRule:
    period: str
    cutoff: date
    unit: str
    dimensions: tuple[str, ...]
    late_min_delta: int
    reissue_until: date


@dataclass(frozen=True)
class Site:
    site: str
    region: str


@dataclass(frozen=True)
class ReviewVoucher:
    id: str
    receipt: str
    owner: str
    opened: date
    closed: date | None
    covered_qty: int


@dataclass
class Dataset:
    transfers: list[Transfer]
    acceptances: list[Acceptance]
    checks: list[Check]
    attribution: list[AttributionRow]
    close: dict[str, CloseRule]
    sites: dict[str, Site]
    review: list[ReviewVoucher]
    directory: Path

    def latest_revisions(self) -> dict[str, int]:
        latest: dict[str, int] = {}
        for row in self.acceptances:
            latest[row.receipt] = max(row.revision, latest.get(row.receipt, 0))
        return latest

    def period_of_receipt(self, receipt: str) -> str:
        batch = next(a.batch for a in self.acceptances if a.receipt == receipt)
        return self.batch_period[batch]

    @property
    def batch_period(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for transfer in self.transfers:
            mapping[transfer.received] = transfer.period
        return mapping


def load_dataset(directory: str | Path) -> Dataset:
    directory = Path(directory)
    raw = load_sources(directory)

    transfers = [
        Transfer(
            id=r["id"],
            period=r["period"],
            sent=r["sent"],
            received=r["received"],
            origin=r["origin"],
            destination=r["destination"],
            stage=r["stage"],
            qty=_int(r, "qty"),
            unit=r["unit"],
            equiv=_int(r, "equiv"),
            conversion=r["conversion"],
        )
        for r in raw["transfers"]
    ]
    acceptances = [
        Acceptance(
            receipt=r["receipt"],
            revision=int(r["revision"]),
            batch=r["batch"],
            customer=r["customer"],
            occurred=_day(r["occurred"]),
            seen=_day(r["seen"]),
            qty=_int(r, "qty"),
            first_pass=_int(r, "first_pass"),
            rework=_int(r, "rework"),
        )
        for r in raw["acceptances"]
    ]
    checks = [
        Check(
            id=r["id"],
            transfer=r["transfer"],
            original_lot=r["original_lot"],
            seen=_day(r["seen"]),
            first_failed=_int(r, "first_failed"),
            qty_inspected=_int(r, "qty_inspected"),
        )
        for r in raw["checks"]
    ]
    attribution = [
        AttributionRow(
            receipt=r["receipt"],
            revision=int(r["revision"]),
            seen=_day(r["seen"]),
            owner=r["owner"],
            share=_fraction(r["share"]),
            status=r["status"],
        )
        for r in raw["attribution"]
    ]
    close = {
        r["period"]: CloseRule(
            period=r["period"],
            cutoff=_day(r["cutoff"]),
            unit=r["unit"],
            dimensions=tuple(part.strip() for part in r["dimensions"].split(",")),
            late_min_delta=int(r["late_min_delta"]),
            reissue_until=_day(r["reissue_until"]),
        )
        for r in raw["close"]
    }
    sites = {r["site"]: Site(site=r["site"], region=r["region"]) for r in raw["sites"]}
    review = [
        ReviewVoucher(
            id=r["id"],
            receipt=r["receipt"],
            owner=r["owner"],
            opened=_day(r["opened"]),
            closed=_day(r["closed"]) if r["closed"] else None,
            covered_qty=_int(r, "covered_qty"),
        )
        for r in raw["review"]
    ]

    _validate_business(transfers, acceptances, checks, attribution, close, sites, review)
    return Dataset(
        transfers=transfers,
        acceptances=acceptances,
        checks=checks,
        attribution=attribution,
        close=close,
        sites=sites,
        review=review,
        directory=directory,
    )


def _validate_business(transfers, acceptances, checks, attribution, close, sites, review):
    # 验收量 = 首过 + 返修；各字段都不得为空
    for row in acceptances:
        if None in (row.qty, row.first_pass, row.rework):
            raise ValueError(f"验收 {row.receipt} rev{row.revision} 存在空数量")
        if row.qty != row.first_pass + row.rework:
            raise ValueError(f"验收 {row.receipt} rev{row.revision} 首过+返修不等于验收量")
        if row.seen < row.occurred:
            raise ValueError(f"验收 {row.receipt} rev{row.revision} 确认早于发生")
    # 有 equiv 必须带换算凭据；量与标准件量为正
    for transfer in transfers:
        if transfer.qty is None or transfer.qty <= 0:
            raise ValueError(f"交接 {transfer.id} 数量缺失或非正")
        if transfer.equiv is not None:
            if not transfer.conversion:
                raise ValueError(f"交接 {transfer.id} 有标准件量却缺换算凭据")
            if transfer.equiv <= 0:
                raise ValueError(f"交接 {transfer.id} 标准件量非正")
    # 检验事实量自洽
    for check in checks:
        if check.first_failed > check.qty_inspected:
            raise ValueError(f"检验 {check.id} 失败量超过检验量")
    # 责任份额：同一确认（receipt+revision）合计不超过 1，状态合法
    grouped: dict[tuple[str, int], list[AttributionRow]] = {}
    for row in attribution:
        grouped.setdefault((row.receipt, row.revision), []).append(row)
        if row.status not in {"signed", "provisional"}:
            raise ValueError(f"责任 {row.receipt} rev{row.revision} 状态非法: {row.status}")
        if not 0 < row.share <= 1:
            raise ValueError(f"责任 {row.receipt} rev{row.revision} 份额超出 (0,1]")
    for key, rows in grouped.items():
        total = sum((row.share for row in rows), Fraction(0))
        if total > 1:
            raise ValueError(f"责任 {key[0]} rev{key[1]} 份额合计超过 1")
    # 关账单位与维度
    for rule in close.values():
        if rule.reissue_until < rule.cutoff:
            raise ValueError(f"期间 {rule.period} 重发截止早于关账时点")
        for dimension in rule.dimensions:
            if dimension not in {"region", "customer"}:
                raise ValueError(f"期间 {rule.period} 对外发布维度非法: {dimension}")
    # 站点归属完整
    for transfer in transfers:
        for site in (transfer.origin, transfer.destination):
            if site not in sites:
                raise ValueError(f"交接 {transfer.id} 的站点 {site} 缺行政归属")
    # 复盘覆盖量非负
    for voucher in review:
        if voucher.covered_qty < 0:
            raise ValueError(f"复盘 {voucher.id} 覆盖量为负")
