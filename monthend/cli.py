"""命令行：按关账周期生成对内复盘包与对外月报。

用法：
    python3 -m monthend.cli --period 2026-03
    python3 -m monthend.cli --data data --out out            # 全部期间
    python3 -m monthend.cli --period 2026-03 --explain       # 附差异说明
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .loader import load_dataset
from .reports import (
    MonthEnd,
    build_issued_report,
    build_working_package,
    difference_explanations,
)


def emit_markdown(report: dict) -> str:
    lines = [f"# {report['report_kind']} — {report['period']}", ""]
    basis = report["basis"]
    lines.append(f"- 口径：{basis['mode']}")
    lines.append(f"- 关账时点：{basis['cutoff']}")
    if "allowed_dimensions" in basis:
        lines.append(f"- 允许发布维度：{', '.join(basis['allowed_dimensions'])}")
    lines.append("")
    pivots = report.get("pivots") or report.get("production_pivots", {})
    for dimension, cells in pivots.items():
        lines.append(f"## 透视：{dimension}")
        lines.append("")
        if cells and "qty" in cells[0]:
            lines.append("| 单元 | 验收量 | 首过 | 返修 | 重算首过率 | 回执 |")
            lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
            for cell in cells:
                lines.append(
                    f"| {cell['key']} | {cell['qty']} | {cell['first_pass']} "
                    f"| {cell['rework']} | {cell.get('fpy_exact') or '—'} "
                    f"| {', '.join(cell['receipts'])} |"
                )
        else:
            lines.append("| 责任方 | 正式确认 | 共担 | 未认领 | 无法分摊 | 缺口 |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
            for cell in cells:
                lines.append(
                    f"| {cell['key']} | {cell.get('signed')} "
                    f"| {cell.get('provisional')} | {cell.get('unclaimed')} "
                    f"| {cell.get('unallocatable')} | {cell.get('gap')} |"
                )
        lines.append("")
    if report.get("late_corrections"):
        lines.append("## 迟到更正")
        lines.append("")
        for notice in report["late_corrections"]:
            lines.append(
                f"- {notice['receipt']} r{notice['new_revision']}（seen "
                f"{notice['seen']}）：旧口径首过 {notice['first_pass_under_issued_basis']} "
                f"-> 新口径 {notice['first_pass_under_current_basis']}，变化 "
                f"{notice['delta_first_pass']}；"
                f"符合重发条件：{'是' if notice['qualifies_for_reissue'] else '否'}。"
                f"{notice['reason']}"
            )
        lines.append("")
    if report.get("incomparable_volume"):
        lines.append("## 不可比量（缺换算凭据，不计零、不跨分母相加）")
        lines.append("")
        for item in report["incomparable_volume"]:
            lines.append(
                f"- {item['transfer']} 批 {item['batch']} {item['stage']}："
                f"{item['qty']} {item['unit']} — {item['reason']}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="联产加工月结对账输出")
    parser.add_argument("--data", type=Path, default=Path("data"), help="交换资料目录")
    parser.add_argument("--period", help="关账期间，缺省处理全部期间")
    parser.add_argument("--out", type=Path, default=Path("out"), help="输出目录")
    parser.add_argument("--explain", action="store_true", help="附差异说明（可回溯）")
    args = parser.parse_args()

    month_end = MonthEnd(load_dataset(args.data))
    periods = [args.period] if args.period else month_end.periods()
    args.out.mkdir(parents=True, exist_ok=True)

    for period in periods:
        if period not in month_end.dataset.close:
            parser.error(f"期间 {period} 没有关账约定")
        working = build_working_package(month_end, period)
        issued = build_issued_report(month_end, period)
        stem = args.out / period
        (stem.with_name(stem.name + "_working.json")).write_text(
            json.dumps(working, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (stem.with_name(stem.name + "_issued.json")).write_text(
            json.dumps(issued, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (stem.with_name(stem.name + "_working.md")).write_text(
            emit_markdown(working), encoding="utf-8"
        )
        (stem.with_name(stem.name + "_issued.md")).write_text(
            emit_markdown(issued), encoding="utf-8"
        )
        if args.explain:
            explanations = difference_explanations(month_end, period)
            (stem.with_name(stem.name + "_differences.json")).write_text(
                json.dumps(explanations, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(f"{period}: 对内复盘包与对外月报已写入 {args.out}")


if __name__ == "__main__":
    main()
