# -*- coding: utf-8 -*-
"""短线快拍：形态打分模块下的短线专用筛选（4 个子模式，可多选）。

方法论来源与批判性吸收
======================
素材是用户 2026-09-21 提供的两份研究报告（原文归档在 .workbuddy/refs/）：
  R1《短周期大涨股量化识别与周期信号研究报告》
      —— 7 只标的 / 40 段主升浪（涨幅≥55%）；ZigZag 波段识别 + 启动特征分位归因 + 信号回测
  R2《形态匹配研究报告》
      —— 40 日「形态指纹」（10 维无量纲）+ 加权标准化距离匹配，全市场 3923 只

【采纳】
  1. R2 的形态指纹与加权标准化距离：只比较形状、不比较价位，183 元和 6 元的股票
     可在同一坐标系里比形态。直接服务「反弹动能」子模式（用 R2 的 A 模板）。
  2. R1 的 ZigZag 波段识别 → 服务「周期震荡」子模式；顶底交替由算法保证，
     「周期有持续性」量化为**段长的稳健变异系数**（MAD / 中位）。
  3. R1「爆发前 20 日」的共性（缩量、位置偏中低、温和超卖、波动收缩）+
     「爆发日的波动放大」→ 构成「即将大涨 / 即将反弹」子模式的主干。
  4. 年线 MA250 作为「趋势未破」硬门槛（R1：40 段主升浪无一例发生在年线下方）。
  5. R2 §3.3「容差语义下限」教训：容差若取模板内标准差，cur_dd 的 std 仅 0.0005，
     匹配器会退化成「只认自己」——模板容差必须带人工下限（见 _TOL_FLOOR）。

【修正 / 不采纳】
  1. **不照搬绝对阈值**。R1/R2 样本仅 7 只、40 段，R1 自己就列了
     「样本量小 / 生存者偏差 / 过拟合风险」三条局限。本模块把位置、量能、
     带宽等**全部改为全市场横截面分位数**；只有风控类门槛（价格、成交额、
     市值、ST、上市天数）保留绝对值。
  2. **不用未来函数**。R1 的 T0 特征（pos120=0.053 等）是启动当日快照，
     照抄等于「等启动」。本模块只使用 t 时刻及之前的收盘数据。
  3. **R2 的 A 模板是追高形态**（已反弹 40%+ 且创出区间新高），与「即将大涨」
     的左侧诉求方向相反 → A 模板只服务「反弹动能」（右侧，可即时购入），
     不用于预判。
  4. **不承诺收益**。R2 自述「未做收益回测，是描述性方法」；R1 回测 60 日
     胜率仅 52.9%（盈亏比 4.82:1，靠重尾赚钱）。本模块定位是**筛选器**，
     输出分数与触发要点，不做涨跌预测。

四个子模式
==========
  A 周期震荡  短周期内有一定振幅、顶底明显，且周期有一定持续性
  B 即将大涨  1~2 天内可能启动（缩量到极致 + 位置低 + 波动收缩 + 趋势未破）
  C 反弹动能  反弹动能极强且有一定持续性，可即时购入（V 型回踩后创新高）
  D 即将反弹  1~2 天内可能大幅反弹（回踩 + 缩量 + 贴支撑 + 止跌信号，左侧）

执行方式：与 pattern.py 一致 —— ProcessPool 常驻池（app/core/pool.py）+ 逐只取
300 根日K。纯 CPU 打分受 GIL 限制，多线程无加速；进程池实测约 6~12s（全市场）。
"""
from __future__ import annotations

import math
import os
from typing import Optional

import numpy as np
import pandas as pd

from ..core import db

# ---- 硬性门槛（风控类，保留绝对值） ----
MIN_ROWS = 260            # 至少 260 根：MA250 + 分位窗口都要够
BARS = 320                # 实际取数根数
MIN_PRICE, MAX_PRICE = 3.0, 400.0
MIN_AMT20_YI = 1.0        # 20 日均成交额下限（亿元）
MIN_MKTCAP_YI = 30.0      # 流通市值下限（亿元）
MIN_LISTED_DAYS = 250     # 非次新

MODE_NAMES = {"A": "周期震荡", "B": "即将大涨", "C": "反弹动能", "D": "即将反弹"}

