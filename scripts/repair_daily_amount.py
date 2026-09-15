# -*- coding: utf-8 -*-
"""日K成交额污染修复（一次性工具，可重复执行，幂等）。

## 背景
2026-09-07 排查发现：`daily` 表存在一批 **成交额被放大 100 倍** 的脏行。
特征（自洽比）：

    ratio = amount / (volume * close)  ∈ [40, 250]  且  turnover / outstanding_share 为 NULL

根因：早期版本的归一化兜底把成交额算成 `volume * close * 100`（把「手→股」的
换算误乘到了成交额上），且当时兜底源缺少 turnover/流通股本。写库后所有依赖
成交额、量能、换手的筛选与预警全部失真。

## 为什么不直接 amount/100
虽然可以就地除 100，但本工具选择 **从数据源重新拉取覆盖**，理由：
  1. 更能自证正确性——拉回来的值与现库值可比对，而不是照假设改数；
  2. 顺带补齐缺失的 turnover / outstanding_share（腾讯源行这两列为空）。

## 安全措施（绝不反向更新）
  * 只覆盖 **被判定为污染的那批 (code, date)**，其余行一个字节都不动；
  * 拉取失败 / 拉不到该日期 → 跳过并计数，不删除、不改写原有行；
  * 写入前对新值做自洽校验，新值仍不在 [0.5, 2.0] 区间则拒绝写入并计数；
  * 全程 INSERT OR REPLACE（按主键幂等），中断后重跑安全。

## 用法
    python scripts/repair_daily_amount.py            # 干跑：只扫描不改写
    python scripts/repair_daily_amount.py --apply    # 实际修复
    python scripts/repair_daily_amount.py --apply --limit 200   # 小批量试跑
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

_HERE = __file__.replace("\\", "/")
_ROOT = _HERE.rsplit("/", 2)[0]
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core import db, sync  # noqa: E402

# 污染特征区间：单位错误必然落在百倍量级；前复权/送转造成的偏移一般 < 10，
# 取 40~250 可稳定区分，不会误伤比亚迪这类高送转个股（实测其自洽比约 3.06）。
BAD_LO, BAD_HI = 40.0, 250.0
# 修复后允许的自洽比区间（前复权会让比值略大于 1，故上界取 2.0）
OK_LO, OK_HI = 0.5, 2.0


def scan_bad(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """扫描污染行，返回 {code: {date, ...}}。"""
    sql = """
        SELECT code, date FROM (
            SELECT code, date, amount / (volume * close) AS r
            FROM daily
            WHERE amount IS NOT NULL AND amount > 0
              AND volume IS NOT NULL AND volume > 0
              AND close IS NOT NULL AND close > 0
        ) WHERE r BETWEEN ? AND ?
    """
    out: dict[str, set[str]] = {}
    for code, d in conn.execute(sql, (BAD_LO, BAD_HI)):
        out.setdefault(str(code), set()).add(str(d)[:10])
    return out


def _ratio(row) -> float | None:
    """对单行 (close, volume, amount) 计算自洽比。"""
    try:
        close, volume, amount = float(row[0]), float(row[1]), float(row[2])
    except (TypeError, ValueError):
        return None
    if not (close > 0 and volume > 0 and amount > 0):
        return None
    return amount / (volume * close)


def _iso(d) -> str | None:
    if d is None:
        return None
    try:
        return d.strftime("%Y-%m-%d")
    except AttributeError:
        s = str(d)[:10]
        return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else None


def repair_one(code: str, dates: set[str], wconn, stats: dict) -> None:
    """重拉单只股票的污染日期并覆盖。"""
    lo = min(dates)
    hi = max(dates)
    try:
        d0 = datetime.strptime(lo, "%Y-%m-%d").date()
        d1 = datetime.strptime(hi, "%Y-%m-%d").date()
        # 前后各留 1 天缓冲，确保目标日期一定落在返回区间内
        bars = sync.fetch_daily(code, d0 - timedelta(days=1), d1 + timedelta(days=1))
    except Exception as e:  # noqa: BLE001
        stats["fetch_fail"] += 1
        stats.setdefault("errors", []).append(f"{code}: {type(e).__name__}: {e}")
        return
    if bars is None or not len(bars):
        stats["fetch_empty"] += 1
        return

    def _f(x):
        """NaN/NaT/None → None（SQLite 写 NULL），其余转 float。"""
        if x is None or x != x:           # noqa: PLR0124 —— x != x 是 NaN 判定
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    rows = []
    for r in bars.itertuples(index=False):
        if _iso(r.date) not in dates:
            continue                      # 只处理被判定污染的那批日期
        ratio = _ratio((r.close, r.volume, getattr(r, "amount", None)))
        if ratio is None or not (OK_LO <= ratio <= OK_HI):
            stats["reject"] += 1          # 新值仍不自洽 → 拒绝写入，保留原样
            continue
        rows.append((
            str(code), _iso(r.date),
            _f(r.open), _f(r.high), _f(r.low), _f(r.close),
            _f(r.volume), _f(getattr(r, "amount", None)),
            _f(getattr(r, "turnover", None)),
            _f(getattr(r, "outstanding_share", None)),
        ))
    if not rows:
        stats["no_valid"] += 1
        return
    try:
        with db.write_lock():
            wconn.executemany(
                "INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            wconn.commit()
        stats["fixed_rows"] += len(rows)
        stats["fixed_codes"] += 1
    except Exception as e:  # noqa: BLE001
        stats["write_fail"] += 1
        stats.setdefault("errors", []).append(f"{code}: 写库失败 {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认干跑）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少只（0=不限）")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    import os
    db.set_db_path(os.environ.get(
        "UNIFIED_DB_PATH", os.path.join(_ROOT, "data", "unified_data.db")))
    conn = db.writer()
    db.ensure_schema(conn)
    bad = scan_bad(conn)
    total_rows = sum(len(v) for v in bad.items())
    print(f"扫描结果：污染行 {total_rows} 行，涉及 {len(bad)} 只股票")
    if not bad:
        print("无可修复数据。")
        return 0
    ds = sorted({d for v in bad.values() for d in v})
    print(f"日期范围：{ds[0]} ~ {ds[-1]}（{len(ds)} 个交易日）")
    if not args.apply:
        print("\n[干跑] 未做任何改写。确认无误后加 --apply 执行修复。")
        return 0

    codes = sorted(bad)
    if args.limit:
        codes = codes[:args.limit]
        print(f"限定处理前 {len(codes)} 只")

    stats = {"fixed_rows": 0, "fixed_codes": 0, "fetch_fail": 0,
             "fetch_empty": 0, "reject": 0, "no_valid": 0, "write_fail": 0,
             "errors": []}
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(repair_one, c, bad[c], conn, stats): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            try:
                f.result()
            except Exception:  # noqa: BLE001
                stats["fetch_fail"] += 1
            if done % 200 == 0 or done == len(codes):
                print(f"  进度 {done}/{len(codes)} | 已修 {stats['fixed_rows']} 行")
    print(f"\n耗时 {time.perf_counter() - t0:.1f}s")
    print(f"修复：{stats['fixed_codes']} 只 / {stats['fixed_rows']} 行")
    print(f"跳过：拉数失败 {stats['fetch_fail']}、空结果 {stats['fetch_empty']}、"
          f"新值不自洽 {stats['reject']}、无有效行 {stats['no_valid']}、"
          f"写库失败 {stats['write_fail']}")
    for e in stats["errors"][:10]:
        print("   !", e)

    left = scan_bad(conn)
    left_rows = sum(len(v) for v in left.items())
    print(f"\n复检：剩余污染行 {left_rows} 行 / {len(left)} 只")
    if left_rows == 0:
        print("全部修复完成。")
    return 0 if left_rows == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
