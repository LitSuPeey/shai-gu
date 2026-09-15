# -*- coding: utf-8 -*-
"""蚂蚁呀欸 · 底部形态四层扫描（适配统一 daily + ant_index 表）。"""
import concurrent.futures
import math
import os
import threading
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..core import db

# ---- 配置（全部可调） ----
ANT_CONFIG = {
    "scan": {"lookback": 300, "retries": 2, "retry_interval": 1.0,
             "max_workers": 8, "calendar_days": 480},
    "hard_filter": {"remove_st": True, "min_list_days": 250,
                   "liquidity_quantile": 0.30},
    "pattern": {"amplitude_range": [0.20, 0.45], "p_threshold": 0.05,
                "close_pos_range": [0.25, 0.70],
                "second_half_vol_ratio_max": 0.85, "recent10_vol_ratio_max": 0.75,
                "up_down_vol_ratio_min": 1.15,
                "min_up_days": 3, "min_down_days": 3},
    "ranking": {"bottom_raise_segments": 3, "min_bottom_rise_pct": 0.02,
                "support_touch_min": 2, "support_rebound_pct": 5.0,
                "support_trigger_zone": 1.05, "support_min_gap": 10,
                "support_observe_days": 5,
                "ma_convergence_pct": 3.0, "ma_convergence_ratio": 0.6,
                "excess_return_min": 0.0, "downside_resistance_ratio": 0.85,
                "prior_drop_min": 0.10},
    "chip_filter": {"enable": False, "only_keep_up": False,
                    "lookback_days": 90, "oscillation_amp_max": 0.25,
                    "oscillation_p": 0.05, "min_gap": 10,
                    "bottom_zone_pct": 5},
    "breakout": {"close_above_range_ratio": 0.97, "volume_ratio": 1.5,
                 "ma_gap_min_pct": 2.0, "ma_gap_recent_lookback": 20,
                 "ma_gap_expand_ratio": 1.5, "ma_gap_compression_pct": 1.0},
}

SCORE_COLS = ["底部抬高得分", "支撑有效性得分", "均线粘合得分",
              "均线收敛得分", "区间超额收益得分", "抗跌性得分", "前期跌幅得分"]
CHIP_COLS = ["前底部获利比例", "最近底部获利比例", "筹码变化方向", "chip_source"]
STATUS_ORDER = ["接近启动", "蓄势中", "已启动", "—"]
OUTPUT_COLS = ["代码", "名称", "板块", "总分（0-7）", "有效项数", "60日振幅"]
OUTPUT_COLS += SCORE_COLS + ["筹码得分"] + CHIP_COLS
OUTPUT_COLS += ["启动状态", "PriorReturn", "DownsideCapture",
                "MA_Gap", "BreakoutDistance",
                "区间超额收益", "60日平均成交额(万)",
                "filter_pass", "failed_conditions",
                "data_quality", "scan_date", "data_source"]


# ---- t 分布 p 值（自实现，无 scipy） ----
def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3e-7, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN: d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN: d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN: c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (a + m2 + qab - a - m))
        d = 1.0 + aa * d
        if abs(d) < FPMIN: d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN: c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betai(a, b, x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    from math import exp, lgamma, log, log1p
    bt = exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_cdf(t, df):
    x = df / (df + t * t)
    ib = _betai(df / 2.0, 0.5, x)
    return 1.0 - 0.5 * ib if t > 0 else 0.5 * ib


def _regression_p(y):
    y = np.asarray(y, dtype=float)
    n = len(y)
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    if sxx <= 0 or n <= 2:
        return 0.0, 1.0
    slope = float(((x - xm) * (y - ym)).sum() / sxx)
    intercept = ym - slope * xm
    resid = y - (intercept + slope * x)
    s2 = float((resid ** 2).sum()) / (n - 2)
    se = float(np.sqrt(s2 / sxx))
    t = slope / se
    p = 2.0 * (1.0 - _t_cdf(abs(t), n - 2))
    return slope, float(p)


# ---- 加载数据 ----
def _load_daily(code: str, lookback: int) -> pd.DataFrame:
    rconn = db.reader()
    df = pd.read_sql_query(
        f"SELECT date, open, high, low, close, volume, amount, "
        f"outstanding_share, turnover FROM daily WHERE code=? "
        f"ORDER BY date DESC LIMIT {int(lookback)}",
        rconn, params=(str(code),))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.iloc[::-1].reset_index(drop=True)


def _load_index() -> Optional[pd.DataFrame]:
    rconn = db.reader()
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM ant_index "
        "ORDER BY date", rconn)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.reset_index(drop=True)


