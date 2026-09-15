# -*- coding: utf-8 -*-
"""
一键从 Github版本/stock_data.db 导入基础数据到 data/unified_data.db
只需在首次部署时运行一次（约 10~20 分钟，取决于机器磁盘）。
后续日常更新由 Web 界面「拉取数据」按钮触发增量同步（AKShare）。
"""
# 用法：python scripts/import_data.py [源库路径]
# 源库为旧版 stock_data.db（可选）；没有旧库时无需运行本脚本，
# 直接启动服务后在网页右上角「刷新信息」→「同步数据」即可从零建库。
import os
import sqlite3
import sys
import time
from datetime import date, timedelta

SRC = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "UNIFIED_SRC_DB", r"C:\path\to\stock_data.db")
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "unified_data.db")
DST = os.path.abspath(DST)
RECENT_DAILY_DAYS = 420  # 日K回看自然日（足够覆盖所有技术形态 + 蚂蚁 300 日）


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    code TEXT PRIMARY KEY,
    name TEXT, sector TEXT, exchange TEXT, listing_date TEXT
);
CREATE TABLE IF NOT EXISTS daily (
    code TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    amount REAL, turnover REAL, outstanding_share REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_code ON daily(code);
CREATE INDEX IF NOT EXISTS idx_daily_date ON daily(date);
CREATE TABLE IF NOT EXISTS valuation (
    code TEXT, date TEXT, pe_ttm REAL, pb REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_valuation_code ON valuation(code);
CREATE TABLE IF NOT EXISTS dividend (
    code TEXT, ex_date TEXT, cash_per_10 REAL,
    PRIMARY KEY (code, ex_date)
);
CREATE TABLE IF NOT EXISTS sector_map (code TEXT PRIMARY KEY, sector TEXT);
CREATE TABLE IF NOT EXISTS sector_boards (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS ant_index (
    symbol TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def import_table(src_conn, dst_conn, table, columns, where=None, batch=5000):
    """流式拷贝一张表到目标库。"""
    cols = ", ".join(columns)
    sel = f"SELECT {cols} FROM {table}" + (f" WHERE {where}" if where else "")
    insert_sql = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({','.join('?'*len(columns))})"
    cur_src = src_conn.execute(sel)
    dst_conn.execute("BEGIN")
    buf, total, t0 = [], 0, time.time()
    while True:
        row = cur_src.fetchone()
        if row is None:
            break
        buf.append(row)
        if len(buf) >= batch:
            dst_conn.executemany(insert_sql, buf)
            dst_conn.commit()
            total += len(buf)
            buf.clear()
            log(f"  {table}: {total:,} 行 ({time.time()-t0:.0f}s)")
    if buf:
        dst_conn.executemany(insert_sql, buf)
        dst_conn.commit()
        total += len(buf)
    log(f"  {table}: 完成 {total:,} 行 ({time.time()-t0:.0f}s)")


def main():
    t_start = time.time()
    log(f"源: {SRC}")
    log(f"目标: {DST}")

    if not os.path.exists(SRC):
        log(f"ERROR: 源数据库不存在 {SRC}")
        sys.exit(1)
    if os.path.exists(DST):
        log(f"目标数据库已存在，跳过导入。如需重建请删除该文件后重跑。")
        return

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True, timeout=60)
    dst = sqlite3.connect(DST, timeout=60)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")
    dst.executescript(SCHEMA)
    dst.commit()

    # 1) 基础小表
    log("=== 1) meta / sector_map / sector_boards / dividend / ant_index ===")
    import_table(src, dst, "meta", ["code", "name", "sector", "exchange", "listing_date"])
    import_table(src, dst, "sector_map", ["code", "sector"])
    import_table(src, dst, "sector_boards", ["name"])
    import_table(src, dst, "dividend", ["code", "ex_date", "cash_per_10"])
    import_table(src, dst, "ant_index",
                 ["symbol", "date", "open", "high", "low", "close", "volume"])

    # 2) valuation 全量（PE/PB 历史分位需要 10 年）
    log("=== 2) valuation（PE/PB 历史，约 10 分钟） ===")
    import_table(src, dst, "valuation", ["code", "date", "pe_ttm", "pb"])

    # 3) daily：最近 RECENT_DAILY_DAYS 自然日
    cutoff = (date.today() - timedelta(days=RECENT_DAILY_DAYS)).strftime("%Y-%m-%d")
    log(f"=== 3) daily（>= {cutoff}） ===")
    import_table(src, dst, "daily",
                 ["code", "date", "open", "high", "low", "close", "volume"],
                 where=f"date >= '{cutoff}'", batch=10000)

    # 4) 用 ant_daily 补全 amount / turnover / outstanding_share
    log("=== 4) 用 ant_daily 补全 amount/turnover/outstanding_share ===")
    ant_min = src.execute("SELECT MIN(date) FROM ant_daily").fetchone()[0]
    ant_max = src.execute("SELECT MAX(date) FROM ant_daily").fetchone()[0]
    log(f"  ant_daily 日期范围: {ant_min} ~ {ant_max}")
    cur = src.execute(
        "SELECT code, date, amount, turnover, outstanding_share FROM ant_daily")
    dst.execute("BEGIN")
    buf, n = [], 0
    insert_sql = ("UPDATE daily SET amount=?, turnover=?, outstanding_share=? "
                   "WHERE code=? AND date=?")
    while True:
        row = cur.fetchone()
        if row is None:
            break
        code, dt, amount, turnover, out_share = row
        buf.append((amount, turnover, out_share, code, dt))
        if len(buf) >= 5000:
            dst.executemany(insert_sql, buf)
            dst.commit()
            n += len(buf)
            buf.clear()
            if n % 50000 == 0:
                log(f"  补全: {n:,} 行")
    if buf:
        dst.executemany(insert_sql, buf)
        dst.commit()
        n += len(buf)
    log(f"  补全完成: {n:,} 行")

    # 5) 对最近几天 ant_daily 覆盖不到的日期，估算 amount = volume * close * 100
    log("=== 5) 对最近几天无 ant_daily 的行估算 amount ===")
    cur = dst.execute(
        "SELECT code, date, volume, close FROM daily "
        "WHERE amount IS NULL AND date >= ?",
        (ant_max,))
    buf, n = [], 0
    update_sql = "UPDATE daily SET amount=? WHERE code=? AND date=?"
    while True:
        row = cur.fetchone()
        if row is None:
            break
        code, dt, vol, close = row
        if vol and close:
            amount = float(vol) * float(close) * 100.0  # 手 -> 股 -> 元
            buf.append((amount, code, dt))
        if len(buf) >= 5000:
            dst.executemany(update_sql, buf)
            dst.commit()
            n += len(buf)
            buf.clear()
    if buf:
        dst.executemany(update_sql, buf)
        dst.commit()
        n += len(buf)
    log(f"  估算 amount: {n:,} 行")

    # 6) 统计
    log("=== 导入完成统计 ===")
    for t in ("meta", "daily", "valuation", "dividend", "sector_map",
              "sector_boards", "ant_index"):
        c = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        log(f"  {t}: {c:,} 行")
    src.close()
    dst.close()
    log(f"全部完成，耗时 {(time.time()-t_start)/60:.1f} 分钟")


if __name__ == "__main__":
    main()