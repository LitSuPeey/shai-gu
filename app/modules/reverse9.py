# -*- coding: utf-8 -*-
"""9Reverse9 —— 神奇九转「即将触发」筛选 + 历史反转时点统计。

指标定义（神奇九转 / TD Sequential Setup）
==========================================
以 4 个交易日前的收盘价为基准（ref_gap=4）：
  买入设置：close[t] < close[t-4]，从首次满足起连续计数；连续满 9 天 → **买入九转**（低九）
  卖出设置：close[t] > close[t-4]，连续满 9 天 → **卖出九转**（高九）
计数在条件中断时归零；满 9 后计数复位，此后可再走出新的 9（第 18、27… 天同为信号）。
本模块全程只使用 t 时刻及之前的收盘数据，不含任何未来函数。

程序一：筛选「即将到达买入九转」的股票
=====================================
当前买入计数 c ∈ [9-N, 8]（N = near_remaining，默认 2 → 即 c ∈ {7, 8}）。
N=1 只留「还差 1 天」；N=3 放宽到「还差 3 天以内」。c=9（今日刚好达成）默认不计入
（可开 include_triggered），因为它已经不是「即将」而是「到达」。
「当前」统一锚定在**参考日 ref_date**：覆盖率 ≥80% 的最新交易日。
（unified_data.db 最近两天常常只有部分股票同步完成，若各股各用最后一根，
 计数口径会不一致 → 用覆盖率闸门挑一个全市场都有的日期，口径统一。）

程序二：历史反转时点统计（对程序一筛出的股票）
============================================
拉取长历史（hist.db 全历史 ∪ unified daily 补齐最新，两者同为前复权口径，
实测重叠区间逐行相等），找出历史上全部买入九转 / 卖出九转信号日 i，
再在**对称窗口** [i-W, i+W] 内定位「股票真正开始反转」的时间节点 j：
  买入九转：j = 窗口内**最低价**所在日  → n = j - i
             n > 0 「9Rev n天后开始真正反弹」/ n = 0 信号日即底 / n < 0「9Rev 反弹起点在信号前|n|天」
  卖出九转：j = 窗口内**最高价**所在日  → m = i - j
             m > 0 「9Rev 提前m天开始下行」/ m = 0 信号日即顶 / m < 0「9Rev 信号后|m|天开始下行」
界内判据采用用户原话的「绝对值 ≤ W」（W = match_window，默认 5）。

**为什么必须用对称窗口**（2026-09-21 实测，900 只 / 17070 个买入信号 / 13546 个卖出信号）：
  买入九转：真底在信号之前的占 24.3%，当天或之后的占 75.7%；
  卖出九转：真顶在信号之后的占 **57.6%**，之前的只占 42.4%。
只把窗口锁在一侧（买入只看信号后、卖出只看信号前）会把另一侧的真实拐点
错报成「窗口内的次极值」，等于报了一个并不成立的「真正反转节点」。

**反转确认**：从真拐点 j 之后 confirm_bars 根内，必须出现 ≥ confirm_pct 的反向运动
（买入看涨幅、卖出看跌幅），否则判为「未确认」丢弃。没有这道闸，
单边下跌途中的任意一根 K 线都会被算成「地板」，统计会整体失真。
靠近数据末端、拐点之后数据不足以确认的信号同样按「待确认」剔除，不污染统计。

排序（严格照用户给定的优先级，逐级生效）
======================================
 1st 历史买入九转的 n 越小越靠前   → 主键 = **仅统计 n ≥ 0 样本**的平均 n 升序。
      （n < 0 表示信号发出时反弹已经开始、属滞后信号，混进均值会让
        「信号越滞后排得越前」，与用户意图相反，故单列展示、不计入主键。）
      并对样本量做**收缩**：n_rank = (Σn + K×全市场均值) / (m + K)。
      理由：用户说的是「历史上**每次** 9 转的 n 越小越靠前」，而实测首版 Top-1
      只靠 **1 个** n=0 样本就以 mean=0 霸榜（全量 413 只里有 21 只样本数 ≤1），
      单点样本谈不上「每次」。收缩后样本多的股票胜出，样本少的被拉回全市场均值。
      K = `shrink_k`（默认 3，可调；设 0 即退回纯平均 n 的字面口径）。
 2nd 一年内卖出九转信号越多越靠前   → 次键 = 近一年卖出九转条数降序
 3rd 代码升序（仅作稳定收尾）

性能
====
不使用进程池：程序一用 batchload 一次性取回全市场近 60 个交易日的日K（1 次查询），
用 numpy 向量化算连续计数，全市场约 1~2s；程序二只对候选股（通常几十到几百只）
做「hist.db 单只索引查询 + 极值定位」，每只 2 次查询、约 3ms。
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..core import batchload
from ..core import db

# ---- 常量 ----
RECENT_WINDOW = 60        # 程序一取数窗口（交易日）：够 20 日均额 + 九转计数
MIN_RECENT_BARS = 15      # 少于该根数无法算九转计数，直接跳过
MIN_HIST_BARS = 60        # 程序二回看下限：太短就没必要统计「历史」
FULL_COVERAGE = 0.80      # 参考日覆盖率闸门（80% 的股票都有当天数据）
MAIN_LEN = 9              # 神奇九转周期长度

DEFAULTS = {
    # —— 程序一：「即将」定义 ——
    "near_remaining": 2,        # 距买入九转还差 ≤ N 天（2 → 当前计数 ∈ {7,8}）
    "include_triggered": False,  # 是否把「今日刚达成第 9 天」也纳入
    "ref_gap": 4,               # 九转比较基准：close[t] vs close[t-ref_gap]
    # —— 程序二：反转定位与确认 ——
    "match_window": 5,          # |n|、|m| 上限（用户要求 ≤5）
    "confirm_pct": 3.0,         # 反转确认幅度（%）
    "confirm_bars": 10,         # 反转确认观察窗口（交易日）
    "hist_years": 5,            # 历史回看年数
    # —— 风控类硬门槛（不做条件的股票直接不参与筛选）——
    "exclude_st": True,
    "min_price": 2.0,
    "max_price": 2000.0,
    "min_amt20_yi": 0.5,        # 20 日均成交额下限（亿元）
    "min_listed_days": 120,
    # —— 输出控制 ——
    "max_results": 600,         # 最多返回多少只
    "show_records": 40,         # 每只股票每侧最多回传多少条历史明细
    # —— 排序 ——
    # 用户规则「历史上每次 9 转的 n 越小越靠前」隐含要求有足够的历史样本。
    # 实测首版 Top-1 只靠 1 个 n=0 样本就以 mean=0 霸榜，与「每次」的语义不符，
    # 故对样本量做收缩：n_rank = (Σn + K×全市场均值) / (m + K)。
    # K=0 即退回「纯平均 n」（完全照字面口径）；K 越大越保守。
    "shrink_k": 3.0,
}

# 说明文案（供 /api/r9/meta 与前端 tooltip 复用）
DOC_LINES = [
    "神奇九转：close 连续 9 天低于 / 高于 4 个交易日前 → 买入 / 卖出九转。",
    "程序一：全市场筛出「即将到达买入九转」的股票（还差天数可调）。",
    "程序二：对这些股票拉长历史，找出历史所有九转信号，并在对称窗口内定位真正的顶/底。",
    "买入九转：n = 真底 − 信号日；卖出九转：m = 信号日 − 真顶。允许为负（|n|、|m| ≤ 窗口）。",
    "实测：卖出九转 57.6% 的真顶落在信号之后 —— 该信号更像「提前预警」而非「滞后的见顶确认」。",
    "反转需被拐点之后的行情确认（否则剔除）；排序主键只统计 n ≥ 0 的样本。",
]


# ================================================================ 九转计数
def _runlen(mask: np.ndarray) -> np.ndarray:
    """连续 True 的游程长度（第 t 位 = 以 t 结尾的连续 True 个数）。O(n) 向量化。"""
    n = int(mask.size)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    idx = np.where(~mask, np.arange(n), -1).astype(np.int64)
    np.maximum.accumulate(idx, out=idx)
    return np.arange(n, dtype=np.int64) - idx


def nine_state(close: np.ndarray, gap: int = 4) -> tuple:
    """返回 (买入信号布尔数组, 卖出信号布尔数组, 买入游程, 卖出游程)。

    信号 = 游程长度 % 9 == 0（满 9 记一次信号并复位，18、27… 同样记）。
    """
    n = int(close.size)
    zero = np.zeros(n, dtype=bool)
    if n <= gap:
        return zero, zero, np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64)
    cond_b = np.zeros(n, dtype=bool)
    cond_s = np.zeros(n, dtype=bool)
    cond_b[gap:] = close[gap:] < close[:-gap]
    cond_s[gap:] = close[gap:] > close[:-gap]
    rb = _runlen(cond_b)
    rs = _runlen(cond_s)
    sig_b = (rb > 0) & (rb % MAIN_LEN == 0)
    sig_s = (rs > 0) & (rs % MAIN_LEN == 0)
    return sig_b, sig_s, rb, rs


def current_count(run_last: int) -> tuple:
    """把「截至最后一根的游程」换算成当前计数。

    returns (count, just_triggered)：
      run_last=0            → (0, False)   不在设置中
      run_last=8            → (8, False)   还差 1 天
      run_last=9            → (9, True)    今日刚好达成九转
      run_last=11           → (2, False)   已达成过，新一轮的第 2 天
    """
    r = int(run_last)
    if r <= 0:
        return 0, False
    if r % MAIN_LEN == 0:
        return MAIN_LEN, True
    return r % MAIN_LEN, False


# ================================================================ 参考日
def _coverage_ref_date(present: list, bars_map: dict) -> Optional[str]:
    """在**所选范围**内挑「覆盖率 ≥80% 的最新交易日」作为参考日。

    注意不能拿「全市场某日的行数」去比对「当前范围股票数」：那样选北交所
    （仅 270 只）时，任何一天的全市场行数都会 ≥ 216，参考日会被误判成最新
    那天，而当天可能根本没有北交所数据。这里改为逐日统计**范围内**真实覆盖。
    """
    cov: dict = {}
    for c in present:
        for d in set(bars_map[c]["date"]):
            cov[d] = cov.get(d, 0) + 1
    if not cov:
        return None
    thr = max(1, int(len(present) * FULL_COVERAGE))
    for d in sorted(cov.keys(), reverse=True):
        if cov[d] >= thr:
            return pd.Timestamp(d).strftime("%Y-%m-%d")
    return pd.Timestamp(max(cov.keys())).strftime("%Y-%m-%d")


# ================================================================ 程序一
def _prefilter(sub: pd.DataFrame, meta: dict, cfg: dict) -> Optional[str]:
    """风控类硬门槛。返回跳过原因，通过返回 None。"""
    if len(sub) < MIN_RECENT_BARS:
        return "数据不足"
    close = float(sub["close"].iloc[-1])
    if not (cfg["min_price"] <= close <= cfg["max_price"]):
        return "价格区间"
    name = str(meta.get("name") or "")
    if cfg["exclude_st"] and ("ST" in name.upper() or "退" in name):
        return "ST/退市"
    amt = sub["amount"].tail(20)
    amt20 = float(amt.mean()) if len(amt) and np.isfinite(amt.mean()) else 0.0
    if amt20 < float(cfg["min_amt20_yi"]) * 1e8:
        return "流动性不足"
    ld = str(meta.get("listing_date") or "")
    if len(ld) >= 10:
        try:
            days = (datetime.now().date() - datetime.strptime(ld[:10], "%Y-%m-%d").date()).days
            if days < int(cfg["min_listed_days"]):
                return "次新股"
        except Exception:  # noqa: BLE001 —— 上市日异常时不剔
            pass
    return None


def screen(cfg: dict, exchange: Optional[str], sectors: Optional[list],
           progress_cb=None) -> tuple:
    """程序一：返回 (候选列表, 跳过统计, 参考日)。每项含 code/name/cur_count 等。"""
    rconn = db.reader()
    where, args = "", []
    if exchange in ("SZ", "SH", "BJ"):
        where = " WHERE exchange=?"
        args.append(exchange)
    if sectors:
        where += (" AND " if where else " WHERE ") + \
            f"sector IN ({','.join('?' * len(sectors))})"
        args.extend(sectors)
    rows = rconn.execute(
        f"SELECT code, name, sector, exchange, listing_date FROM meta{where}",
        tuple(args)).fetchall()
    if not rows:
        return [], {}, None

    # 一次性取回近 60 个交易日的全市场日K（1 次查询），参考日与逐股计数都基于它
    bars_map = batchload.load_daily_map(window_days=RECENT_WINDOW)
    if not bars_map:
        return [], {"无行情数据": len(rows)}, None
    present = [str(r[0]) for r in rows
               if str(r[0]) in bars_map and not bars_map[str(r[0])].empty]

    ref_date = _coverage_ref_date(present, bars_map)
    if not ref_date:
        return [], {"无行情数据": len(rows)}, None
    ref_ts = pd.Timestamp(ref_date)

    gap = int(cfg["ref_gap"])
    near = int(cfg["near_remaining"])
    lo_cnt = max(1, MAIN_LEN - near)

    out: list = []
    skip: dict = {}
    total = len(rows)
    for i, r in enumerate(rows):
        if progress_cb and (i % 500 == 0 or i == total - 1):
            progress_cb(i + 1, total, f"程序一 · 九转计数 {i + 1}/{total}")
        code = str(r[0])
        meta = {"name": r[1], "sector": r[2], "listing_date": r[4]}
        df = bars_map.get(code)
        if df is None or df.empty:
            skip["无行情"] = skip.get("无行情", 0) + 1
            continue
        sub = df[df["date"] <= ref_ts]
        if len(sub) < MIN_RECENT_BARS:
            skip["数据不足"] = skip.get("数据不足", 0) + 1
            continue
        # 参考日当天必须真的有行情：只按 <= ref_date 过滤会让长期停牌股拿 N 天前的
        # 旧K线参与「当前计数」，与其它股票口径不一致（等于在推荐一只停牌的票）。
        if str(sub["date"].iloc[-1])[:10] != ref_date:
            skip["参考日无行情"] = skip.get("参考日无行情", 0) + 1
            continue
        reason = _prefilter(sub, meta, cfg)
        if reason:
            skip[reason] = skip.get(reason, 0) + 1
            continue
        close = pd.to_numeric(sub["close"], errors="coerce").to_numpy(dtype=float)
        if not np.all(np.isfinite(close[-min(len(close), gap + 1):])):
            skip["数据异常"] = skip.get("数据异常", 0) + 1
            continue
        _sb, _ss, rb, _rs = nine_state(close, gap)
        cnt, trig = current_count(int(rb[-1]))
        ok = (trig and cfg["include_triggered"]) or (lo_cnt <= cnt <= MAIN_LEN - 1)
        if not ok:
            continue
        out.append({
            "code": code,
            "name": r[1] or "",
            "sector": r[2] or "",
            "exchange": r[3] or "",
            "cur_count": int(cnt),
            "remain": int(MAIN_LEN - cnt) if not trig else 0,
            "triggered": bool(trig),
            "close": round(float(close[-1]), 2),
        })
    return out, skip, ref_date


# ================================================================ 程序二
def _load_history(rconn: sqlite3.Connection, code: str, hist_bars: int,
                  ref_date: str) -> tuple:
    """长历史：hist.db（全历史，前复权）∪ unified daily（补齐最新）。

    实测两库重叠区间逐行相等（同为前复权），可安全合并；unified 侧只取
    hist 最后日期之后的增量，避免重复行。
    **两库都必须截到 ref_date**：hist.db 的部分股票已同步到 ref_date 之后，
    若不过滤，历史会越过「当前计数」的基准日，凭空多出一段相对未来，
    与程序一的口径自相矛盾。
    """
    dates: list = []
    o: list = []
    h: list = []
    l: list = []
    c: list = []
    root = os.path.dirname(os.path.abspath(db.db_path()))
    hist_path = os.path.join(root, "hist.db")
    last_hist = ""
    if os.path.exists(hist_path):
        try:
            hp = os.path.abspath(hist_path).replace("\\", "/")
            hc = sqlite3.connect(f"file:{hp}?mode=ro", uri=True, timeout=30)
            try:
                hs = hc.execute(
                    "SELECT date, open, high, low, close FROM daily_hist "
                    "WHERE code=? AND date<=? ORDER BY date DESC LIMIT ?",
                    (code, ref_date, int(hist_bars))).fetchall()
            finally:
                hc.close()
            for d, oo, hh, ll, cc in reversed(hs):
                dates.append(str(d)[:10]); o.append(oo); h.append(hh)
                l.append(ll); c.append(cc)
            if dates:
                last_hist = dates[-1]
        except Exception:  # noqa: BLE001 —— hist.db 缺失/损坏时退回主库
            dates, o, h, l, c = [], [], [], [], []
            last_hist = ""
    if last_hist:
        us = rconn.execute(
            "SELECT date, open, high, low, close FROM daily "
            "WHERE code=? AND date>? AND date<=? ORDER BY date",
            (code, last_hist, ref_date)).fetchall()
    else:
        us = rconn.execute(
            "SELECT date, open, high, low, close FROM daily "
            "WHERE code=? AND date<=? ORDER BY date DESC LIMIT ?",
            (code, ref_date, int(hist_bars))).fetchall()
        us = list(reversed(us))
    for d, oo, hh, ll, cc in us:
        dates.append(str(d)[:10]); o.append(oo); h.append(hh)
        l.append(ll); c.append(cc)
    if not dates:
        return ([], np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0))
    return (dates,
            np.asarray(o, dtype=float), np.asarray(h, dtype=float),
            np.asarray(l, dtype=float), np.asarray(c, dtype=float))


def _analyze(dates: list, high: np.ndarray, low: np.ndarray, close: np.ndarray,
             cfg: dict) -> dict:
    """在单只股票的长历史上提取九转信号 + 真正反转节点（含确认闸门）。

    极值搜索用**对称窗口** [i-W, i+W]：真拐点既可能在信号之后、也可能在之前，
    只锁一侧会把另一侧的真实拐点错报成"窗口内的次极值"。实测（900 只 / 17070 个买入
    信号 / 13546 个卖出信号）：
        买入九转 24.3% 的真底落在信号之前（信号滞后），75.7% 在当天或之后；
        卖出九转 57.6% 的真顶落在信号之后（信号其实是提前预警），42.4% 在之前。
    所以 n / m 允许为负，界内判据是用户原话的「绝对值 ≤ W」：
        n = 真底 − 信号日   （n>0 反弹在信号后；n=0 信号日即底；n<0 底在信号前）
        m = 信号日 − 真顶   （m>0 真顶在信号前「提前」；m<0 真顶在信号后）
    确认闸从**真拐点 j 之后**起算（而非从信号日起算）：只有拐点之后确实反转了才算数。
    """
    gap = int(cfg["ref_gap"])
    W = max(1, int(cfg["match_window"]))
    cpct = float(cfg["confirm_pct"]) / 100.0
    cbars = max(1, int(cfg["confirm_bars"]))
    n = int(close.size)
    sig_b, sig_s, _rb, _rs = nine_state(close, gap)

    buy: list = []
    sell: list = []
    pend_b = pend_s = 0

    def _extreme(seg: np.ndarray, base: int, sig: int, is_min: bool) -> int:
        """窗口内取极值；平局时取**离信号日最近**的那一天（不偏袒任何一侧）。"""
        m = float(seg.min()) if is_min else float(seg.max())
        cand = np.flatnonzero(seg == m) + base
        return int(cand[np.argmin(np.abs(cand - sig))])

    for i in np.flatnonzero(sig_b):
        i = int(i)
        lo, hi = max(0, i - W), min(n - 1, i + W)
        j = _extreme(low[lo:hi + 1], lo, i, True)
        k2 = min(n - 1, j + cbars)
        if k2 <= j:                       # 拐点之后数据不足 → 待确认
            pend_b += 1
            continue
        if float(np.max(close[j + 1:k2 + 1])) < float(close[j]) * (1.0 + cpct):
            continue                       # 拐点后未出现有效反弹 → 丢弃
        nn = j - i
        if abs(nn) > W:
            continue
        buy.append({
            "date": dates[i], "rev_date": dates[j], "n": int(nn),
            "price": round(float(close[i]), 2),
            "rev_price": round(float(low[j]), 2),
            "text": (f"9Rev {nn}天后开始真正反弹" if nn >= 0
                     else f"9Rev 反弹起点在信号前{-nn}天"),
        })

    for i in np.flatnonzero(sig_s):
        i = int(i)
        lo, hi = max(0, i - W), min(n - 1, i + W)
        j = _extreme(high[lo:hi + 1], lo, i, False)
        k2 = min(n - 1, j + cbars)
        if k2 <= j:                       # 拐点之后数据不足 → 待确认
            pend_s += 1
            continue
        if float(np.min(close[j + 1:k2 + 1])) > float(close[j]) * (1.0 - cpct):
            continue                       # 拐点后未出现有效下行 → 丢弃
        mm = i - j
        if abs(mm) > W:
            continue
        sell.append({
            "date": dates[i], "rev_date": dates[j], "m": int(mm),
            "price": round(float(close[i]), 2),
            "rev_price": round(float(high[j]), 2),
            "text": (f"9Rev 提前{mm}天开始下行" if mm >= 0
                     else f"9Rev 信号后{-mm}天开始下行"),
        })

    return {"buy": buy, "sell": sell, "pending_buy": pend_b, "pending_sell": pend_s}


# ================================================================ 主入口
def run(cfg: Optional[dict] = None, exchange: Optional[str] = None,
        sectors: Optional[list] = None, progress_cb=None) -> dict:
    """9Reverse9 全流程：程序一筛 → 程序二统计 → 按优先级排序。"""
    c = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if v is not None:
            c[k] = v
    c["near_remaining"] = int(max(1, min(8, int(c["near_remaining"]))))
    c["ref_gap"] = int(max(1, min(10, int(c["ref_gap"]))))
    c["match_window"] = int(max(1, min(10, int(c["match_window"]))))
    c["confirm_bars"] = int(max(1, min(60, int(c["confirm_bars"]))))
    c["confirm_pct"] = float(max(0.0, min(50.0, float(c["confirm_pct"]))))
    c["hist_years"] = float(max(0.5, min(25.0, float(c["hist_years"]))))
    c["show_records"] = int(max(3, min(200, int(c["show_records"]))))
    c["max_results"] = int(max(1, min(3000, int(c["max_results"]))))
    c["shrink_k"] = float(max(0.0, min(50.0, float(c.get("shrink_k", 3.0)))))

    t0 = time.time()
    cands, skip_stats, ref_date = screen(c, exchange, sectors, progress_cb)
    if progress_cb:
        progress_cb(1, 1, f"程序一完成：候选 {len(cands)} 只，正在拉取历史…")

    hist_bars = int(c["hist_years"] * 250)
    rconn = db.reader()
    # 近一年的起点（用于「一年内卖出九转」计数）
    try:
        cut_1y = (datetime.strptime(ref_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        cut_1y = ""

    total = len(cands)
    done = 0
    for it in cands:
        done += 1
        if progress_cb and (done % 10 == 0 or done == total):
            progress_cb(done, max(total, 1), f"程序二 · 历史九转统计 {done}/{total}")
        try:
            dates, o, h, l, cl = _load_history(rconn, it["code"], hist_bars, ref_date)
            if len(dates) < MIN_HIST_BARS:
                it["buy_history"] = []
                it["sell_history"] = []
                it["buy_n"] = it["sell_n"] = it["sell_1y"] = 0
                it["n_pos"] = it["n_lag"] = it["m_pos"] = it["m_neg"] = 0
                it["n_pos_sum"] = 0
                it["n_mean"] = it["n_min"] = None
                it["bars_hist"] = len(dates)
                it["note"] = "历史不足"
                continue
            res = _analyze(dates, h, l, cl, c)
            buy = res["buy"]
            sell = res["sell"]
            it["bars_hist"] = len(dates)
            it["buy_n"] = len(buy)
            it["sell_n"] = len(sell)
            it["pending_buy"] = res["pending_buy"]
            it["pending_sell"] = res["pending_sell"]
            # 排序主键只用 n ≥ 0 的样本：n < 0 表示信号发出时反弹已经开始（滞后信号），
            # 若混进均值，就会出现「信号越滞后、平均 n 越小、排得越前」的反直觉结果。
            pos_n = [b["n"] for b in buy if b["n"] >= 0]
            it["n_pos"] = len(pos_n)
            it["n_lag"] = len(buy) - len(pos_n)
            it["n_pos_sum"] = int(sum(pos_n))
            if pos_n:
                it["n_mean"] = round(float(np.mean(pos_n)), 2)
                it["n_min"] = int(min(pos_n))
                it["n_max"] = int(max(pos_n))
            else:
                it["n_mean"] = it["n_min"] = it["n_max"] = None
            it["m_pos"] = sum(1 for s in sell if s["m"] > 0)
            it["m_neg"] = sum(1 for s in sell if s["m"] < 0)
            it["sell_1y"] = sum(1 for s in sell if cut_1y and s["date"] >= cut_1y)
            # 明细按时间倒序，仅回传最近 show_records 条（统计仍基于全量）
            it["buy_history"] = list(reversed(buy))[:c["show_records"]]
            it["sell_history"] = list(reversed(sell))[:c["show_records"]]
        except Exception as e:  # noqa: BLE001 —— 单只失败不影响整体
            it["buy_history"] = it["sell_history"] = []
            it["buy_n"] = it["sell_n"] = it["sell_1y"] = 0
            it["n_pos"] = it["n_lag"] = it["m_pos"] = it["m_neg"] = 0
            it["n_pos_sum"] = 0
            it["n_mean"] = it["n_min"] = None
            it["bars_hist"] = 0
            it["note"] = f"异常:{type(e).__name__}"

    # ---- 排序：1st 平均 n（仅 n≥0 样本）升序 → 2nd 近一年卖出九转数降序 → 3rd 代码 ----
    INF = 9e9
    # 样本量收缩：n_rank = (Σn + K×全市场均值) / (m + K)，m = 该股 n≥0 样本数。
    # 只有 1 个 n=0 样本的股票会被拉回全市场均值附近，不再霸榜；
    # 样本多的股票几乎不受影响。K=0 时退回纯平均 n（字面口径）。
    tot_sum = sum(it.get("n_pos_sum") or 0 for it in cands)
    tot_cnt = sum(it.get("n_pos") or 0 for it in cands)
    prior = round(float(tot_sum) / tot_cnt, 4) if tot_cnt else 0.0
    K = float(c["shrink_k"])
    for it in cands:
        m = it.get("n_pos") or 0
        if not m:
            it["n_rank"] = None                      # 没有任何 n≥0 样本 → 垫底
        elif K > 0:
            it["n_rank"] = round((float(it.get("n_pos_sum") or 0) + K * prior) / (m + K), 4)
        else:
            it["n_rank"] = it.get("n_mean")
    cands.sort(key=lambda r: (
        r.get("n_rank") if r.get("n_rank") is not None else INF,
        -(r.get("sell_1y") or 0),
        r["code"],
    ))
    for i, r in enumerate(cands, 1):
        r["rank"] = i
    # 状态文案
    for r in cands:
        if r.get("triggered"):
            r["status"] = "已达成买入九转（第9天）"
        else:
            r["status"] = f"即将买入九转 · 还差{r.get('remain', 0)}天（当前{r.get('cur_count', 0)}/9）"
    results = cands[:c["max_results"]]
    # 全市场口径的滞后统计（供前端汇总条展示「信号及时性」的全貌）
    tot_n = sum((r.get("n_pos") or 0) + (r.get("n_lag") or 0) for r in cands)
    tot_lag = sum(r.get("n_lag") or 0 for r in cands)
    tot_s = sum(r.get("sell_n") or 0 for r in cands)
    tot_mneg = sum(r.get("m_neg") or 0 for r in cands)
    return {
        "results": results,
        "skip_stats": skip_stats,
        "ref_date": ref_date,
        "cut_1y": cut_1y,
        "stats": {
            "buy_total": tot_n,
            "buy_lag": tot_lag,          # 真底在信号之前（信号滞后）的买入样本数
            "buy_lag_pct": round(100.0 * tot_lag / tot_n, 1) if tot_n else None,
            "n_prior": prior,            # 全市场 n≥0 样本的加权均值（收缩目标）
            "shrink_k": K,               # 收缩强度（0 = 关闭，用纯平均 n 排序）
            "thin": sum(1 for r in cands if (r.get("n_pos") or 0) == 1),  # 只有 1 个样本的只数
            "sell_total": tot_s,
            "sell_top_after": tot_mneg,  # 真顶在信号之后（信号属提前预警）的卖出样本数
            "sell_top_after_pct": round(100.0 * tot_mneg / tot_s, 1) if tot_s else None,
        },
        "params": {
            "near_remaining": c["near_remaining"], "ref_gap": c["ref_gap"],
            "match_window": c["match_window"], "confirm_pct": c["confirm_pct"],
            "confirm_bars": c["confirm_bars"], "hist_years": c["hist_years"],
            "include_triggered": bool(c["include_triggered"]),
            "exclude_st": bool(c["exclude_st"]),
            "shrink_k": c["shrink_k"],
            "min_price": c["min_price"], "max_price": c["max_price"],
            "min_amt20_yi": c["min_amt20_yi"], "min_listed_days": c["min_listed_days"],
        },
        "candidates": len(cands),
        "elapsed": round(time.time() - t0, 2),
    }


def meta_info() -> dict:
    """默认参数与说明（前端渲染用）。"""
    return {
        "defaults": dict(DEFAULTS),
        "doc": list(DOC_LINES),
        "main_len": MAIN_LEN,
        "recent_window": RECENT_WINDOW,
    }