# ---- 进程池支持（绕过 GIL）----
# 常驻池（app/core/pool.py）统一 initializer 只设 db_path，指数在此惰性加载：
# worker 进程首次用到时加载一次，之后常驻复用（_UNSET 区分「未加载」与「加载失败=None」）。
_INDEX_UNSET = object()
_INDEX_DF: Optional[pd.DataFrame] = _INDEX_UNSET


def _worker_index() -> Optional[pd.DataFrame]:
    global _INDEX_DF
    if _INDEX_DF is _INDEX_UNSET:
        try:
            _INDEX_DF = _load_index()
        except Exception:
            _INDEX_DF = None
    return _INDEX_DF


def _ant_worker(code: str, name: str, sector: str, cfg: dict,
                scan_date_s: str):
    """模块级四层扫描 worker（供 ProcessPool 使用，可 pickle）。"""
    sc, hf, pt, rk, bk = (cfg["scan"], cfg["hard_filter"],
                          cfg["pattern"], cfg["ranking"], cfg["breakout"])
    index_df = _worker_index()
    try:
        d = _load_daily(code, max(int(sc["lookback"]) + 20, 320))
    except Exception:
        return None
    if d is None or d.empty:
        return None
    base_row = {
        "代码": code, "名称": name,
        "板块": sector,
        "总分（0-7）": np.nan, "有效项数": np.nan,
        "60日振幅": np.nan, "启动状态": "",
        "PriorReturn": np.nan, "DownsideCapture": np.nan,
        "MA_Gap": np.nan, "BreakoutDistance": np.nan,
        "区间超额收益": np.nan, "60日平均成交额(万)": np.nan,
        "filter_pass": False, "failed_conditions": "",
        "data_quality": "OK", "scan_date": scan_date_s,
        "data_source": "AKShare",
    }
    for c in SCORE_COLS:
        base_row[c] = np.nan
    if d[["open", "high", "low", "close", "volume"]].isna().any().any():
        base_row["data_quality"] = "缺失值"
    if (d["close"] <= 0).any() or (d["volume"] <= 0).any():
        base_row["data_quality"] += ";价格/量异常"
    if len(d) < 40:
        base_row["failed_conditions"] = "样本不足"
        base_row["data_quality"] = "SKIP:" + base_row["data_quality"]
        return base_row
    fails = []
    if len(d) < int(hf["min_list_days"]):
        fails.append(f"上市交易不足({len(d)}日)")
    amt = d["amount"].tail(60)
    amount60 = float(amt.mean()) if len(amt) else np.nan
    base_row["60日平均成交额(万)"] = (
        amount60 / 1e4) if pd.notna(amount60) else np.nan
    if fails:
        base_row["failed_conditions"] = ";".join(fails)
        return base_row

    w = d.tail(60).reset_index(drop=True)
    amp = float((w["high"].max() - w["low"].min()) / w["close"].mean())
    base_row["60日振幅"] = amp
    _, p_val = _regression_p(np.log(w["close"].to_numpy(dtype=float)))
    close_pos = float((w["close"].iloc[-1] - w["low"].min())
                      / (w["high"].max() - w["low"].min()))
    v1 = float(w["volume"].iloc[30:60].mean()
               / w["volume"].iloc[0:30].mean())
    v2 = float(w["volume"].iloc[51:60].mean() / w["volume"].mean())
    chg = w["close"].pct_change()
    up_days = chg > 0
    dn_days = chg < 0
    n_up, n_dn = int(up_days.sum()), int(dn_days.sum())
    up_vol = float(w["volume"][up_days.values].mean()) if n_up else np.nan
    dn_vol = float(w["volume"][dn_days.values].mean()) if n_dn else np.nan
    vol_ratio = (up_vol / dn_vol) if pd.notna(up_vol) and pd.notna(
        dn_vol) and dn_vol > 0 else np.nan
    a_lo, a_hi = pt["amplitude_range"]
    p_lo, p_hi = pt["close_pos_range"]
    if not (a_lo <= amp <= a_hi): fails.append("振幅不符")
    if not (p_val > pt["p_threshold"]): fails.append("线性趋势显著")
    if not (p_lo <= close_pos <= p_hi): fails.append("收盘位置不符")
    if not (v1 < pt["second_half_vol_ratio_max"]): fails.append("后半程量能未收缩")
    if not (v2 < pt["recent10_vol_ratio_max"]): fails.append("近期缩量不足")
    if n_up < pt["min_up_days"]: fails.append("上涨日不足")
    if n_dn < pt["min_down_days"]: fails.append("下跌日不足")
    if pd.isna(vol_ratio) or not (vol_ratio > pt["up_down_vol_ratio_min"]):
        fails.append("涨跌量能比不足")
    if fails:
        base_row["failed_conditions"] = ";".join(fails)
        return base_row

    scores = {}
    segs3 = [w.iloc[0:20], w.iloc[20:40], w.iloc[40:60]]
    lows = [float(s["low"].min()) for s in segs3]
    raise_pct = float(rk["min_bottom_rise_pct"])
    scores["底部抬高得分"] = 1 if (
        lows[1] >= lows[0] * (1 + raise_pct)
        and lows[2] >= lows[1] * (1 + raise_pct)) else 0
    scores["支撑有效性得分"] = (1 if _support_successes(w, rk)
                                >= int(rk["support_touch_min"]) else 0)
    ma5 = w["close"].rolling(5).mean()
    ma10 = w["close"].rolling(10).mean()
    ma20 = w["close"].rolling(20).mean()
    c_t = float(w["close"].iloc[-1])
    mc = float(rk["ma_convergence_pct"])
    scores["均线粘合得分"] = 1 if (
        abs(float(ma5.iloc[-1]) - float(ma10.iloc[-1])) / c_t * 100 < mc
        and abs(float(ma5.iloc[-1]) - float(ma20.iloc[-1])) / c_t * 100 < mc
        and abs(float(ma10.iloc[-1]) - float(ma20.iloc[-1])) / c_t * 100 < mc) else 0
    gap = (ma5 - ma20).abs() / w["close"]
    gap_t = float(gap.iloc[-1]) * 100.0
    gap_20 = float(gap.iloc[-21]) * 100.0 if len(gap) >= 21 else np.nan
    base_row["MA_Gap"] = gap_t
    if pd.isna(gap_20) or gap_20 <= 0:
        scores["均线收敛得分"] = 0
    else:
        scores["均线收敛得分"] = 1 if gap_t / gap_20 < float(
            rk["ma_convergence_ratio"]) else 0
    excess = np.nan
    if index_df is not None and len(index_df) >= 61:
        d0, d1 = index_df["date"].iloc[-61], index_df["date"].iloc[-1]
        st0 = d[d["date"] <= d0]
        st1 = d[d["date"] <= d1]
        if len(st0) and len(st1):
            ret_s = float(st1["close"].iloc[-1] / st0["close"].iloc[-1] - 1.0)
            ret_i = float(index_df["close"].iloc[-1]
                          / index_df["close"].iloc[-61] - 1.0)
            excess = ret_s - ret_i
    base_row["区间超额收益"] = excess
    scores["区间超额收益得分"] = (1 if pd.notna(excess)
                                  and excess > float(rk["excess_return_min"])
                                  else np.nan)
    down_cap = np.nan
    if index_df is not None and len(index_df) >= 61:
        seg_i = index_df.iloc[-61:]
        iret = seg_i["close"].pct_change().dropna()
        dn = iret[iret < 0]
        if len(dn):
            srets = []
            for dt_ in dn.index:
                d_dt = seg_i.loc[dt_, "date"]
                prev_d = d[d["date"] < d_dt]
                cur_d = d[d["date"] <= d_dt]
                if len(prev_d) and len(cur_d):
                    srets.append(float(cur_d["close"].iloc[-1]
                                       / prev_d["close"].iloc[-1] - 1.0))
            if srets:
                down_cap = abs(float(np.mean(srets))) / abs(float(dn.mean()))
    base_row["DownsideCapture"] = down_cap
    scores["抗跌性得分"] = (1 if pd.notna(down_cap)
                            and down_cap <= float(rk["downside_resistance_ratio"])
                            else np.nan)
    prior = np.nan
    if len(d) >= 122:
        prior = float(d["close"].iloc[-62] / d["close"].iloc[-122] - 1.0)
    base_row["PriorReturn"] = prior
    scores["前期跌幅得分"] = (1 if pd.notna(prior)
                              and prior <= -float(rk["prior_drop_min"])
                              else np.nan)
    items = [(k, v) for k, v in scores.items() if pd.notna(v)]
    effective = len(items)
    score_sum = sum(int(v) for _, v in items)
    base_row["有效项数"] = effective
    if effective == 0:
        base_row["failed_conditions"] = "无有效评分项"
        return base_row
    # 分母固定为 7（SCORE_COLS 项数），缺失项按「未通过=0」计入。
    # 原实现用 score_sum/effective*7 把缺失项剔出分母 → 数据更少（缺指数/上市不足
    # 122 日 → 区间超额/抗跌/前期跌幅为 NaN）的股票反被抬高：例如 7 项过 5 项得 5.0，
    # 而只有 4 项可算、全过则得 4/4*7=7.0 —— 通过项更少却排更前，跨股票不可比。
    # 分母固定后，7 项齐全的股票得分与原来完全一致（sum/7*7 == sum）。
    base_row["总分（0-7）"] = round(float(score_sum), 3)
    base_row["filter_pass"] = True
    base_row["failed_conditions"] = ""
    for k in SCORE_COLS:
        base_row[k] = scores.get(k, np.nan)
    return base_row


