# -*- coding: utf-8 -*-
"""批量行情加载：把「逐只 SELECT」改成「整批一次取回 + 内存切分」。

背景
====
分析慢的根因不是算法，而是「5000+ 只股票 = 5000+ 次 pandas 查询」。
实测（本机，unified_data.db 1.3GB / 1.57M 日K行）：

    逐只 500 只(30日)   0.13s
    批量 500 只(全历史) 0.26s   ← 全历史也只要逐只的 2 倍时间
    批量 500 只(近30日) 0.04s

也就是说：批量取一次的成本 ≈ 固定开销，和股票数几乎无关；而逐只查询的
成本随股票数线性增长。全市场 5554 只时，逐只约 5554 次查询，
批量只需 1 次（或按窗口切几批）。

设计要点
========
1. **只查一次，全部塞进内存**，用 groupby 按 code 切分后按需取用。
2. **按窗口裁剪**：日K 只取最近 N 个交易日（用交易日历而非自然日，
   避免节假日把窗口算少），内存占用可控。
3. **惰性加载 + 进程内缓存**：同一轮分析里多个子模块反复要数据时只查一次。
4. **不改变语义**：切出来的每个 DataFrame 与逐只查询结果逐行等价
   （列名、顺序、排序方向都对齐各模块原实现）。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Dict, Iterable, Optional

import pandas as pd

from . import db

# ---- 进程内缓存（按 (库, 窗口交易日数) 为键；数据同步后会 bump 版本号） ----
_CACHE: Dict[tuple, Dict[str, pd.DataFrame]] = {}
_ORDER: Dict[tuple, list] = {}
_LOCK = threading.RLock()
_VERSION = 0

# 各表默认取的列
_DAILY_COLS = ["date", "open", "high", "low", "close",
               "volume", "amount", "turnover", "outstanding_share"]


def bump_version() -> None:
    """数据变动（同步完成）后调用，让下一次分析重新加载。"""
    global _VERSION
    with _LOCK:
        _VERSION += 1
        _CACHE.clear()
        _ORDER.clear()


def _recent_dates(n: int) -> list:
    """最近 n 个交易日（升序）。用真实交易日历，避免长假把窗口砍掉。"""
    rconn = db.reader()
    rows = rconn.execute(
        "SELECT DISTINCT date FROM daily ORDER BY date DESC LIMIT ?",
        (int(n),)).fetchall()
    return sorted(str(r[0]) for r in rows)


def load_daily_map(window_days: int = 0,
                   codes: Optional[Iterable[str]] = None) -> Dict[str, pd.DataFrame]:
    """一次取回全市场（或指定 codes）的日K，按 code 切成 dict。

    window_days > 0 时只取最近 window_days 个交易日（按交易日历）；
    = 0 表示全历史（慎用，1.57M 行）。

    返回的每个 DataFrame：date 为 datetime，按日期**升序**，
    列 = date/open/high/low/close/volume/amount/turnover/outstanding_share。
    """
    key = ("daily", int(window_days), _VERSION)
    with _LOCK:
        if key in _CACHE:
            full = _CACHE[key]
            if codes is None:
                return full
            return {str(c): full[str(c)] for c in codes if str(c) in full}

    rconn = db.reader()
    cols = ", ".join(_DAILY_COLS)
    if window_days and window_days > 0:
        cut = _recent_dates(window_days)
        if not cut:
            return {}
        sql = (f"SELECT code, {cols} FROM daily WHERE date>=? "
               f"ORDER BY code, date")
        df = pd.read_sql_query(sql, rconn, params=(cut[0],))
    else:
        df = pd.read_sql_query(
            f"SELECT code, {cols} FROM daily ORDER BY code, date", rconn)
    if df.empty:
        return {}

    df["code"] = df["code"].astype(str)
    df["date"] = pd.to_datetime(df["date"])
    out: Dict[str, pd.DataFrame] = {}
    for code, g in df.groupby("code", sort=False):
        out[str(code)] = g.drop(columns="code").reset_index(drop=True)

    with _LOCK:
        _CACHE[key] = out
    if codes is None:
        return out
    return {str(c): out[str(c)] for c in codes if str(c) in out}


def load_daily_map_since(window_days: int, codes: Optional[Iterable[str]] = None
                         ) -> Dict[str, pd.DataFrame]:
    """同 load_daily_map，但语义更明确：只取最近 window_days 个交易日。"""
    return load_daily_map(window_days=window_days, codes=codes)


def cached_codes() -> set:
    """当前已缓存的 code（供调试/统计）。"""
    with _LOCK:
        out = set()
        for v in _CACHE.values():
            out |= set(v.keys())
        return out


def load_hist_map(codes: Optional[Iterable[str]] = None
                  ) -> Dict[str, pd.DataFrame]:
    """一次取回 hist.db（长周期库）的全部日K，按 code 切成 dict。

    列 = date/open/high/low/close/volume/amount，date 为 datetime、升序。
    hist.db 有 1600 万行，只在需要时加载一次并常驻（进程内）。
    """
    key = ("hist", 0, _VERSION)
    with _LOCK:
        if key in _CACHE:
            full = _CACHE[key]
            if codes is None:
                return full
            return {str(c): full[str(c)] for c in codes if str(c) in full}

    # 直接按主库路径派生 hist.db 路径，避免 import ant1000 造成循环依赖
    root = os.path.dirname(os.path.abspath(db.db_path()))
    path = os.path.join(root, "hist.db")
    abs_path = os.path.abspath(path).replace("\\", "/")
    conn = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True, timeout=60)
    conn.execute("PRAGMA cache_size = -131072")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA query_only = ON")
    df = pd.read_sql_query(
        "SELECT code, date, open, high, low, close, volume, amount "
        "FROM daily_hist ORDER BY code, date", conn)
    if df.empty:
        return {}
    df["code"] = df["code"].astype(str)
    df["date"] = pd.to_datetime(df["date"])
    out: Dict[str, pd.DataFrame] = {}
    for code, g in df.groupby("code", sort=False):
        out[str(code)] = g.drop(columns="code").reset_index(drop=True)

    with _LOCK:
        _CACHE[key] = out
    if codes is None:
        return out
    return {str(c): out[str(c)] for c in codes if str(c) in out}
