# -*- coding: utf-8 -*-
"""形态打分（来自 scrensto pattern_core，适配本项目统一数据库）。"""
import math
import os
import threading
from typing import Optional

import numpy as np
import pandas as pd

from ..core import db

# ---- 阈值常量 ----
MIN_ROWS = 120
UPTREND_LOOKBACK = 250
SWING_K = 5

FILT_RSI_MIN, FILT_RSI_MAX = 15.0, 85.0
FILT_BIAS20_MIN, FILT_BIAS20_MAX = -15.0, 20.0
FILT_VOL_RATIO5_MAX = 4.0
FILT_GAIN60_MAX = 80.0
FILT_LOSS60_MAX = -30.0
FILT_ATR_PCT_MAX = 12.0
RECENT_WINDOW = 60

BOTTOM_ZONE_RSI_MAX = 56.3
BOTTOM_ZONE_BIAS20_MAX = 5.0

FEAT_WEIGHTS = {"rsi": 0.25, "bias20": 0.25,
                "vol_ratio5": 0.20, "shadow": 0.15, "amplitude": 0.15}
SIM_WEIGHT_TOTAL = 0.55
# σ 两轮收紧（2.0 → 1.4 → 0.75）：实测 1.4 时底部组相似分仍普遍 80+（33% 股票 ≥75 强匹配）；
# 0.75 后 ≥75 降至约 5%、均值 59.6 → 44.5，分布呈金字塔形，真正贴近历史样本者仍可上 90
SIM_SIGMA = 0.75
TOP_SCORE_DISCOUNT = 0.5

# 结构分梯度化（原布尔满分制导致普遍高分、区分度差）：
#   趋势 12（布尔事实）+ 回调带 5~10（带内按位置）+ 持续 1~5（越短越满）+ 涨跌比 2~10（越强越满）
#   满分 37 分，且多数股票不再接近满分
STRUCT_UPTREND_SCORE = 12.0
STRUCT_PULLBACK_BAND_SCORE = 10.0
STRUCT_DURATION_SCORE = 5.0
STRUCT_RISE_FALL_SCORE = 10.0
STRUCT_SCORE_MAX = 37.0
PULLBACK_BAND_MIN, PULLBACK_BAND_MAX = -18.0, -3.0
PULLBACK_MAX_DAYS = 15
RISE_FALL_RATIO_MIN = 1.2

GRADE_STRONG, GRADE_MEDIUM, GRADE_WEAK = 75.0, 60.0, 45.0

BOTTOM_STAT = {
    "rsi":         {"median": 48.52, "std": 7.43, "min": 25.56, "max": 56.2},
    "bias20":      {"median": -1.65, "std": 3.49, "min": -7.71, "max": 5.63},
    "bias60":      {"median": 3.32,  "std": 7.64, "min": -16.58, "max": 12.73},
    "vol_ratio5":  {"median": 0.90,  "std": 0.28, "min": 0.68, "max": 1.7},
    "amplitude":   {"median": 3.63,  "std": 3.43, "min": 1.59, "max": 13.76},
    "upper_shadow": {"median": 0.12, "std": 0.13, "min": 0.0, "max": 0.4},
    "lower_shadow": {"median": 0.38, "std": 0.22, "min": 0.02, "max": 0.74},
    "atr_pct":     {"median": 3.28,  "std": 2.06, "min": 2.21, "max": 8.4},
    "move_pct":    {"median": -7.31, "std": 4.40, "min": -17.84, "max": -3.03},
}
TOP_STAT = {
    "rsi":         {"median": 63.08, "std": 6.91, "min": 46.67, "max": 73.67},
    "bias20":      {"median": 5.72,  "std": 3.94, "min": 0.65, "max": 13.26},
    "bias60":      {"median": 11.98, "std": 8.18, "min": -7.74, "max": 25.77},
    "vol_ratio5":  {"median": 1.27,  "std": 0.30, "min": 0.9, "max": 2.15},
    "amplitude":   {"median": 4.73,  "std": 3.05, "min": 1.8, "max": 12.31},
    "upper_shadow": {"median": 0.33, "std": 0.21, "min": 0.03, "max": 0.81},
    "lower_shadow": {"median": 0.15, "std": 0.08, "min": 0.0, "max": 0.36},
    "atr_pct":     {"median": 3.24,  "std": 1.88, "min": 2.31, "max": 7.93},
    "move_pct":    {"median": 9.55,  "std": 9.30, "min": 5.12, "max": 35.51},
}