# ---- 第二层: 支撑事件 ----
def _support_successes(bars, rk):
    support = float(bars["low"].min())
    low = bars["low"].to_numpy()
    close = bars["close"].to_numpy()
    n = len(bars)
    zone = float(rk["support_trigger_zone"])
    obs = int(rk["support_observe_days"])
    gap = int(rk["support_min_gap"])
    rebound = float(rk["support_rebound_pct"]) / 100.0
    successes = 0
    last_end = -10 ** 9
    i = 0
    while i < n:
        if low[i] <= support * zone and (i - last_end) >= gap:
            if i + obs >= n:
                i += 1; continue
            base = float(low[i])
            if float(close[i + 1:i + obs + 1].max()) / base - 1.0 >= rebound:
                successes += 1
            last_end = i + obs
            i = last_end + 1
        else:
            i += 1
    return successes


# ---- 第四层: 启动状态 ----
# ---- 第三层：可选筹码底部改善（AKShare stock_cyq_em 优先，换手衰减法兜底） ----
def _est_profit_ratio(seg):
    """换手衰减法估算截至 seg 最后一日的收盘获利筹码比例（0~1）。"""
    if seg is None or seg.empty or "turnover" not in seg.columns:
        return np.nan
    cost = ((seg["high"] + seg["low"] + seg["close"]) / 3.0).to_numpy(dtype=float)
    vol = seg["volume"].to_numpy(dtype=float)
    to = seg["turnover"].clip(lower=0).to_numpy(dtype=float) / 100.0
    cum = to[::-1].cumsum()[::-1] - to  # 自该日起的累计换手
    w = vol * np.exp(-cum)
    wsum = float(w.sum())
    if wsum <= 0:
        return np.nan
    ref = float(seg["close"].iloc[-1])
    return float(w[cost <= ref].sum() / wsum)


