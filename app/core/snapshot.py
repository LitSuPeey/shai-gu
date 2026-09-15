# -*- coding: utf-8 -*-
"""快照表构建：把每只股票的最新行情/估值/分位/股息率预计算成一张 5554 行小表。

筛选模块（PE/PB 低估、高股息、最新行情）直接查这张表，避免每次扫描 940 万行估值。
构建是一次性（数据同步后刷新一次），可接受数十秒与较大内存。
"""
import time
from typing import Optional

import numpy as np
import pandas as pd

from . import db


def build_snapshot(progress_cb=None) -> dict:
    """构建/刷新 snapshot 表。返回统计信息。"""
    t0 = time.time()
    rconn = db.reader()
    wconn = db.writer()
    db.ensure_schema(wconn)  # 确保 snapshot 表存在

    def cb(frac, msg):
        if progress_cb:
            progress_cb(frac, 1.0, msg)

    # ---------- 1. 最新日线 + 前收（纯 SQL，快） ----------
    cb(0.05, "日线最新快照")
    daily = pd.read_sql_query(
        "SELECT d.code, d.date, d.close, d.volume, d.amount, d.turnover, "
        "       (SELECT d2.close FROM daily d2 "
        "        WHERE d2.code=d.code AND d2.date<d.date "
        "        ORDER BY d2.date DESC LIMIT 1) AS prev_close "
        "FROM daily d "
        "WHERE d.date = (SELECT MAX(date) FROM daily WHERE code=d.code)",
        rconn)

    # ---------- 2. 最新估值 + PE/PB 分位（pandas groupby rank） ----------
    cb(0.25, "估值分位计算")
    val = pd.read_sql_query(
        "SELECT code, date, pe_ttm, pb FROM valuation", rconn)
    if not val.empty:
        val["code"] = val["code"].astype("category")
        val["pe_ttm"] = val["pe_ttm"].astype("float32")
        val["pb"] = val["pb"].astype("float32")
        val = val.sort_values(["code", "date"])
        val["cnt"] = val.groupby("code")["pe_ttm"].transform("count")
        val["pe_pct"] = (val.groupby("code")["pe_ttm"].rank()
                         / val["cnt"].replace(0, np.nan) * 100.0)
        val["pb_pct"] = (val.groupby("code")["pb"].rank()
                         / val["cnt"].replace(0, np.nan) * 100.0)
        val_latest = (val.groupby("code", observed=True).tail(1)
                      [["code", "pe_ttm", "pb", "pe_pct", "pb_pct"]])
    else:
        val_latest = pd.DataFrame(
            columns=["code", "pe_ttm", "pb", "pe_pct", "pb_pct"])

    # ---------- 3. 最近一年股息率（dividend 仅 5 万行，轻） ----------
    cb(0.55, "股息率计算")
    latest_date = daily["date"].max() if not daily.empty else None
    div = pd.read_sql_query(
        "SELECT code, ex_date, cash_per_10 FROM dividend", rconn)
    div_snap = pd.DataFrame(columns=["code", "div_yield"])
    if not div.empty and latest_date:
        import datetime as _dt
        try:
            cutoff = (_dt.datetime.strptime(str(latest_date), "%Y-%m-%d")
                      - _dt.timedelta(days=365)).strftime("%Y-%m-%d")
        except Exception:
            cutoff = None
        if cutoff:
            div_recent = div[div["ex_date"] >= cutoff]
            dps = div_recent.groupby("code")["cash_per_10"].sum() / 10.0
            div_snap = pd.DataFrame({
                "code": dps.index.astype(str),
                "div_yield": dps.values,
            })

    # ---------- 4. 合并 ----------
    cb(0.7, "合并写入")
    snap = daily.merge(val_latest, on="code", how="left")
    snap = snap.merge(div_snap, on="code", how="left")
    snap["code"] = snap["code"].astype(str)
    if "prev_close" in snap.columns:
        snap["pct_chg"] = np.where(
            snap["prev_close"].notna() & (snap["prev_close"] != 0),
            (snap["close"] - snap["prev_close"]) / snap["prev_close"] * 100.0,
            np.nan)
    else:
        snap["pct_chg"] = np.nan

    cols = ["code", "date", "close", "prev_close", "pct_chg",
            "volume", "amount", "turnover",
            "pe_ttm", "pb", "pe_pct", "pb_pct", "div_yield"]
    snap = snap[[c for c in cols if c in snap.columns]]

    # ---------- 5. 写入（先清空再批量写） ----------
    cb(0.85, "写入数据库")
    snap = snap.where(pd.notna(snap), None)
    records = [tuple(None if (isinstance(v, float) and (v != v)) else v
                     for v in r) for r in snap.itertuples(index=False, name=None)]
    with db.write_lock():
        wconn.execute("DELETE FROM snapshot")
        wconn.executemany(
            "INSERT OR REPLACE INTO snapshot VALUES ("
            + ",".join("?" * len(cols)) + ")", records)
        wconn.commit()

    cb(1.0, "完成")
    return {
        "rows": len(snap),
        "elapsed": round(time.time() - t0, 2),
    }


def latest_rows(limit: int = 500) -> list[dict]:
    """读取快照表（供筛选模块直接查）。"""
    rconn = db.reader()
    df = pd.read_sql_query(
        "SELECT * FROM snapshot ORDER BY code LIMIT ?", rconn,
        params=(int(limit),))
    return df.where(pd.notna(df), None).to_dict(orient="records")


def snapshot_ready() -> bool:
    """快照表是否已构建。"""
    rconn = db.reader()
    try:
        n = rconn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0]
        return n > 0
    except Exception:
        return False