BOTTOM_POOL = [
    {"stock": "中百集团", "code": "000759", "date": "2026-06-05", "rsi": 52.68, "bias20": 4.33,
     "vol_ratio5": 0.79, "amplitude": 13.13, "upper_shadow": 0.0, "lower_shadow": 0.059,
     "tags": ["缩量回踩", "强势回踩(未破MA20)"]},
    {"stock": "中百集团", "code": "000759", "date": "2026-06-29", "rsi": 55.49, "bias20": 5.63,
     "vol_ratio5": 0.84, "amplitude": 13.76, "upper_shadow": 0.0, "lower_shadow": 0.253,
     "tags": ["缩量回踩", "强势回踩(未破MA20)"]},
    {"stock": "中百集团", "code": "000759", "date": "2026-07-07", "rsi": 47.6, "bias20": -3.92,
     "vol_ratio5": 0.7, "amplitude": 5.1, "upper_shadow": 0.161, "lower_shadow": 0.226,
     "tags": ["缩量回踩"]},
    {"stock": "中百集团", "code": "000759", "date": "2026-07-27", "rsi": 53.65, "bias20": 2.37,
     "vol_ratio5": 0.79, "amplitude": 9.6, "upper_shadow": 0.143, "lower_shadow": 0.397,
     "tags": ["缩量回踩", "强势回踩(未破MA20)"]},
    {"stock": "浙农股份", "code": "002758", "date": "2026-06-10", "rsi": 25.56, "bias20": -7.71,
     "vol_ratio5": 1.22, "amplitude": 3.06, "upper_shadow": 0.043, "lower_shadow": 0.739,
     "tags": ["超卖深蹲", "负乖离过大", "下影探底"]},
    {"stock": "浙农股份", "code": "002758", "date": "2026-07-09", "rsi": 36.98, "bias20": -4.73,
     "vol_ratio5": 1.17, "amplitude": 3.43, "upper_shadow": 0.115, "lower_shadow": 0.154,
     "tags": ["温和回踩"]},
    {"stock": "浙农股份", "code": "002758", "date": "2026-07-22", "rsi": 40.76, "bias20": -3.05,
     "vol_ratio5": 1.25, "amplitude": 3.63, "upper_shadow": 0.0, "lower_shadow": 0.107,
     "tags": ["温和回踩"]},
    {"stock": "浙农股份", "code": "002758", "date": "2026-08-07", "rsi": 48.58, "bias20": -0.15,
     "vol_ratio5": 1.7, "amplitude": 2.8, "upper_shadow": 0.045, "lower_shadow": 0.5,
     "tags": ["下影探底"]},
    {"stock": "华工科技", "code": "000988", "date": "2025-06-19", "rsi": 53.82, "bias20": 0.89,
     "vol_ratio5": 0.9, "amplitude": 3.14, "upper_shadow": 0.388, "lower_shadow": 0.18,
     "tags": ["强势回踩(未破MA20)"]},
    {"stock": "华工科技", "code": "000988", "date": "2025-07-07", "rsi": 48.75, "bias20": -1.65,
     "vol_ratio5": 0.95, "amplitude": 2.78, "upper_shadow": 0.2, "lower_shadow": 0.376,
     "tags": ["温和回踩"]},
    {"stock": "华工科技", "code": "000988", "date": "2025-07-25", "rsi": 56.2, "bias20": 1.7,
     "vol_ratio5": 0.68, "amplitude": 1.59, "upper_shadow": 0.118, "lower_shadow": 0.579,
     "tags": ["缩量回踩", "下影探底", "强势回踩(未破MA20)"]},
    {"stock": "华工科技", "code": "000988", "date": "2025-08-07", "rsi": 55.25, "bias20": 1.02,
     "vol_ratio5": 1.35, "amplitude": 4.92, "upper_shadow": 0.108, "lower_shadow": 0.34,
     "tags": ["强势回踩(未破MA20)"]},
    {"stock": "宏景科技", "code": "301396", "date": "2024-12-25", "rsi": 43.2, "bias20": -5.81,
     "vol_ratio5": 0.77, "amplitude": 5.36, "upper_shadow": 0.214, "lower_shadow": 0.65,
     "tags": ["负乖离过大", "缩量回踩", "下影探底"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-07-17", "rsi": 44.39, "bias20": -2.25,
     "vol_ratio5": 1.46, "amplitude": 2.08, "upper_shadow": 0.15, "lower_shadow": 0.64,
     "tags": ["下影探底"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-08-05", "rsi": 48.52, "bias20": -0.72,
     "vol_ratio5": 0.75, "amplitude": 1.89, "upper_shadow": 0.397, "lower_shadow": 0.484,
     "tags": ["缩量回踩"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-09-04", "rsi": 47.33, "bias20": -2.14,
     "vol_ratio5": 0.9, "amplitude": 7.16, "upper_shadow": 0.026, "lower_shadow": 0.223,
     "tags": ["温和回踩"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-10-15", "rsi": 51.01, "bias20": -1.06,
     "vol_ratio5": 0.79, "amplitude": 4.49, "upper_shadow": 0.065, "lower_shadow": 0.553,
     "tags": ["缩量回踩", "下影探底"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-11-03", "rsi": 45.29, "bias20": -4.55,
     "vol_ratio5": 1.13, "amplitude": 4.18, "upper_shadow": 0.047, "lower_shadow": 0.646,
     "tags": ["下影探底"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-11-21", "rsi": 39.72, "bias20": -6.0,
     "vol_ratio5": 1.12, "amplitude": 3.54, "upper_shadow": 0.396, "lower_shadow": 0.017,
     "tags": ["负乖离过大"]},
]

TOP_POOL = [
    {"stock": "中百集团", "code": "000759", "date": "2026-05-29", "rsi": 63.24, "bias20": 12.88,
     "vol_ratio5": 1.52, "amplitude": 12.31, "upper_shadow": 0.31, "lower_shadow": 0.113,
     "tags": ["乖离过大", "放量冲高"]},
    {"stock": "中百集团", "code": "000759", "date": "2026-06-23", "rsi": 59.83, "bias20": 9.37,
     "vol_ratio5": 1.1, "amplitude": 9.63, "upper_shadow": 0.794, "lower_shadow": 0.048,
     "tags": ["上影滞涨"]},
    {"stock": "中百集团", "code": "000759", "date": "2026-07-02", "rsi": 61.5, "bias20": 11.94,
     "vol_ratio5": 1.37, "amplitude": 8.98, "upper_shadow": 0.172, "lower_shadow": 0.19,
     "tags": ["乖离过大"]},
    {"stock": "中百集团", "code": "000759", "date": "2026-07-20", "rsi": 61.34, "bias20": 11.82,
     "vol_ratio5": 1.27, "amplitude": 11.32, "upper_shadow": 0.316, "lower_shadow": 0.203,
     "tags": ["乖离过大"]},
    {"stock": "中百集团", "code": "000759", "date": "2026-07-29", "rsi": 58.42, "bias20": 7.13,
     "vol_ratio5": 1.32, "amplitude": 8.42, "upper_shadow": 0.414, "lower_shadow": 0.172,
     "tags": ["温和顶部"]},
    {"stock": "浙农股份", "code": "002758", "date": "2026-06-26", "rsi": 46.67, "bias20": 0.73,
     "vol_ratio5": 0.98, "amplitude": 3.91, "upper_shadow": 0.806, "lower_shadow": 0.194,
     "tags": ["上影滞涨"]},
    {"stock": "浙农股份", "code": "002758", "date": "2026-07-20", "rsi": 52.86, "bias20": 1.97,
     "vol_ratio5": 1.23, "amplitude": 3.16, "upper_shadow": 0.64, "lower_shadow": 0.36,
     "tags": ["上影滞涨"]},
    {"stock": "浙农股份", "code": "002758", "date": "2026-08-04", "rsi": 53.22, "bias20": 2.16,
     "vol_ratio5": 0.9, "amplitude": 1.87, "upper_shadow": 0.333, "lower_shadow": 0.067,
     "tags": ["缩量滞涨"]},
    {"stock": "浙农股份", "code": "002758", "date": "2026-08-18", "rsi": 64.3, "bias20": 5.69,
     "vol_ratio5": 2.15, "amplitude": 4.25, "upper_shadow": 0.029, "lower_shadow": 0.147,
     "tags": ["放量冲高"]},
    {"stock": "华工科技", "code": "000988", "date": "2025-06-16", "rsi": 66.75, "bias20": 5.37,
     "vol_ratio5": 1.18, "amplitude": 3.34, "upper_shadow": 0.416, "lower_shadow": 0.04,
     "tags": ["温和顶部"]},
    {"stock": "华工科技", "code": "000988", "date": "2025-06-30", "rsi": 69.05, "bias20": 5.05,
     "vol_ratio5": 0.98, "amplitude": 1.8, "upper_shadow": 0.542, "lower_shadow": 0.157,
     "tags": ["上影滞涨"]},
    {"stock": "华工科技", "code": "000988", "date": "2025-07-17", "rsi": 66.84, "bias20": 7.12,
     "vol_ratio5": 1.23, "amplitude": 6.13, "upper_shadow": 0.326, "lower_shadow": 0.038,
     "tags": ["温和顶部"]},
    {"stock": "华工科技", "code": "000988", "date": "2025-07-31", "rsi": 63.08, "bias20": 5.44,
     "vol_ratio5": 1.25, "amplitude": 4.22, "upper_shadow": 0.673, "lower_shadow": 0.152,
     "tags": ["上影滞涨"]},
    {"stock": "宏景科技", "code": "301396", "date": "2025-01-14", "rsi": 59.82, "bias20": 8.74,
     "vol_ratio5": 1.3, "amplitude": 9.67, "upper_shadow": 0.125, "lower_shadow": 0.195,
     "tags": ["温和顶部"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-07-04", "rsi": 64.29, "bias20": 5.72,
     "vol_ratio5": 1.63, "amplitude": 4.73, "upper_shadow": 0.41, "lower_shadow": 0.063,
     "tags": ["放量冲高"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-07-30", "rsi": 69.76, "bias20": 5.42,
     "vol_ratio5": 1.23, "amplitude": 3.91, "upper_shadow": 0.476, "lower_shadow": 0.136,
     "tags": ["温和顶部"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-08-25", "rsi": 73.67, "bias20": 11.07,
     "vol_ratio5": 1.39, "amplitude": 7.26, "upper_shadow": 0.298, "lower_shadow": 0.19,
     "tags": ["超买冲顶", "乖离过大"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-10-09", "rsi": 72.7, "bias20": 13.26,
     "vol_ratio5": 1.21, "amplitude": 4.62, "upper_shadow": 0.749, "lower_shadow": 0.201,
     "tags": ["超买冲顶", "乖离过大", "上影滞涨"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-10-27", "rsi": 58.18, "bias20": 0.65,
     "vol_ratio5": 1.47, "amplitude": 3.58, "upper_shadow": 0.294, "lower_shadow": 0.247,
     "tags": ["温和顶部"]},
    {"stock": "北方华创", "code": "002371", "date": "2025-11-18", "rsi": 56.39, "bias20": 2.75,
     "vol_ratio5": 2.06, "amplitude": 9.1, "upper_shadow": 0.319, "lower_shadow": 0.036,
     "tags": ["放量冲高"]},
    {"stock": "北方华创", "code": "002371", "date": "2026-01-07", "rsi": 73.25, "bias20": 11.35,
     "vol_ratio5": 1.4, "amplitude": 6.38, "upper_shadow": 0.276, "lower_shadow": 0.0,
     "tags": ["超买冲顶", "乖离过大"]},
]

TIER_FULL_NAMES = {"A": "A强趋势浅回踩", "B": "B高波动剧烈回踩",
                   "C": "C温和深蹲型", "-": "-未入档"}
TIER_SHORT_NAMES = {"A": "A强趋势", "B": "B高波动", "C": "C温和", "-": "未入档"}
TIER_A_PULLBACK, TIER_A_RSI, TIER_A_BIAS20_MIN = (-6.8, -3.6), (44.0, 56.0), -2.0
TIER_B_PULLBACK, TIER_B_RSI = (-18.5, -13.5), (45.0, 56.0)
TIER_C_RSI_MAX, TIER_C_BIAS20_MAX = 48.6, -3.0


# ---- 指标 ----
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    for n in (5, 10, 20, 60):
        df[f"ma{n}"] = c.rolling(n).mean()
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    df["dif"], df["dea"], df["macd"] = dif, dea, (dif - dea) * 2
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/14, adjust=False).mean()
    al = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)
    df["vol_ma5"] = v.rolling(5).mean()
    df["vol_ma20"] = v.rolling(20).mean()
    df["vol_ratio5"] = v / df["vol_ma5"].replace(0, np.nan)
    df["bias20"] = (c - df["ma20"]) / df["ma20"] * 100
    df["bias60"] = (c - df["ma60"]) / df["ma60"] * 100
    tr = pd.concat([h - l, (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    df["atr_pct"] = df["atr"] / c * 100
    df["amplitude"] = (h - l) / c.shift() * 100
    rng = (h - l).replace(0, np.nan)
    df["upper_shadow"] = (h - np.maximum(c, df["open"])) / rng
    df["lower_shadow"] = (np.minimum(c, df["open"]) - l) / rng
    return df


def _weighted_distance(feat, stat, shadow_key):
    mapping = {"rsi": feat["rsi"], "bias20": feat["bias20"],
               "vol_ratio5": feat["vol_ratio5"],
               "shadow": feat[shadow_key], "amplitude": feat["amplitude"]}
    dist_sq = 0.0
    for key, w in FEAT_WEIGHTS.items():
        sk = shadow_key if key == "shadow" else key
        std = stat[sk]["std"] or 1.0
        z = (mapping[key] - stat[sk]["median"]) / std
        dist_sq += w * z * z
    return math.sqrt(dist_sq)


def _sample_distance(feat, smp_feat, stat, shadow_key):
    pairs = {"rsi": (feat["rsi"], smp_feat["rsi"]),
             "bias20": (feat["bias20"], smp_feat["bias20"]),
             "vol_ratio5": (feat["vol_ratio5"], smp_feat["vol_ratio5"]),
             "shadow": (feat[shadow_key], smp_feat[shadow_key]),
             "amplitude": (feat["amplitude"], smp_feat["amplitude"])}
    dist_sq = 0.0
    for key, w in FEAT_WEIGHTS.items():
        sk = shadow_key if key == "shadow" else key
        std = stat[sk]["std"] or 1.0
        z = (pairs[key][0] - pairs[key][1]) / std
        dist_sq += w * z * z
    return math.sqrt(dist_sq)


def _nearest_sample(feat, pool, stat, shadow_key):
    best, best_d = None, float("inf")
    for smp in pool:
        smp_feat = {"rsi": smp["rsi"], "bias20": smp["bias20"],
                    "vol_ratio5": smp["vol_ratio5"], "amplitude": smp["amplitude"],
                    "lower_shadow": smp["lower_shadow"],
                    "upper_shadow": smp["upper_shadow"]}
        d = _sample_distance(feat, smp_feat, stat, shadow_key)
        if d < best_d:
            best, best_d = smp, d
    return best


def _distance_to_score(dist): return 100.0 * math.exp(-(dist**2) / (2*SIM_SIGMA**2))


def detect_swings(df, k=SWING_K):
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    pivots = []
    for i in range(k, n - k):
        is_h = all(highs[i] >= highs[i-j] for j in range(1, k+1)) and \
               all(highs[i] >= highs[i+j] for j in range(1, k+1))
        is_l = all(lows[i] <= lows[i-j] for j in range(1, k+1)) and \
               all(lows[i] <= lows[i+j] for j in range(1, k+1))
        if is_h:
            pivots.append((i, "H", highs[i]))
        elif is_l:
            pivots.append((i, "L", lows[i]))
    swings = []
    for p in pivots:
        if not swings:
            swings.append(p); continue
        _, lt, lp = swings[-1]
        idx, typ, price = p
        if typ == lt:
            if (typ == "H" and price > lp) or (typ == "L" and price < lp):
                swings[-1] = p
        else:
            swings.append(p)
    return swings


def _analyze_structure(df):
    n = len(df)
    closes = df["close"].values
    last_close = closes[-1]
    ma20 = float(df["ma20"].iloc[-1]) if np.isfinite(df["ma20"].iloc[-1]) else np.nan
    ma60 = float(df["ma60"].iloc[-1]) if np.isfinite(df["ma60"].iloc[-1]) else np.nan
    uptrend = False
    win = closes[-UPTREND_LOOKBACK:] if n > UPTREND_LOOKBACK else closes
    half = len(win) // 2
    if half > 0 and len(win) - half > 0:
        if float(np.max(win[half:])) > float(np.max(win[:half])):
            uptrend = True
    if np.isfinite(ma60) and last_close > ma60:
        uptrend = True
    if np.isfinite(ma20) and np.isfinite(ma60) and ma20 > ma60:
        uptrend = True
    swings = detect_swings(df, k=SWING_K)
    highs_idx = [s[0] for s in swings if s[1] == "H"]
    pullback_pct = pullback_days = rise_fall_ratio = None
    if highs_idx:
        hi = highs_idx[-1]
        seg = closes[hi:]
        trough_off = int(np.argmin(seg))
        trough_close = float(seg[trough_off])
        high_close = float(closes[hi])
        if high_close > 0:
            dd = (trough_close / high_close - 1) * 100
            if dd <= -1.0:
                pullback_pct = dd
                pullback_days = trough_off
                lows_before = [s[0] for s in swings if s[1] == "L" and s[0] < hi]
                if lows_before and dd < 0:
                    lo = lows_before[-1]
                    rise_pct = (high_close / float(closes[lo]) - 1) * 100
                    if rise_pct > 0:
                        rise_fall_ratio = rise_pct / abs(dd)
    return {"uptrend": uptrend, "pullback_pct": pullback_pct,
            "pullback_days": pullback_days, "rise_fall_ratio": rise_fall_ratio}


def _structure_score(s):
    """结构分（满分 37）：布尔项保持、量化项按程度梯度给分——
    回调带内越贴近中心分越高、回撤持续越短分越高、涨跌比越强分越高，
    避免旧版「满足即满分」导致大多数股票结构分趋同的虚高问题。"""
    score = 0.0
    if s["uptrend"]:
        score += STRUCT_UPTREND_SCORE
    pb = s["pullback_pct"]
    if pb is not None and PULLBACK_BAND_MIN <= pb <= PULLBACK_BAND_MAX:
        half = (PULLBACK_BAND_MAX - PULLBACK_BAND_MIN) / 2 or 1.0
        center = (PULLBACK_BAND_MAX + PULLBACK_BAND_MIN) / 2
        ratio = 1.0 - abs(pb - center) / half          # 带中心 1.0 → 带边缘 0.0
        score += STRUCT_PULLBACK_BAND_SCORE * (0.5 + 0.5 * ratio)   # 5 ~ 10
    days = s["pullback_days"]
    if days is not None and days <= PULLBACK_MAX_DAYS:
        score += STRUCT_DURATION_SCORE * max(0.2, 1.0 - days / PULLBACK_MAX_DAYS)   # 1 ~ 5
    ratio = s["rise_fall_ratio"]
    if ratio is not None and ratio >= RISE_FALL_RATIO_MIN:
        ratio_frac = min((ratio - RISE_FALL_RATIO_MIN) / (2.0 - RISE_FALL_RATIO_MIN), 1.0)
        score += STRUCT_RISE_FALL_SCORE * (0.2 + 0.8 * ratio_frac)  # 2 ~ 10
    return score


def _judge_tier(pb, rsi, bias20):
    if pb is not None and TIER_A_PULLBACK[0] <= pb <= TIER_A_PULLBACK[1] \
            and TIER_A_RSI[0] <= rsi <= TIER_A_RSI[1] \
            and bias20 >= TIER_A_BIAS20_MIN:
        return "A"
    if pb is not None and TIER_B_PULLBACK[0] <= pb <= TIER_B_PULLBACK[1] \
            and TIER_B_RSI[0] <= rsi <= TIER_B_RSI[1]:
        return "B"
    if rsi <= TIER_C_RSI_MAX and bias20 <= TIER_C_BIAS20_MAX:
        return "C"
    return "-"


def _grade(s):
    if s >= GRADE_STRONG: return "强匹配"
    if s >= GRADE_MEDIUM: return "较匹配"
    if s >= GRADE_WEAK:   return "弱匹配"
    return "不展示"


def _score(df: pd.DataFrame) -> Optional[dict]:
    if df is None or len(df) < MIN_ROWS:
        return None
    df = df.copy()
    if "date" in df.columns:
        df["date"] = df["date"].astype(str)
        df = df.sort_values("date").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(df) < MIN_ROWS:
        return None
    ind = calc_indicators(df)
    last = ind.iloc[-1]
    close = float(last["close"])
    if not np.isfinite(close) or close <= 0:
        return None
    rsi_v = float(last["rsi"]) if np.isfinite(last["rsi"]) else np.nan
    if not np.isfinite(rsi_v) or rsi_v < FILT_RSI_MIN or rsi_v > FILT_RSI_MAX:
        return None
    bias20 = float(last["bias20"]) if np.isfinite(last["bias20"]) else np.nan
    if not np.isfinite(bias20) or bias20 < FILT_BIAS20_MIN or bias20 > FILT_BIAS20_MAX:
        return None
    vol5 = float(last["vol_ratio5"]) if np.isfinite(last["vol_ratio5"]) else np.nan
    if np.isfinite(vol5) and vol5 > FILT_VOL_RATIO5_MAX:
        return None
    atr_pct = float(last["atr_pct"]) if np.isfinite(last["atr_pct"]) else np.nan
    if np.isfinite(atr_pct) and atr_pct > FILT_ATR_PCT_MAX:
        return None
    if len(ind) > RECENT_WINDOW:
        base = float(ind["close"].iloc[-1 - RECENT_WINDOW])
        if base > 0:
            chg60 = (close / base - 1) * 100
            if chg60 > FILT_GAIN60_MAX or chg60 < FILT_LOSS60_MAX:
                return None
    feat = {
        "rsi": rsi_v, "bias20": bias20,
        "bias60": float(last["bias60"]) if np.isfinite(last["bias60"]) else 0.0,
        "vol_ratio5": vol5 if np.isfinite(vol5) else 1.0,
        "amplitude": float(last["amplitude"]) if np.isfinite(last["amplitude"]) else 0.0,
        "upper_shadow": float(last["upper_shadow"]) if np.isfinite(last["upper_shadow"]) else 0.0,
        "lower_shadow": float(last["lower_shadow"]) if np.isfinite(last["lower_shadow"]) else 0.0,
        "atr_pct": atr_pct if np.isfinite(atr_pct) else 0.0,
    }
    is_bottom = (feat["rsi"] <= BOTTOM_ZONE_RSI_MAX
                 and feat["bias20"] <= BOTTOM_ZONE_BIAS20_MAX)
    if is_bottom:
        pool, stat, shadow_key, prefix = BOTTOM_POOL, BOTTOM_STAT, "lower_shadow", "底部"
    else:
        pool, stat, shadow_key, prefix = TOP_POOL, TOP_STAT, "upper_shadow", "顶部"
    dist = _weighted_distance(feat, stat, shadow_key)
    sim_score = _distance_to_score(dist) * SIM_WEIGHT_TOTAL
    struct = _analyze_structure(ind)
    struct_score = _structure_score(struct)
    raw_score = sim_score + struct_score
    final = raw_score if is_bottom else raw_score * TOP_SCORE_DISCOUNT
    final = max(0.0, min(100.0, final))
    nearest = _nearest_sample(feat, pool, stat, shadow_key)
    type_label = f"{prefix}·{nearest['tags'][0]}" if nearest else f"{prefix}·未知"
    tier = _judge_tier(struct["pullback_pct"], feat["rsi"], feat["bias20"])
    ret20 = np.nan
    if len(ind) > 20:
        b = float(ind["close"].iloc[-21])
        if b > 0:
            ret20 = (float(last["close"]) / b - 1) * 100
    return {
        "score": round(final, 1),
        "raw_score": round(raw_score, 1),
        "sim_score": round(sim_score, 1),
        "struct_score": round(struct_score, 1),
        # 档位按「折扣前」的原始匹配度判定：score 里的 TOP_SCORE_DISCOUNT 是
        # 「优先看底部」的排序惩罚，不是匹配质量。若用 final 判档，顶部形态
        # 上限 92×0.5=46 < GRADE_MEDIUM(60)，75/60 两档对顶部永远不可达，
        # 于是再像的顶部形态也只显示「弱匹配」——文案与实际不符。
        # 底部形态 raw==final，此处改动对它零影响。
        "grade": _grade(raw_score),
        "score_discount": 1.0 if is_bottom else TOP_SCORE_DISCOUNT,
        "is_bottom": is_bottom,
        "type_label": type_label,
        "nearest_sample": f"{nearest['stock']} {nearest['date']}" if nearest else "",
        "tags": list(nearest["tags"]) if nearest else [],
        "tier": TIER_FULL_NAMES[tier],
        "tier_short": TIER_SHORT_NAMES[tier],
        "date": str(last.get("date", "")),
        "close": round(close, 3),
        "rsi": round(feat["rsi"], 2),
        "bias20": round(feat["bias20"], 2),
        "vol_ratio5": round(feat["vol_ratio5"], 2),
        "amplitude": round(feat["amplitude"], 2),
        "lower_shadow": round(feat["lower_shadow"], 3),
        "upper_shadow": round(feat["upper_shadow"], 3),
        "pullback_pct": round(struct["pullback_pct"], 2) if struct["pullback_pct"] is not None else None,
        "rise_fall_ratio": round(struct["rise_fall_ratio"], 2) if struct["rise_fall_ratio"] is not None else None,
        "ret20": round(ret20, 2) if np.isfinite(ret20) else None,
    }


def _bars_for(code: str, n: int = 260) -> pd.DataFrame:
    rconn = db.reader()
    df = pd.read_sql_query(
        f"SELECT date, open, high, low, close, volume FROM daily "
        f"WHERE code=? ORDER BY date DESC LIMIT {int(n)}",
        rconn, params=(str(code),))
    if df.empty:
        return df
    return df.iloc[::-1].reset_index(drop=True)


def _init_worker(db_path: str) -> None:
    """子进程初始化：设置数据库路径（Windows spawn 不继承内存状态）。"""
    db.set_db_path(db_path)


def _score_worker(args) -> tuple:
    """模块级打分 worker（供 ProcessPool 使用，可 pickle）。"""
    code, name, sector, min_score, only_bottom, only_top = args
    try:
        df = _bars_for(code, 260)
        if df is None or len(df) < MIN_ROWS:
            return (code, name, sector, None, "数据不足")
        res = _score(df)
        if res is None:
            return (code, name, sector, None, "硬过滤")
        if only_top:
            # 「只看顶部形态（逃顶）」：原来前端只把它翻成 only_bottom=False，
            # 结果底部+顶部混在一起返回，而顶部因折扣分低被压在末尾 ≈ 看不到顶。
            # 现在真正按方向筛选。
            if res["is_bottom"]:
                return (code, name, sector, None, "底部状态")
        elif only_bottom and not res["is_bottom"]:
            return (code, name, sector, None, "顶部状态")
        # 分数线比的是「匹配度」(raw_score)，不是带折扣的排序分 score：
        # score 对顶部形态乘了 TOP_SCORE_DISCOUNT(0.5)，上限 92×0.5=46 < UI 默认
        # 分数线 60 —— 于是「逃顶」永远返回 0 条。raw_score 两类同尺度可比；
        # 底部形态 raw==final，故对默认视图零影响。
        if res["raw_score"] < min_score:
            return (code, name, sector, None, "低于分数")
        res["code"] = code
        res["name"] = name
        res["sector"] = sector
        return (code, name, sector, res, None)
    except Exception as e:
        return (code, name, sector, None, f"异常:{type(e).__name__}")


def run(min_score: float = 45.0, only_bottom: bool = True,
        exchange: Optional[str] = None, sectors: Optional[list] = None,
        max_workers: int = 8, only_top: bool = False,
        progress_cb=None) -> tuple[list[dict], dict]:
    """全市场形态打分。返回 (results, skip_stats)。

    min_score：**匹配度 (raw_score) 下限**，底部/顶部同一尺度。
    only_top=True：只保留顶部形态（与 only_bottom 互斥，only_top 优先）。
    """
    rconn = db.reader()
    where, args = "", []
    if exchange in ("SZ", "SH", "BJ"):
        where = " WHERE exchange=?"
        args.append(exchange)
    if sectors:
        if where:
            where += f" AND sector IN ({','.join('?'*len(sectors))})"
        else:
            where = f" WHERE sector IN ({','.join('?'*len(sectors))})"
        args.extend(sectors)
    rows = rconn.execute(
        f"SELECT code, name, sector FROM meta{where}", tuple(args)).fetchall()
    if not rows:
        return [], {}
    skip_stats: dict[str, int] = {}

    results = []
    total = len(rows)

    # 纯 CPU 打分受 GIL 限制，ThreadPool 多线程几乎无加速；改用进程池真正并行。
    # 池常驻复用（app/core/pool.py）：Windows spawn 下省去每次重建 worker 的 2-4s 开销。
    from ..core import pool as _ppool
    tasks = [(c, n, s, min_score, only_bottom, only_top) for c, n, s in rows]
    workers = max(1, min(max_workers, os.cpu_count() or 4))
    ex = _ppool.get_pool(workers, db.db_path())
    try:
        done = 0
        for code, name, sector, res, skip in ex.map(
                _score_worker, tasks, chunksize=64):
            done += 1
            if progress_cb and (done % 200 == 0 or done == total):
                progress_cb(done, total, f"打分 {done}/{total}")
            if res is not None:
                results.append(res)
            elif skip:
                skip_stats[skip] = skip_stats.get(skip, 0) + 1
    except BaseException:
        # 取消/异常时丢弃常驻池（取消未开始的任务，不等跑完），下次扫描重建
        _ppool.discard_pool()
        raise
    # 同分排序：分数 → 已入档（tier != "-"）优先 → 底部形态优先 → 相似分高优先
    results.sort(key=lambda r: (
        -r["score"],
        0 if r.get("tier_short", "-") != "-" else 1,
        0 if r.get("is_bottom") else 1,
        -float(r.get("sim_score") or 0),
    ))
    return results, skip_stats