def _ant_call(name, fn, cfg):
    """带重试的 AKShare 调用：默认重试2次、每次间隔1秒（可配置）。"""
    import time as _time
    retries = int(cfg["scan"].get("retries", 2))
    interval = float(cfg["scan"].get("retry_interval", 1.0))
    last = None
    for i in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < retries:
                _time.sleep(interval)
    raise RuntimeError(f"{name}: {type(last).__name__}: {last}")


def _chip_result(code, daily, cfg):
    """寻找最近 lookback_days 内两个震荡周期，比较底部获利盘均值。返回 dict。"""
    import akshare as ak  # 仅在筹码开关开启时才会走到这里（主进程）
    cf = cfg["chip_filter"]
    lookback = int(cf["lookback_days"])
    seg = daily.tail(lookback)
    n = len(seg)
    if n < 60:
        return {"ok": False, "reason": "样本不足"}
    amp_max = float(cf["oscillation_amp_max"])
    p_th = float(cf["oscillation_p"])
    min_gap = int(cf["min_gap"])
    zone_pct = float(cf["bottom_zone_pct"]) / 100.0
    L = 30  # 周期窗口长度（实现约定：30个交易日）
    valid = []
    for s in range(0, n - L + 1):
        win = seg.iloc[s:s + L]
        amp = (win["high"].max() - win["low"].min()) / win["close"].mean()
        if amp > amp_max:
            continue
        _, p = _regression_p(np.log(win["close"].to_numpy(dtype=float)))
        if p <= p_th:
            continue
        valid.append(s)
    # 从最近往前取两个、满足间隔 ≥ min_gap
    picks = []
    prev_start = None
    for s in reversed(valid):
        if prev_start is None:
            picks.append(s)
            prev_start = s
        elif (prev_start - (s + L)) >= min_gap:
            picks.append(s)
            prev_start = s
        if len(picks) == 2:
            break
    if len(picks) < 2:
        return {"ok": False, "reason": "震荡周期不足"}
    s_recent, s_prior = picks[0], picks[1]  # picks[0] 更近
    periods = [seg.iloc[s_prior:s_prior + L], seg.iloc[s_recent:s_recent + L]]

    # 数据来源：优先 stock_cyq_em（仅近90日），失败/不足则换手衰减法估算
    cyq = None
    source = "estimated"
    try:
        df_cyq = _ant_call(f"筹码分布({code})",
                           lambda: ak.stock_cyq_em(symbol=code, adjust="qfq"),
                           cfg)
        if df_cyq is not None and not df_cyq.empty:
            dcol = next((c for c in df_cyq.columns if "日期" in str(c)), None)
            pcol = next((c for c in df_cyq.columns if "获利" in str(c)), None)
            if dcol and pcol:
                cyq = pd.DataFrame({
                    "date": pd.to_datetime(df_cyq[dcol], errors="coerce"),
                    "profit": pd.to_numeric(df_cyq[pcol], errors="coerce"),
                }).dropna()
                if len(cyq) < 60:
                    cyq = None
    except Exception:
        cyq = None
    if cyq is not None:
        source = "AKShare"

    vals = []
    for pwin in periods:
        min_low = float(pwin["low"].min())
        zone = pwin[pwin["low"] <= min_low * (1.0 + zone_pct)]
        if zone.empty:
            vals.append(np.nan)
            continue
        if cyq is not None:
            merged = zone.merge(cyq, on="date", how="left")
            vals.append(float(merged["profit"].mean()))
        else:
            full = daily[daily["date"] <= pwin["date"].iloc[-1]].tail(120)
            prs = []
            for _, zr in zone.iterrows():
                s = full[full["date"] <= zr["date"]].tail(120)
                prs.append(_est_profit_ratio(s))
            vals.append(float(np.nanmean(prs)) if prs else np.nan)
    prior_v, recent_v = vals[0], vals[1]
    if pd.isna(prior_v) or pd.isna(recent_v):
        return {"ok": False, "reason": "获利比例缺失"}
    direction = "上升" if recent_v > prior_v else "下降"
    return {"ok": True, "prior": prior_v, "recent": recent_v,
            "direction": direction, "source": source}


