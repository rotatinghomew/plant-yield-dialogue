"""领料路由与拆并批交接的路径还原。

一条交接边把 ``sent`` 批的量交到 ``received`` 批：
- 拆分：同一来源批有多条出边，每条边带自己的量与（可能的）标准件当量；
- 合并：同一接收批有多条入边，原批各自的检验事实分别保留，不互相冲销；
- 无换算凭据的边标为不可比，暴露量但不进标准件分母，绝不按零处理。

检验事实（checks）挂在其引用的交接边上，沿路径回到原始批；
已发生的首检失败是独立事实，下游复验通过不会抹掉它。
"""

from __future__ import annotations

from dataclasses import dataclass

from .loader import Acceptance, Check, Dataset, Transfer


@dataclass(frozen=True)
class Contribution:
    """一次交接对某个接收批的一条来源贡献，可继续回溯到原始批。"""

    transfer_id: str
    source_batch: str
    qty: int
    unit: str
    equiv: int | None
    conversion: str
    stage: str
    origin: str
    destination: str
    checks: tuple[Check, ...] = ()
    path: tuple[str, ...] = ()

    @property
    def comparable(self) -> bool:
        return self.equiv is not None


@dataclass
class Trace:
    """一个批号的完整来源结构与可追溯检验事实。"""

    batch: str
    inbound: tuple[Contribution, ...]
    delivery_site: str | None
    delivery_region: str | None
    comparable_equiv: int
    incomparable: tuple[Contribution, ...]
    traceable_failures: tuple[tuple[Check, tuple[str, ...]], ...]
    site_mismatches: tuple[str, ...]

    @property
    def has_comparable_volume(self) -> bool:
        return self.comparable_equiv > 0


def build_traces(dataset: Dataset) -> dict[str, Trace]:
    inbound: dict[str, list[Transfer]] = {}
    outbound: dict[str, list[Transfer]] = {}
    for transfer in dataset.transfers:
        inbound.setdefault(transfer.received, []).append(transfer)
        outbound.setdefault(transfer.sent, []).append(transfer)
    checks_by_transfer: dict[str, list[Check]] = {}
    for check in dataset.checks:
        checks_by_transfer.setdefault(check.transfer, []).append(check)

    traces: dict[str, Trace] = {}

    def walk(batch: str, visiting: frozenset[str]) -> tuple[Contribution, ...]:
        if batch in traces:
            return traces[batch].inbound
        if batch in visiting:
            raise ValueError(f"交接路由在批 {batch} 出现环，无法回溯")
        edges = inbound.get(batch, [])
        contributions: list[Contribution] = []
        for edge in edges:
            edge_checks = tuple(checks_by_transfer.get(edge.id, ()))
            if edge.sent in inbound:
                ancestors = walk(edge.sent, visiting | {batch})
                base_path = tuple(
                    step for ancestor in ancestors for step in ancestor.path
                )
                # 路径中段（非直接入边）的检验事实同样随来源贡献保留
                ancestor_checks = tuple(
                    check
                    for ancestor in ancestors
                    for check in ancestor.checks
                )
            else:
                base_path = ()
                ancestor_checks = ()
            contributions.append(
                Contribution(
                    transfer_id=edge.id,
                    source_batch=edge.sent,
                    qty=edge.qty,
                    unit=edge.unit,
                    equiv=edge.equiv,
                    conversion=edge.conversion,
                    stage=edge.stage,
                    origin=edge.origin,
                    destination=edge.destination,
                    checks=ancestor_checks + edge_checks,
                    path=(*base_path, edge.id),
                )
            )
        return tuple(contributions)

    all_batches = {t.received for t in dataset.transfers} | {
        t.sent for t in dataset.transfers
    }
    for batch in sorted(all_batches):
        contributions = walk(batch, frozenset())
        edges = inbound.get(batch, [])
        destinations = {edge.destination for edge in edges}
        site_mismatches: list[str] = []
        if len(destinations) > 1:
            site_mismatches.append(
                f"批 {batch} 的入边目的地不一致: {sorted(destinations)}"
            )
        delivery_site = next(iter(destinations)) if len(destinations) == 1 else None
        delivery_region = (
            dataset.sites[delivery_site].region if delivery_site in dataset.sites else None
        )
        comparable = sum(c.equiv or 0 for c in contributions)
        incomparable = tuple(c for c in contributions if not c.comparable)
        failures = tuple(
            (check, c.path)
            for c in contributions
            for check in c.checks
            if check.first_failed > 0
        )
        traces[batch] = Trace(
            batch=batch,
            inbound=contributions,
            delivery_site=delivery_site,
            delivery_region=delivery_region,
            comparable_equiv=comparable,
            incomparable=incomparable,
            traceable_failures=failures,
            site_mismatches=tuple(site_mismatches),
        )
    return traces


def trace_for_acceptance(
    acceptance: Acceptance, traces: dict[str, Trace]
) -> Trace:
    trace = traces.get(acceptance.batch)
    if trace is None:
        raise ValueError(f"验收 {acceptance.receipt} 的批 {acceptance.batch} 没有交接路由")
    return trace


def unmatched_comparable_volume(
    acceptance: Acceptance, trace: Trace
) -> int:
    """可比来源当量与验收标准件量的差额；不为零说明路由与验收对不上。"""
    return trace.comparable_equiv - acceptance.qty