DEFAULTS = {
    "modes": ["A", "B", "C", "D"],
    "min_score": 55.0,
    "require_above_ma250": True,     # 年线一票否决（趋势未破），对所有子模式生效
    # ZigZag 阈值：日线上 7% 会把日内噪声也识别成波段（实测 180 日能切出 18 段，
    # 段长中位仅 7 日 —— 那不是「周期」而是噪声）。12% 后波段数落到个位数。
    "zz_thr": 0.12,
    "cycle_lookback": 180,           # 周期震荡回看天数
    "min_price": MIN_PRICE,
    "max_price": MAX_PRICE,
    "min_amt20_yi": MIN_AMT20_YI,
    "min_mktcap_yi": MIN_MKTCAP_YI,
    "min_vol20": 0.30,               # 年化波动率下限（弹性基因），0=不限
    "min_listed_days": MIN_LISTED_DAYS,
}

# ---------------------------------------------------------------- R2 模板 C
# 模板参数来自 R2 §6.2：4 只严格同构标的（300475/300454/001389/300672）
# 近 40 日形态指纹的中位数为中心，容差 = max(1.4826×MAD, 语义下限)。
_TEMPLATE_C = {
    "center": {"lo_pos": 0.051, "v_depth": -0.097, "rebound": 0.405, "cur_dd": 0.000,
               "from_low": 0.405, "slope": 0.412, "vol": 0.679, "end_pos": 1.000,
               "late_mom": 0.102, "days_since_low": 37.0},
    "tol":    {"lo_pos": 0.090, "v_depth": 0.070, "rebound": 0.150, "cur_dd": 0.040,
               "from_low": 0.150, "slope": 0.160, "vol": 0.130, "end_pos": 0.050,
               "late_mom": 0.060, "days_since_low": 10.0},
    "dir":    {"lo_pos": -1, "v_depth": -1, "rebound": 1, "cur_dd": 1, "from_low": 1,
               "slope": 1, "vol": 1, "end_pos": 1, "late_mom": 1, "days_since_low": 1},
    "weight": {"lo_pos": 2.4, "v_depth": 1.2, "rebound": 1.8, "cur_dd": 3.0,
               "from_low": 1.8, "slope": 1.5, "vol": 1.0, "end_pos": 3.0,
               "late_mom": 1.2, "days_since_low": 1.4},
}
# 容差语义下限（R2 §3.3 的 ②）——上表 tol 已经是 max(MAD, 下限) 的结果，
# 这里额外兜一层，防止未来改 center 时忘记重算 tol 导致匹配器退化。
_TOL_FLOOR = {"lo_pos": 0.05, "v_depth": 0.03, "rebound": 0.06, "cur_dd": 0.02,
              "from_low": 0.06, "slope": 0.08, "vol": 0.05, "end_pos": 0.03,
              "late_mom": 0.03, "days_since_low": 4.0}


# ================================================================ 基础工具
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _band(x: float, lo: float, hi: float, soft: float = 0.5) -> float:
    """软区间得分：落在 [lo, hi] 内为 1，外侧按 soft×区间宽度衰减到 0。"""
    if x is None or not np.isfinite(x):
        return 0.0
    if lo <= x <= hi:
        return 1.0
    span = max(hi - lo, 1e-9)
    if x < lo:
        return float(max(0.0, 1.0 - (lo - x) / (span * soft)))
    return float(max(0.0, 1.0 - (x - hi) / (span * soft)))