def _apply_chip_scores(records, daily_loader, cfg, limit=50, progress_cb=None):
    """主进程对 filter_pass 前 limit 只补算可选筹码项并重算总分。

    筹码需要联网调 AKShare，故不放子进程（避免 spawn 环境加载 V8 风险），
    只对现有 7 项得分排序后的前 limit 只计算，控制网络耗时。
    """
    if not cfg["chip_filter"].get("enable"):
        return records
    passed = [r for r in records if r.get("filter_pass")]
    passed.sort(key=lambda r: (-float(r.get("总分（0-7）") or 0),
                               float(r.get("BreakoutDistance") or -9e9)))
    targets = passed[:limit]
    for i, r in enumerate(targets):
        try:
            d = daily_loader(str(r["代码"]),
                             max(int(cfg["scan"]["lookback"]) + 20, 320))
            chip = _chip_result(str(r["代码"]), d, cfg) if d is not None and len(d) else {"ok": False}
        except Exception:
            chip = {"ok": False}
        if chip.get("ok"):
            r["筹码得分"] = 1 if chip["direction"] == "上升" else 0
            r["前底部获利比例"] = chip.get("prior")
            r["最近底部获利比例"] = chip.get("recent")
            r["筹码变化方向"] = chip.get("direction")
            r["chip_source"] = chip.get("source")
        else:
            r["筹码得分"] = 0
            r["筹码变化方向"] = "无"
            r["chip_source"] = "N/A"
        # 说明：这里**不再重算** 总分（0-7）。
        # 原实现把「筹码得分」并入分母变成 8 项再 ×7/8 —— 但筹码只对排序后
        # 前 limit 只计算，其余股票的 总分 仍是 7 项口径，导致「同一列混两套公式」，
        # 且排序用 7 项、显示用 8 项，表格里 总分 与名次不自洽。
        # 现约定：总分（0-7）恒为 SCORE_COLS 七项合计，筹码作为独立维度单列展示
        # （筹码是否达标仍由 chip_filter.only_keep_up 过滤），显示值与排序一致。
        if progress_cb:
            progress_cb(i + 1, len(targets), f"筹码分析 {i+1}/{len(targets)}")
    # only_keep_up：剔除筹码未改善的
    if cfg["chip_filter"].get("only_keep_up"):
        for r in targets:
            if r.get("筹码变化方向") not in (None, "上升") and r.get("filter_pass"):
                r["filter_pass"] = False
                r["failed_conditions"] = "筹码未改善(only_keep_up)"
    return records


