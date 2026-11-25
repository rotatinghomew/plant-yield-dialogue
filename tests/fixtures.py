"""测试用合成资料构造：把行字典写成套用 sources.json 约定的 CSV 目录。

合成资料与真实 data/ 同格式，单独写到临时目录；
测试以参数路径装载，避免对仓库数据产生任何写入。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

HEADERS = {
    "transfers": [
        "id",
        "period",
        "sent",
        "received",
        "origin",
        "destination",
        "stage",
        "qty",
        "unit",
        "equiv",
        "conversion",
    ],
    "acceptances": [
        "receipt",
        "revision",
        "batch",
        "customer",
        "occurred",
        "seen",
        "qty",
        "first_pass",
        "rework",
    ],
    "checks": [
        "id",
        "transfer",
        "original_lot",
        "seen",
        "first_failed",
        "qty_inspected",
    ],
    "attribution": ["receipt", "revision", "seen", "owner", "share", "status"],
    "close": [
        "period",
        "cutoff",
        "unit",
        "dimensions",
        "late_min_delta",
        "reissue_until",
    ],
    "sites": ["site", "region"],
    "review": ["id", "receipt", "owner", "opened", "closed", "covered_qty"],
}

DEFAULT_SITES = [
    {"site": "O", "region": "central"},
    {"site": "N", "region": "north"},
    {"site": "S", "region": "south"},
    {"site": "E", "region": "east"},
]


def write_dataset(directory: Path, tables: dict[str, list[dict]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    for name, headers in HEADERS.items():
        rows = tables.get(name, [])
        with (directory / f"{name}.csv").open(
            "w", encoding="utf-8", newline=""
        ) as file:
            writer = csv.DictWriter(file, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in headers})
    (directory / "sources.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )
    return directory


def transfer(
    id,
    period,
    sent,
    received,
    origin,
    destination,
    stage,
    qty,
    unit="piece",
    equiv=None,
    conversion="",
):
    return {
        "id": id,
        "period": period,
        "sent": sent,
        "received": received,
        "origin": origin,
        "destination": destination,
        "stage": stage,
        "qty": str(qty),
        "unit": unit,
        "equiv": "" if equiv is None else str(equiv),
        "conversion": conversion if equiv is None else (conversion or "C1"),
    }


def acceptance(
    receipt, revision, batch, customer, occurred, seen, qty, first_pass, rework
):
    return {
        "receipt": receipt,
        "revision": str(revision),
        "batch": batch,
        "customer": customer,
        "occurred": occurred,
        "seen": seen,
        "qty": str(qty),
        "first_pass": str(first_pass),
        "rework": str(rework),
    }


def check(id, transfer_ref, original_lot, seen, first_failed, qty_inspected):
    return {
        "id": id,
        "transfer": transfer_ref,
        "original_lot": original_lot,
        "seen": seen,
        "first_failed": str(first_failed),
        "qty_inspected": str(qty_inspected),
    }


def attribution(receipt, revision, seen, owner, share, status):
    return {
        "receipt": receipt,
        "revision": str(revision),
        "seen": seen,
        "owner": owner,
        "share": str(share),
        "status": status,
    }


def close_rule(period, cutoff, dimensions="region,customer", delta=3, until=None):
    return {
        "period": period,
        "cutoff": cutoff,
        "unit": "piece",
        "dimensions": dimensions,
        "late_min_delta": str(delta),
        "reissue_until": until or cutoff[:8] + "20",
    }


def review_voucher(id, receipt, owner, opened, closed, covered_qty):
    return {
        "id": id,
        "receipt": receipt,
        "owner": owner,
        "opened": opened,
        "closed": closed or "",
        "covered_qty": str(covered_qty),
    }