def _pct_rank(series: np.ndarray, value: float) -> float:
    """value 在 series 中的分位（0~1）。NaN 剔除；样本不足时返回 0.5（中性）。"""
    s = np.asarray(series, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < 20 or not np.isfinite(value):
        return 0.5
    return float((s <= value).mean())


def _zigzag(values: np.ndarray, thr: float) -> list:
    """百分比 ZigZag：返回 [(index, price, 'H'|'L'), ...] 顶底交替序列。

    与 ant1000 同源做法 —— 必须用 ZigZag 而非「贪心 pivot 配对」：后者会把
    一个大波段拆成若干小段（R1/ant1000 均已踩过这个坑）。
    """
    n = len(values)
    if n < 3 or thr <= 0:
        return []
    piv: list = []
    i_max = i_min = 0
    v_max = v_min = float(values[0])
    direction = 0                      # 0 未定 / 1 上行 / -1 下行
    base = float(values[0])
    for i in range(1, n):
        v = float(values[i])
        if v > v_max:
            i_max, v_max = i, v
        if v < v_min:
            i_min, v_min = i, v
        if direction == 0:
            if v_max >= base * (1 + thr):
                piv.append((i_min, v_min, "L"))
                direction = 1
                i_max, v_max = i, v
            elif v_min <= base * (1 - thr):
                piv.append((i_max, v_max, "H"))
                direction = -1
                i_min, v_min = i, v
        elif direction == 1:
            if v <= v_max * (1 - thr):
                piv.append((i_max, v_max, "H"))
                direction = -1
                i_min, v_min = i, v
        else:
            if v >= v_min * (1 + thr):
                piv.append((i_min, v_min, "L"))
                direction = 1
                i_max, v_max = i, v
    # 收尾：把最后一段的极值也算成 pivot（供「距最近顶/底天数」使用）
    if direction == 1:
        piv.append((i_max, v_max, "H"))
    elif direction == -1:
        piv.append((i_min, v_min, "L"))
    # 去重（可能出现连续同类型）
    out = []
    for p in piv:
        if out and out[-1][2] == p[2]:
            keep = p if ((p[2] == "H" and p[1] > out[-1][1])
                         or (p[2] == "L" and p[1] < out[-1][1])) else out[-1]
            out[-1] = keep
        else:
            out.append(p)
    return out


def _shape_fingerprint(close: np.ndarray, window: int = 40) -> Optional[dict]:
    """R2 的 10 维形态指纹（全部无量纲，可跨价位比较）。"""
    s = np.asarray(close, dtype=float)[-window:]
    n = len(s)
    if n < window or not np.all(np.isfinite(s)) or s.min() <= 0:
        return None
    hi_i, lo_i = int(np.argmax(s)), int(np.argmin(s))
    rng = s.max() - s.min()
    x = (np.arange(n) - n / 2) / (n / 2)
    r = np.diff(s) / s[:-1]
    sd = s.std()
    return {
        "lo_pos": lo_i / (n - 1),
        "v_depth": s[lo_i] / s[0] - 1,
        "rebound": s[hi_i] / s[lo_i] - 1,
        "cur_dd": s[-1] / s[hi_i] - 1,
        "from_low": s[-1] / s[lo_i] - 1,
        "end_pos": (s[-1] - s.min()) / (rng + 1e-9),
        "slope": float((x * ((s - s.mean()) / (sd + 1e-9))).mean()),
        "vol": float(r.std() * np.sqrt(250)) if r.size else 0.0,
        "late_mom": s[-1] / s[int(n * 2 / 3)] - 1,
        "days_since_low": n - 1 - lo_i,
    }


def _similarity(fp: dict) -> float:
    """与 R2 模板 C 的相似度（0~100）。加权标准化距离 → 指数映射。"""
    keys = list(_TEMPLATE_C["center"])
    z_sq = 0.0
    w_sum = 0.0
    for k in keys:
        tol = max(_TEMPLATE_C["tol"][k], _TOL_FLOOR[k])
        z = (fp[k] - _TEMPLATE_C["center"][k]) * _TEMPLATE_C["dir"][k] / tol
        w = _TEMPLATE_C["weight"][k]
        z_sq += w * z * z
        w_sum += w
    dist = math.sqrt(z_sq / max(w_sum, 1e-9))
    return 100.0 * math.exp(-0.60 * dist)


# ================================================================ 指标
def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    for n in (5, 10, 20, 60, 250):
        df[f"ma{n}"] = c.rolling(n).mean()
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    df["dif"], df["dea"], df["macd"] = dif, dea, (dif - dea) * 2
    for n, col in ((6, "rsi6"), (14, "rsi14")):
        delta = c.diff()
        ag = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        al = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
        rs = ag / al.replace(0, np.nan)
        df[col] = 100 - 100 / (1 + rs)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["atr_pct"] = df["atr"] / c * 100
    df["vol_ma5"] = v.rolling(5).mean()
    df["vol_ma20"] = v.rolling(20).mean()
    df["vol_ratio5"] = v / df["vol_ma5"].replace(0, np.nan)
    df["amt_ma20"] = df["amount"].rolling(20).mean() if "amount" in df.columns else np.nan
    mid = df["ma20"]
    std = c.rolling(20).std()
    df["boll_up"] = mid + 2 * std
    df["boll_low"] = mid - 2 * std
    df["boll_w"] = (df["boll_up"] - df["boll_low"]) / mid.replace(0, np.nan)
    df["bias20"] = (c - df["ma20"]) / df["ma20"] * 100
    df["amplitude"] = (h - l) / c.shift() * 100
    rng = (h - l).replace(0, np.nan)
    df["upper_shadow"] = (h - np.maximum(c, df["open"])) / rng
    df["lower_shadow"] = (np.minimum(c, df["open"]) - l) / rng
    df["body"] = (c - df["open"]).abs() / rng
    return df


def _context(ind: pd.DataFrame) -> dict:
    """构造四个子模式共用的最新时点特征（全部只用 t 及之前的数据）。"""
    last = ind.iloc[-1]
    c = float(last["close"])

    def fl(col, dflt=np.nan):
        v = last.get(col, np.nan)
        return float(v) if v is not None and np.isfinite(v) else dflt

    win250 = ind.tail(250)
    win120 = ind.tail(120)
    win40 = ind.tail(40)

    hi250 = float(win250["high"].max())
    lo120 = float(win120["low"].min())
    hi120 = float(win120["high"].max())
    dd250 = c / hi250 - 1 if hi250 > 0 else np.nan
    pos120 = ((c - lo120) / (hi120 - lo120)) if hi120 > lo120 else 0.5

    hi40 = float(win40["high"].max())
    dd40 = c / hi40 - 1 if hi40 > 0 else np.nan

    ma250 = fl("ma250")
    above = bool(np.isfinite(ma250) and ma250 > 0 and c > ma250)
    ma250_dev = (c / ma250 - 1) if np.isfinite(ma250) and ma250 > 0 else np.nan

    mas = [fl(f"ma{n}") for n in (5, 10, 20)]
    if all(np.isfinite(m) and m > 0 for m in mas):
        ma_spread = (max(mas) - min(mas)) / float(np.mean(mas))
    else:
        ma_spread = np.nan

    rets = ind["close"].pct_change().tail(20).dropna().values
    vol20 = float(np.std(rets) * np.sqrt(250)) if rets.size >= 10 else np.nan

    # 止跌信号计数（0~4）——「即将反弹」子模式需要至少 2 个
    sig = 0
    ls_max = float(ind["lower_shadow"].tail(3).max())
    if np.isfinite(ls_max) and ls_max >= 0.35:
        sig += 1                                   # 下影探底
    if bool(((ind["body"].tail(3) < 0.25) & (ind["amplitude"].tail(3) < 6.0)).any()):
        sig += 1                                   # 缩量小实体 / 十字星
    if len(ind) >= 2:
        r6, r14 = ind["rsi6"], ind["rsi14"]
        if float(r6.iloc[-2]) <= float(r14.iloc[-2]) and float(r6.iloc[-1]) > float(r14.iloc[-1]):
            sig += 1                               # RSI6 上穿 RSI14
    if len(ind) >= 3:
        m3 = ind["macd"].tail(3).values
        if np.all(np.isfinite(m3)) and m3[2] > m3[1] > m3[0]:
            sig += 1                               # MACD 柱连续回升

    return {
        "close": c,
        "date": str(last.get("date", ""))[:10],
        "amt20": fl("amt_ma20"),
        "pos120": float(np.clip(pos120, 0.0, 1.0)) if np.isfinite(pos120) else 0.5,
        "dd250": dd250,
        "dd40": dd40,
        "rsi6": fl("rsi6"), "rsi14": fl("rsi14"),
        "bias20": fl("bias20"),
        "d20": (c / fl("ma20") - 1) if np.isfinite(fl("ma20")) and fl("ma20") > 0 else np.nan,
        "boll_w": fl("boll_w"),
        "boll_w_rank": _pct_rank(ind["boll_w"].tail(250).values, fl("boll_w")),
        "vol_rank": _pct_rank(ind["volume"].tail(250).values, fl("volume")),
        "vratio5": fl("vol_ratio5", 1.0),
        "vol20": vol20,
        "ma250": ma250,
        "above_ma250": above,
        "ma250_dev": ma250_dev,
        "ma_spread": ma_spread,
        "atr_pct": fl("atr_pct"),
        "sig_count": sig,
    }


# ================================================================ 四个子模式
def _mode_cycle(ind: pd.DataFrame, ctx: dict, cfg: dict):
    """A 周期震荡：短周期内有一定振幅且顶底明显，且周期有一定的持续性。

    「周期」定义为**同类型相邻顶（或底）之间的间隔**（顶→顶 / 底→底），
    而不是相邻顶底之间的半周期；「持续性」用周期长度的稳健变异系数衡量。
    """
    look = int(cfg["cycle_lookback"])
    close = ind["close"].values[-look:]
    if close.size < 80:
        return None
    piv = _zigzag(close, float(cfg["zz_thr"]))
    if len(piv) < 5:
        return None
    segs = list(zip(piv[:-1], piv[1:]))
    amps = [abs(p[1] / q[1] - 1.0) for q, p in segs if q[1] > 0]
    if len(amps) < 4:
        return None
    # 噪声闸门：平均段长 < 8 个交易日说明阈值太细，切出来的是日内噪声不是波段
    if len(segs) > close.size / 8:
        return None
    hidx = [p[0] for p in piv if p[2] == "H"]
    lidx = [p[0] for p in piv if p[2] == "L"]
    hp = [hidx[k + 1] - hidx[k] for k in range(len(hidx) - 1)] if len(hidx) >= 2 else []
    lp = [lidx[k + 1] - lidx[k] for k in range(len(lidx) - 1)] if len(lidx) >= 2 else []
    periods = [p for p in (hp + lp) if p > 0]
    if len(periods) < 2 or not hp or not lp:
        return None
    amp_med = float(np.median(amps))
    per_med = float(np.median(periods))
    n_cycle = min(len(hp), len(lp))
    if n_cycle < 2 or amp_med < 0.08 or not (15.0 <= per_med <= 90.0):
        return None
    mad_p = float(np.median(np.abs(np.array(periods, dtype=float) - per_med)))
    cv = mad_p / per_med if per_med > 0 else 1.0
    q_amp = float(np.exp(-(np.log(max(amp_med, 1e-6) / 0.15) ** 2) / (2 * 0.45 ** 2)))
    q_dur = float(np.exp(-(np.log(max(per_med, 1e-6) / 30.0) ** 2) / (2 * 0.65 ** 2)))
    q_reg = float(max(0.0, min(1.0, 1.0 - cv / 0.50)))
    q_cnt = float(min(1.0, n_cycle / 3.0))
    score = 100.0 * (0.32 * q_amp + 0.22 * q_dur + 0.28 * q_reg + 0.18 * q_cnt)
    last_i, _, last_t = piv[-1]
    days_since = close.size - 1 - last_i
    return score, {
        "n_cycle": n_cycle,
        "n_seg": len(segs),
        "amp_pct": amp_med * 100,
        "period": per_med,
        "reg": q_reg,
        "near": ("底" if last_t == "L" else "顶"),
        "days_since": days_since,
    }


def _mode_surge(ind: pd.DataFrame, ctx: dict, cfg: dict):
    """B 即将大涨：缩量到极致 + 位置低 + 波动收缩 + 趋势未破（R1 爆发前 20 日特征）。

    门槛对齐 R1 的「爆发前 20 日」分布：量能分位 0.440、pos120 0.457、
    drawdown −29.2%（40% 落全样本 p75 之上，说明是「回撤不深但整理充分」）。
    """
    if ctx["vol_rank"] > 0.45 or ctx["pos120"] > 0.55:
        return None
    if not np.isfinite(ctx["dd250"]) or ctx["dd250"] > -0.18:
        return None
    if ctx["boll_w_rank"] > 0.50:
        return None
    q_vol = 1.0 - ctx["vol_rank"]
    q_boll = 1.0 - ctx["boll_w_rank"]
    q_pos = 1.0 - ctx["pos120"]
    q_dd = _band(ctx["dd250"], -0.62, -0.15)
    q_rsi = _band(ctx["rsi14"], 25.0, 55.0)
    q_ma = 1.0 - min(1.0, (ctx["ma_spread"] / 0.08)) if np.isfinite(ctx["ma_spread"]) else 0.0
    q_gene = _band(ctx["vol20"], 0.32, 1.30, soft=0.6)
    score = 100.0 * (0.22 * q_vol + 0.18 * q_boll + 0.16 * q_pos + 0.12 * q_dd
                     + 0.12 * q_rsi + 0.10 * q_ma + 0.10 * q_gene)
    return score, {"q_vol": q_vol, "q_boll": q_boll, "q_ma": q_ma, "q_dd": q_dd}


def _mode_rebound(ind: pd.DataFrame, ctx: dict, cfg: dict):
    """C 反弹动能：R2 的 A 模板 —— V 型回踩后收在区间上沿、正在向上攻。"""
    fp = _shape_fingerprint(ind["close"].values, 40)
    if fp is None:
        return None
    if fp["lo_pos"] > 0.28 or fp["rebound"] < 0.22:
        return None
    if fp["cur_dd"] < -0.16 or fp["slope"] < 0.10:
        return None
    if fp["end_pos"] < 0.75 or fp["days_since_low"] < 8:
        return None
    if np.isfinite(ctx["vol20"]) and ctx["vol20"] < 0.35:
        return None                          # 弹性基因不足
    score = _similarity(fp)
    return score, {
        "rebound_pct": fp["rebound"] * 100,
        "cur_dd_pct": fp["cur_dd"] * 100,
        "slope": fp["slope"],
        "lo_pos": fp["lo_pos"],
        "days_since_low": fp["days_since_low"],
    }


def _mode_dip(ind: pd.DataFrame, ctx: dict, cfg: dict):
    """D 即将反弹：回踩 + 缩量 + 贴支撑 + 止跌信号（左侧，1~2 天）。

    回撤区间取 R2 §6.4「B. 回踩不破前低（多氟多型）」的 cur_dd ∈ [−30%, −10%]，
    并要求至少 2 个止跌信号（下影探底 / 缩量小实体 / RSI6 上穿 / MACD 柱回升），
    只出现 1 个不足以说明「跌不动了」。
    """
    if not np.isfinite(ctx["dd40"]) or not (-0.32 <= ctx["dd40"] <= -0.10):
        return None
    if not np.isfinite(ctx["dd250"]) or ctx["dd250"] > -0.15:
        return None
    if ctx["vol_rank"] > 0.50 or ctx["sig_count"] < 2:
        return None
    q_dd = _band(ctx["dd40"], -0.32, -0.08)
    q_vol = 1.0 - ctx["vol_rank"]
    q_vr = float(max(0.0, min(1.0, (1.15 - ctx["vratio5"]) / 0.40)))
    q_sup = _band(ctx["d20"], -0.12, 0.03, soft=0.8)
    q_rsi = _band(ctx["rsi14"], 20.0, 52.0)
    q_sig = min(1.0, ctx["sig_count"] / 3.0)
    q_space = float(max(0.0, min(1.0, -ctx["dd250"] / 0.45)))
    score = 100.0 * (0.20 * q_dd + 0.16 * q_vol + 0.12 * q_vr + 0.14 * q_sup
                     + 0.12 * q_rsi + 0.16 * q_sig + 0.10 * q_space)
    return score, {"q_sup": q_sup, "q_vr": q_vr, "q_sig": q_sig, "sig": ctx["sig_count"],
                   "dd40_pct": ctx["dd40"] * 100}


_MODE_FN = {"A": _mode_cycle, "B": _mode_surge, "C": _mode_rebound, "D": _mode_dip}


# ================================================================ 主打分
def _hard_filter(ind: pd.DataFrame, meta: dict, cfg: dict) -> Optional[str]:
    """风控类硬门槛（绝对值）。返回跳过原因，通过则返回 None。"""
    c = float(ind["close"].iloc[-1])
    if not (cfg["min_price"] <= c <= cfg["max_price"]):
        return "价格区间"
    name = str(meta.get("name") or "")
    if "ST" in name.upper() or "退" in name:
        return "ST/退市"
    amt20 = float(ind["amt_ma20"].iloc[-1]) if np.isfinite(ind["amt_ma20"].iloc[-1]) else 0.0
    if amt20 < cfg["min_amt20_yi"] * 1e8:
        return "流动性不足"
    shar = ind["outstanding_share"].iloc[-1] if "outstanding_share" in ind.columns else np.nan
    if np.isfinite(shar) and float(shar) > 0:
        mktcap_yi = c * float(shar) / 1e8
        if mktcap_yi < cfg["min_mktcap_yi"]:
            return "市值不足"
    ld = str(meta.get("listing_date") or "")
    if len(ld) >= 10:
        try:
            import datetime as _dt
            days = (_dt.date.today() - _dt.date.fromisoformat(ld[:10])).days
            if days < int(cfg["min_listed_days"]):
                return "次新股"
        except Exception:  # noqa: BLE001 —— 上市日格式异常时不做次新剔除
            pass
    v20 = None
    rets = ind["close"].pct_change().tail(20).dropna().values
    if rets.size >= 10:
        v20 = float(np.std(rets) * np.sqrt(250))
    if cfg["min_vol20"] and v20 is not None and v20 < cfg["min_vol20"]:
        return "波动率不足"
    return None


def _score(df: pd.DataFrame, meta: dict, cfg: dict):
    """返回 (结果 dict | None, 跳过原因)。"""
    if df is None or len(df) < MIN_ROWS:
        return None, "数据不足"
    df = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(df) < MIN_ROWS:
        return None, "数据不足"
    ind = calc_indicators(df)
    skip = _hard_filter(ind, meta, cfg)
    if skip:
        return None, skip
    ctx = _context(ind)
    if not np.isfinite(ctx["close"]) or ctx["close"] <= 0:
        return None, "数据异常"
    # 年线一票否决（全局，对四个子模式同时生效）：R1 的 40 段主升浪无一例发生
    # 在年线下方，跌破年线意味着「用过期地图找路」。
    if cfg["require_above_ma250"] and not ctx["above_ma250"]:
        return None, "年线下方"

    hits: dict = {}
    sub: dict = {}
    for m in cfg["modes"]:
        fn = _MODE_FN.get(m)
        if fn is None:
            continue
        try:
            r = fn(ind, ctx, cfg)
        except Exception:  # noqa: BLE001 —— 单模式异常不影响其它模式
            r = None
        if r is None:
            continue
        sc, det = r
        if not np.isfinite(sc):
            continue
        sub[m] = round(float(sc), 1)
        if sc >= cfg["min_score"]:
            hits[m] = (float(sc), det)
    if not hits:
        return None, "未触发"

    best_mode = max(hits, key=lambda k: hits[k][0])
    best_score, best_det = hits[best_mode]
    order = sorted(hits.keys(), key=lambda k: -hits[k][0])

    # 触发要点：从命中的模式里挑最有信息量的几条
    bits = []
    if "A" in hits:
        d = hits["A"][1]
        bits.append(f"{d['n_cycle']}轮周期（均振幅{d['amp_pct']:.0f}%·周期{d['period']:.0f}日）")
    if "B" in hits:
        bits.append(f"缩量至{ctx['vol_rank'] * 100:.0f}%分位·带宽{ctx['boll_w_rank'] * 100:.0f}%分位")
    if "C" in hits:
        d = hits["C"][1]
        bits.append(f"低点后第{d['days_since_low']:.0f}日·反弹{d['rebound_pct']:.0f}%")
    if "D" in hits:
        d = hits["D"][1]
        bits.append(f"止跌信号{d['sig']}/4·距20日线{ctx['d20'] * 100:+.1f}%")
    if np.isfinite(ctx["ma250_dev"]):
        bits.append(f"距年线{ctx['ma250_dev'] * 100:+.1f}%")
    bits.append(f"回撤{ctx['dd250'] * 100:.0f}%")

    return {
        "modes": "+".join(MODE_NAMES[k] for k in order),
        "main": MODE_NAMES[best_mode],
        "score": round(best_score, 1),
        "sA": sub.get("A"), "sB": sub.get("B"),
        "sC": sub.get("C"), "sD": sub.get("D"),
        "n_cycles": hits["A"][1]["n_cycle"] if "A" in hits else None,
        "amp_pct": round(hits["A"][1]["amp_pct"], 1) if "A" in hits else None,
        "cycle_days": round(hits["A"][1]["period"], 1) if "A" in hits else None,
        "pos120": round(ctx["pos120"], 3),
        "vol_rank": round(ctx["vol_rank"], 3),
        "dd250_pct": round(ctx["dd250"] * 100, 1),
        "rebound_pct": round(hits["C"][1]["rebound_pct"], 1) if "C" in hits else None,
        "ma250_dev_pct": round(ctx["ma250_dev"] * 100, 1) if np.isfinite(ctx["ma250_dev"]) else None,
        "close": round(ctx["close"], 2),
        "state": " · ".join(bits),
        "date": ctx["date"],
    }, None


# ================================================================ 数据 & 并行
def _bars_for(code: str, n: int = BARS) -> pd.DataFrame:
    rconn = db.reader()
    df = pd.read_sql_query(
        f"SELECT date, open, high, low, close, volume, amount, outstanding_share "
        f"FROM daily WHERE code=? ORDER BY date DESC LIMIT {int(n)}",
        rconn, params=(str(code),))
    if df.empty:
        return df
    return df.iloc[::-1].reset_index(drop=True)


def _init_worker(db_path: str) -> None:
    db.set_db_path(db_path)


def _score_worker(args) -> tuple:
    """模块级 worker（供 ProcessPool 使用，可 pickle）。"""
    code, meta, cfg = args
    try:
        df = _bars_for(code, BARS)
        res, skip = _score(df, meta, cfg)
        if res is None:
            return (code, res, skip)
        return (code, res, None)
    except Exception as e:  # noqa: BLE001
        return (code, None, f"异常:{type(e).__name__}")


def run(cfg: Optional[dict] = None, exchange: Optional[str] = None,
        sectors: Optional[list] = None, max_workers: int = 8,
        progress_cb=None) -> tuple[list, dict]:
    """全市场短线快拍。返回 (results, skip_stats)。"""
    c = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if v is not None:
            c[k] = v
    c["modes"] = [m for m in (c.get("modes") or []) if m in _MODE_FN] or list(_MODE_FN)
    c["min_score"] = float(c["min_score"])
    c["zz_thr"] = float(c["zz_thr"])
    c["cycle_lookback"] = int(c["cycle_lookback"])

    rconn = db.reader()
    where, args = "", []
    if exchange in ("SZ", "SH", "BJ"):
        where = " WHERE exchange=?"
        args.append(exchange)
    if sectors:
        if where:
            where += f" AND sector IN ({','.join('?' * len(sectors))})"
        else:
            where = f" WHERE sector IN ({','.join('?' * len(sectors))})"
        args.extend(sectors)
    rows = rconn.execute(
        f"SELECT code, name, sector, listing_date FROM meta{where}", tuple(args)).fetchall()
    if not rows:
        return [], {}

    tasks = [(str(r[0]), {"name": r[1], "sector": r[2], "listing_date": r[3]}, c)
             for r in rows]
    total = len(tasks)
    skip_stats: dict = {}
    results: list = []

    # 与 pattern.py 一致：纯 CPU 打分受 GIL 限制 → 用常驻进程池
    from ..core import pool as _ppool
    workers = max(1, min(int(max_workers), os.cpu_count() or 4))
    ex = _ppool.get_pool(workers, db.db_path())
    try:
        done = 0
        for code, res, skip in ex.map(_score_worker, tasks, chunksize=64):
            done += 1
            if progress_cb and (done % 200 == 0 or done == total):
                progress_cb(done, total, f"快拍 {done}/{total}")
            if res is not None:
                res["code"] = code
                res["name"] = None      # 由下面的 meta 映射补，避免 worker 传中文
                results.append(res)
            elif skip:
                skip_stats[skip] = skip_stats.get(skip, 0) + 1
    except BaseException:
        _ppool.discard_pool()
        raise

    name_map = {str(r[0]): (r[1], r[2]) for r in rows}
    for r in results:
        nm, sec = name_map.get(r["code"], ("", ""))
        r["name"] = nm
        r["sector"] = sec
    results.sort(key=lambda r: (-r["score"], r["code"]))
    return results, skip_stats