def _breakout_status(bars, bk):
    close = bars["close"].to_numpy(dtype=float)
    high = bars["high"].to_numpy(dtype=float)
    vol = bars["volume"].to_numpy(dtype=float)
    ref_high = float(high[-60:-1].max())
    bd = float(close[-1] / ref_high - 1.0)
    vol_t = float(vol[-1])
    baseline = float(vol[-20:-1].mean())
    ratio = float(bk["volume_ratio"])
    ma5 = float(close[-5:].mean())
    ma10 = float(close[-10:].mean())
    ma20 = float(close[-20:].mean())
    gap_now = float(abs(ma5 - ma20) / close[-1] * 100.0)
    gap_prev = float(abs(close[-6:-1].mean() - close[-21:-1].mean())
                     / close[-2] * 100.0) if len(close) >= 22 else 0.0
    if bd > 0 and vol_t > baseline * ratio:
        return "已启动", bd, gap_now
    lb = int(bk["ma_gap_recent_lookback"])
    comp_pct = float(bk["ma_gap_compression_pct"])
    expand_ratio = float(bk["ma_gap_expand_ratio"])
    min_pct = float(bk["ma_gap_min_pct"])
    hist_gaps = []
    for k in range(1, lb + 1):
        if len(close) < 21 + k: break
        m5 = close[-k - 5:-k].mean() if k + 5 <= len(close) else np.nan
        m20 = close[-k - 20:-k].mean() if k + 20 <= len(close) else np.nan
        ck = close[-k - 1]
        if ck > 0 and not np.isnan(m5) and not np.isnan(m20):
            hist_gaps.append(abs(m5 - m20) / ck * 100.0)
    cond2 = False
    if len(hist_gaps) >= 1:
        min_gap_hist = float(min(hist_gaps))
        cond2 = (ma5 > ma10 > ma20 and min_gap_hist <= comp_pct
                 and gap_now > min_gap_hist * expand_ratio
                 and gap_now > gap_prev and gap_now > min_pct)
    if cond2:
        return "已启动", bd, gap_now
    near = False
    if (bd >= float(bk["close_above_range_ratio"]) - 1.0) and bd <= 0 \
            and not (vol_t > baseline * ratio):
        near = True
    if (ma5 > ma10) or (ma10 > ma20):
        near = True
    if near:
        return "接近启动", bd, gap_now
    return "蓄势中", bd, gap_now


def _merge_cfg(cfg):
    out = {}
    for k, v in ANT_CONFIG.items():
        sub = dict(cfg.get(k) or {}) if isinstance(cfg, dict) else {}
        out[k] = {**v, **sub}
    return out


def sort_ranked(ranked):
    ranked = ranked.copy()
    ranked["_srank"] = ranked["启动状态"].map(
        {s: i for i, s in enumerate(STATUS_ORDER)}
    ).fillna(len(STATUS_ORDER))
    ranked["_amt"] = pd.to_numeric(
        ranked.get("60日平均成交额(万)"), errors="coerce").fillna(0)
    ranked = ranked.sort_values(
        ["_srank", "总分（0-7）", "BreakoutDistance", "MA_Gap",
         "区间超额收益", "_amt"],
        ascending=[True, False, False, False, False, False], na_position="last")
    return ranked.drop(columns=["_srank", "_amt"]).reset_index(drop=True)


def scan(cfg: dict = None, exchange: Optional[str] = None,
         sectors: Optional[list] = None, only_pass: bool = True,
         progress_cb=None, logs: list = None) -> pd.DataFrame:
    """执行四层扫描。"""
    cfg = _merge_cfg(cfg or {})
    sc, hf, pt, rk, bk = (cfg["scan"], cfg["hard_filter"],
                            cfg["pattern"], cfg["ranking"], cfg["breakout"])
    logs = [] if logs is None else logs

    index_df = _load_index()
    ref_date = index_df["date"].iloc[-1].date() if index_df is not None else date.today()

    rconn = db.reader()
    meta = pd.read_sql_query(
        "SELECT code, name, sector, exchange FROM meta", rconn)
    if exchange in ("SZ", "SH", "BJ"):
        meta = meta[meta["exchange"] == exchange]
    if sectors:
        meta = meta[meta["sector"].isin(set(sectors))]
    if hf.get("remove_st", True):
        meta = meta[~meta["name"].astype(str).str.upper().str.contains("ST|退", na=False)]
    codes = meta["code"].astype(str).tolist()
    total = len(codes)
    if total == 0:
        return pd.DataFrame()
    if progress_cb:
        progress_cb(0, total, f"蚂蚁扫描：{total} 只")

    scan_date_s = date.today().strftime("%Y-%m-%d")
    records = []
    name_map = dict(zip(meta["code"].astype(str), meta["name"]))
    sector_map = dict(zip(meta["code"].astype(str), meta["sector"]))


    from ..core import pool as _ppool
    tasks = [(c, name_map.get(c, c), sector_map.get(c, "未分类"), cfg, scan_date_s)
             for c in codes]
    _workers = max(1, min(int(sc.get("max_workers", 8)), os.cpu_count() or 4))
    ex = _ppool.get_pool(_workers, db.db_path())
    try:
        done = 0
        for r in ex.map(_ant_worker, [t[0] for t in tasks],
                        [t[1] for t in tasks], [t[2] for t in tasks],
                        [t[3] for t in tasks], [t[4] for t in tasks],
                        chunksize=32):
            done += 1
            if progress_cb and (done % 50 == 0 or done == total):
                progress_cb(done, total, f"蚂蚁四层扫描 {done}/{total}")
            if r is not None:
                records.append(r)
    except BaseException:
        # 取消/异常时丢弃常驻池（取消未开始的任务，不等跑完），下次扫描重建
        _ppool.discard_pool()
        raise
    # 可选筹码第 8 项：主进程对前 50 只补算（联网，需 chip_filter.enable）
    if cfg["chip_filter"].get("enable"):
        records = _apply_chip_scores(
            records, _load_daily, cfg, limit=50, progress_cb=progress_cb)
        if progress_cb:
            progress_cb(total, total, "蚂蚁四层扫描（含筹码）完成")
    out = pd.DataFrame(records)
    if out.empty:
        return out
    ranked = out[out["filter_pass"]].copy()
    failed = out[~out["filter_pass"]].sort_values("代码").reset_index(drop=True)
    if not ranked.empty:
        ranked["_amt"] = ranked["60日平均成交额(万)"].fillna(0)
        ranked = ranked.sort_values(
            ["总分（0-7）", "BreakoutDistance", "MA_Gap",
             "区间超额收益", "_amt"],
            ascending=[False, False, False, False, False], na_position="last")
        ranked = ranked.drop(columns="_amt")
        top30 = ranked.head(30)
        status_map = {}
        for i, r in top30.iterrows():
            try:
                d = _load_daily(str(r["代码"]), 70)
                if d is not None and len(d) >= 60:
                    w = d.tail(60).reset_index(drop=True)
                    st, bd, mg = _breakout_status(w, bk)
                    status_map[str(r["代码"])] = (st, bd, mg)
            except Exception:
                continue
        for i, r in ranked.iterrows():
            if str(r["代码"]) in status_map:
                ranked.at[i, "启动状态"] = status_map[str(r["代码"])][0]
                ranked.at[i, "BreakoutDistance"] = status_map[str(r["代码"])][1]
                ranked.at[i, "MA_Gap"] = status_map[str(r["代码"])][2]
            else:
                ranked.at[i, "启动状态"] = "—"
        ranked = sort_ranked(ranked)
        out = ranked if only_pass else pd.concat([ranked, failed], ignore_index=True)
    else:
        out = ranked if only_pass else failed
    if not out.empty:
        out["抗跌性得分"] = out["抗跌性得分"].apply(
            lambda v: "NA" if pd.isna(v) else int(v))
    cols = [c for c in OUTPUT_COLS if c in out.columns]
    return out[cols]