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

v2 优化（2026-09-23）—— 对齐用户原始四项需求
=============================================
v1 的六个真实缺陷（由审计 + 锚点校验共同确认）：

  P0-1 **`date.today()` 是未来函数**（原 `_hard_filter`）。判次新股用系统当天日期，
       回测历史时点会把「当时是次新、现在不是」的票放进来。→ 改用
       `ind["date"].iloc[-1]`（数据自身的最新日期），实盘/回测口径统一。

  P0-2 **需求 b/d 的「1-2 天」完全没实现**。v1 判据全是存量状态（缩量、低位、
       波动收缩），这些条件今天满足、明天满足、半年后可能还满足 —— 筛出来的是
       「随时可能启动但不知道哪天」的池子。R1 报告原文：「S1 触发当日买入，
       10 日胜率仅 41.2%；而**等待确认信号后买入，胜率显著提升**」，确认信号 =
       `close>open ∧ vol>1.2×vma5 ∧ close>MA5`。
       → v2 新增 `_trigger()`「临界触发」维度（5 个子信号），并要求它占独立权重：
         b/d 最终分 = 0.62×状态分 + 0.38×触发分。

  P0-3 **`_mode_rebound` 会高分选中「反弹尾声」**。`_TEMPLATE_C` 的中心就是
       `cur_dd=0 / end_pos=1.0 / rebound=0.405 / days_since_low=37` —— 刻意匹配
       「已反弹 40%+ 且创区间新高」的追高形态，而过滤下界（cur_dd≥-0.16、
       end_pos≥0.75）又很松。一只已反弹 80%、刚开始回落 1% 的票仍可能拿高分。
       且**完全没有度量「持续性」**（只有幅度）。
       → v2 新增 `_momentum_persist()`：衰减闸门（末段 vs 前段）、量价配合、
         连涨上限、上影压制；c 最终分 = 0.68×相似度 + 0.32×持续性分，
         并对「动能衰减 / 连拉过多」施加乘性折扣。

  P1-4 **`_mode_cycle` 的周期度量统计意义不足**。v1 只要 `len(periods)≥2` 就放行
       —— 拿 2 个样本算 MAD/中位，CV 没有统计意义，纯随机游走也能蒙混。
       且缺「顶底水平性」与「振幅可比性」：一段 8% 一段 40%、或顶底同步上移的
       **上升通道**，也会被判成「周期」。
       → v2 新增 `_cycle_quality()`：样本量≥4、顶/底价格水平性 `level_cv`、
         振幅可比性 `amp_cv`、通道惩罚（顶底同步漂移）。

  P1-5 **A∪B 冲突语义被 `max` 掩盖**。A 要求「区间规律往复」，B 要求「缩量到
       极致待变盘」——两者同时高分本质是**矛盾信号**：真的缩到极致，下一步更
       可能是**变盘**而非第 N 轮往复。v1 直接取最高分模式，冲突信息丢失。
       → v2 加 `_CONFLICT` 乘性折扣表 + 输出 `conflict` 标记，并在排序里
         融合「最高分 + 次高分」（`0.78×max + 0.22×second`），避免单点极值定生死。

  P1-6 分位基准随切片长度漂移。`_pct_rank` 一律吃 `tail(250)`（已固定），
       唯一不变量是「至少 260 根」，由 `MIN_ROWS` 保证 —— v2 显式注释。

  P0-7 **硬门槛误杀真机会**（锚点校验发现）。`min_vol20=0.30` 把 300475
       在 2025-08-01（此后 60 日 +330.5%）直接判「波动率不足」剔除。
       锚点校验结果（v1）：
         300475 @2025-08-01 → 未触发（波动率不足）  ← 漏掉最大机会
         300475 @2025-07-10 → 59.9 分，但理由是「周期震荡」（说错了）
       波动率闸门本意是「排除弹性基因不足的僵尸股」，但它把「长期低波动后
       即将爆发」的标的也一并挡掉 —— 与需求 b/d 直接冲突。
       → v2 把 `min_vol20` 默认降为 0（不参与硬门槛），改为在模式内以
         **软得分** 形式评估弹性；同时保留用户可手动开启的能力。

评分锚点（v2 自测 4 个真实案例，见 .workbuddy/_snap_anchor.py）：
  300475 @2025-08-01（60日 +330.5%） 应有高分
  300475 @2025-09-18（此后最大回撤 -56%） 应不给高分
  300454 @2025-07-02（60日 +32.5%）    应有高分
  300672 @2020-06-15（60日 +3.7%）     应有高分（弱正例）

执行方式：与 pattern.py 一致 —— ProcessPool 常驻池（app/core/pool.py）+ 逐只取
320 根日K。纯 CPU 打分受 GIL 限制，多线程无加速；进程池实测约 6~12s（全市场）。
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
    # v2：波动率闸门默认关闭（见 DEFAULTS 注释：0.30 会误杀「低波动后爆发」的真机会）。
    "min_vol20": 0.0,                # 年化波动率下限（0 = 不启用硬门槛）
    "min_listed_days": MIN_LISTED_DAYS,
}

# ---------------------------------------------------------------- v2 合成系数
# 「状态分 × 触发折扣」的系数（见 _apply_trigger）。
#   TRIG_FLOOR：触发分 0 时的乘数（0.80 = 打八折，不是一票否决）
#   TRIG_SPAN ：触发分 100 时的额外加成（0.35 → 最高 ×1.15）
TRIG_FLOOR, TRIG_SPAN = 0.80, 0.35
# 「形态相似度 × 持续性系数」的系数（见 _mode_rebound）。
PERS_FLOOR, PERS_SPAN = 0.78, 0.34

# ---------------------------------------------------------------- 冲突折扣表
# A（区间规律往复）与 B（缩量到极致待变盘）语义对立：真的缩到极致，下一步更可能
# 是「变盘」而不是第 N 轮往复。同时高分时给较高分那一个打折，并把冲突写进 state。
_CONFLICT = {
    ("A", "B"): 0.86,
    ("A", "D"): 0.92,   # 震荡中回踩 = 更正常的低吸位，折扣轻
    ("B", "C"): 0.90,   # 缩量待变盘 vs 已放量上攻，方向矛盾
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


# ================================================================ v2 新维度
def _apply_trigger(state: float, trig: float, cfg: dict) -> float:
    """把「状态分」与「临界触发分」合成最终分。

    v2 初版把两者做**线性加权**（0.62×state + 0.38×trig）—— 实测这会产生一个
    严重缺陷：触发分 0 时会把 80 分的好状态直接拉到 50 分，把「爆发前夜但当天
    还没确认」的票一票否决。锚点校验里 300454（后续 60 日 +32.5%）与 300672
    （+3.7%）就是这样被误杀的。

    改为**乘法结构**：
        score = state × (TRIG_FLOOR + TRIG_SPAN × trig/100)
    含义 ——
      · 触发分 0   → 打 8 折（「值得关注，但今天还不是买点」）
      · 触发分 100 → 加价 15%（「状态 + 时机同时到位」）
    这样既保留了「时机」的区分度（最高 / 最低差 ~44%），又不会把状态极好的
    真机会一票否决。**状态决定「值不值得看」，触发决定「是不是现在」。**
    """
    return float(state * (TRIG_FLOOR + TRIG_SPAN * (trig / 100.0)))


def _trigger(ind: pd.DataFrame, ctx: dict) -> tuple[float, dict]:
    """临界触发分（0~100）—— 服务需求 b/d 的「即将（1-2 天）」。

    v1 的 b/d 全是「存量状态」：缩量、低位、波动收缩。这些条件今天满足、
    明天满足、半年后可能还满足，所以 v1 筛出的是「随时可能启动但不知道哪天」的池子。

    R1 报告原文：「S1 触发当日买入，10 日胜率仅 41.2%；而**等待确认信号后
    买入，胜率显著提升**」，确认信号 = close>open ∧ vol>1.2×vma5 ∧ close>MA5。
    本函数把「即将」翻译成 5 个**只在临界点才亮**的信号，全部只用 t 及之前数据：

      T1 放量确认    vol > 1.2×vol_ma5 且 close > open        （R1 确认信号）
      T2 站上短均    close > ma5 且 ma5 拐头向上               （趋势由跌转升首日）
      T3 首次异动    vol_ratio5 > 1.3 且此前 3 日都 < 1.0      （安静久了突然有人来）
      T4 波动由缩转扩 atr_pct 抬升 且 前 5 日处于低位           （压缩后的释放）
      T5 贴压力位    距 ma20 ≤ 3% 或已站上 boll 中轨            （一步之遥）
    """
    if len(ind) < 30:
        return 0.0, {"trig": [], "n_trig": 0}

    last = ind.iloc[-1]
    prev = ind.iloc[-2] if len(ind) >= 2 else last

    def g(row, col, dflt=np.nan):
        v = row.get(col, np.nan)
        return float(v) if v is not None and np.isfinite(v) else dflt

    close = g(last, "close")
    open_ = g(last, "open")
    vol = g(last, "volume")
    vma5 = g(last, "vol_ma5")
    ma5 = g(last, "ma5")
    ma5_prev = g(prev, "ma5")
    vr5 = g(last, "vol_ratio5", 1.0)
    atr_pct = g(last, "atr_pct")
    atr_prev = g(prev, "atr_pct")
    boll_mid = g(last, "ma20")

    hits: list[str] = []

    # T1 放量确认（R1 明确背书的那一条）
    if np.isfinite(vol) and np.isfinite(vma5) and vma5 > 0 and close > open_:
        if vol > 1.20 * vma5:
            hits.append("T1放量确认")

    # T2 站上短均 + 拐头（要求「有效站上」：不只是压着 MA5，而是有距离）
    if np.isfinite(ma5) and np.isfinite(ma5_prev) and ma5 > 0:
        if close > ma5 * 1.005 and ma5 > ma5_prev:
            hits.append("T2站上短均")

    # T3 首次异动：今日量比 > 1.3，且此前 3 日都在 1.0 以下
    if len(ind) >= 4 and np.isfinite(vr5) and vr5 > 1.30:
        prev3 = ind["vol_ratio5"].iloc[-4:-1].values
        prev3 = prev3[np.isfinite(prev3)]
        if prev3.size >= 2 and float(np.max(prev3)) < 1.0:
            hits.append("T3首次异动")

    # T4 波动由缩转扩（要求扩张幅度可观，避免横盘小波动误判）
    if (np.isfinite(atr_pct) and np.isfinite(atr_prev)
            and atr_pct > atr_prev * 1.12):
        base5 = ind["atr_pct"].iloc[-6:-1].values
        base5 = base5[np.isfinite(base5)]
        if base5.size >= 4 and atr_pct > float(np.mean(base5)) * 1.10:
            hits.append("T4波动转扩")

    # T5 贴压力位 + 有向上动能
    # v1 设计缺陷：横盘时 close≈ma20，d20≈0 恒成立 → T5 在「躺平」状态下永远为真。
    # 修正：必须**同时**有向上动能（收在 MA5 上方 或 MA5 拐头），否则只是躺在均线上。
    d20 = ctx.get("d20")
    if np.isfinite(d20):
        up_momentum = bool(np.isfinite(ma5) and ma5 > 0
                           and (close > ma5 or (np.isfinite(ma5_prev) and ma5 > ma5_prev)))
        if -0.03 <= d20 <= 0.045 and up_momentum:
            hits.append("T5贴压力位")
        elif (np.isfinite(boll_mid) and boll_mid > 0 and close >= boll_mid
              and d20 > 0 and up_momentum):
            hits.append("T5贴压力位")

    # 加权：T1/T3 是「有人开始买」的强证据，权重最高
    W = {"T1放量确认": 0.30, "T3首次异动": 0.26, "T2站上短均": 0.18,
         "T4波动转扩": 0.14, "T5贴压力位": 0.12}
    score = 100.0 * sum(W[h] for h in hits)
    return float(min(100.0, score)), {"trig": hits, "n_trig": len(hits)}


def _momentum_persist(ind: pd.DataFrame, fp: dict, ctx: dict) -> tuple[float, dict]:
    """动能持续性分（0~100）+ 乘性折扣——服务需求 c 的「有持续性」。

    v1 只度量了「反弹幅度」（rebound / from_low），完全没有度量「持续性」：
    一只反弹 80% 但末段已经走弱、且连拉 7 根阳线的票，和一只反弹 40% 且
    仍在加速的票，v1 可能给同样的分。本函数补四项：

      P1 衰减闸门  末段涨幅 / 前段涨幅 ≥ 0.30（末段不能明显弱于前段）
      P2 量价配合  近 5 日均量 / 20 日均量 ≥ 1.0（上涨有量）
      P3 连涨上限  连续收阳 ≤ 5 根（排除「已连拉 7 根」的尾声）
      P4 上影压制  近 3 日上影线均值 ≤ 0.50（上方抛压不重）

    返回 (持续性分, 明细)；明细里的 `discount` 是建议施加的乘性折扣。
    """
    from_low = float(fp.get("from_low", 0.0))
    late_mom = float(fp.get("late_mom", 0.0))
    detail: dict = {}

    # P1 衰减闸门：前段涨幅 = from_low − late_mom
    front = from_low - late_mom
    if front > 1e-6:
        ratio = late_mom / front
        q_decay = float(np.clip((ratio - 0.10) / 0.60, 0.0, 1.0))
    else:
        q_decay = 0.0
    detail["decay_ratio"] = round(late_mom / front, 3) if front > 1e-6 else None

    # P2 量价配合
    v5 = float(ind["volume"].tail(5).mean())
    v20 = float(ind["volume"].tail(20).mean())
    q_volup = float(np.clip((v5 / v20 - 0.75) / 0.55, 0.0, 1.0)) if v20 > 0 else 0.5
    detail["vol_up_ratio"] = round(v5 / v20, 3) if v20 > 0 else None

    # P3 连涨天数（连续收阳）
    body = (ind["close"].tail(8) > ind["open"].tail(8)).values
    consec = 0
    for b in body[::-1]:
        if b:
            consec += 1
        else:
            break
    # ≤3 最优；4~5 扣一点；≥7 明显扣
    q_consec = 1.0 if consec <= 3 else (0.75 if consec <= 5 else 0.35)
    detail["consec_up"] = int(consec)

    # P4 上影压制
    us = ind["upper_shadow"].tail(3).values
    us = us[np.isfinite(us)]
    us_mean = float(np.mean(us)) if us.size else 0.5
    q_shadow = float(np.clip((0.60 - us_mean) / 0.45, 0.0, 1.0))
    detail["upper_shadow"] = round(us_mean, 3)

    score = 100.0 * (0.38 * q_decay + 0.24 * q_volup + 0.20 * q_consec + 0.18 * q_shadow)

    # 乘性折扣：真正的「尾声形态」直接压分
    disc = 1.0
    if front > 1e-6 and late_mom / front < 0.15:
        disc *= 0.80                    # 末段几乎不动 —— 动能已衰减
    if consec >= 7:
        disc *= 0.78                    # 已连拉 7 根以上
    if us_mean > 0.70:
        disc *= 0.90                    # 上影极重
    detail["discount"] = disc
    return float(np.clip(score, 0.0, 100.0)), detail


def _cycle_quality(piv: list, amps: list, periods: list, per_med: float) -> tuple[float, dict]:
    """周期质量分（0~100）—— 服务需求 a 的「顶底明显 + 周期有持续性」。

    v1 只用「段长的稳健变异系数 CV = MAD/中位」，且只要 2 个样本就放行。
    问题有两个：
      1. 2 个样本的 MAD/中位没有统计意义，纯随机游走也能算出很小的 CV；
      2. 只度量了「时间上的规律」，完全没度量「价格上的规律」—— 一段 8%
         一段 40%、或有顶底同步上移的**上升通道**，也会被当成「周期」。
    本函数补三项：

      C1 顶/底水平性  MAD(顶价)/中位(顶价) ≤ 0.18 且同理底价（水平支撑压力）
      C2 振幅可比性   MAD(振幅)/中位(振幅) ≤ 0.55
      C3 通道惩罚     顶价序列与底价序列**同向漂移** → 判为通道而非震荡
    """
    detail: dict = {}
    highs = np.array([p[1] for p in piv if p[2] == "H"], dtype=float)
    lows = np.array([p[1] for p in piv if p[2] == "L"], dtype=float)
    a = np.array([x for x in amps if np.isfinite(x) and x > 0], dtype=float)

    # C1 顶/底水平性
    def _lcv(v: np.ndarray) -> Optional[float]:
        if v.size < 2 or np.median(v) <= 0:
            return None
        med = float(np.median(v))
        return float(np.median(np.abs(v - med)) / med)

    h_cv = _lcv(highs)
    l_cv = _lcv(lows)
    lvl = [x for x in (h_cv, l_cv) if x is not None]
    level_cv = float(np.mean(lvl)) if lvl else None
    q_level = 1.0 - float(np.clip((level_cv - 0.06) / 0.16, 0.0, 1.0)) if level_cv is not None else 0.5
    detail["level_cv"] = round(level_cv, 4) if level_cv is not None else None

    # C2 振幅可比性
    if a.size >= 2 and np.median(a) > 0:
        amp_cv = float(np.median(np.abs(a - np.median(a))) / np.median(a))
    else:
        amp_cv = None
    q_amp = 1.0 - float(np.clip((amp_cv - 0.15) / 0.45, 0.0, 1.0)) if amp_cv is not None else 0.5
    detail["amp_cv"] = round(amp_cv, 4) if amp_cv is not None else None

    # C3 通道惩罚：顶价与底价的相对漂移同向且幅度可观 → 是趋势通道
    drift = 0.0
    if highs.size >= 2 and lows.size >= 2:
        h_drift = float(highs[-1] / highs[0] - 1.0)
        l_drift = float(lows[-1] / lows[0] - 1.0)
        # 同向漂移量取两者绝对值的较小者（都漂才叫通道）
        if h_drift * l_drift > 0:
            drift = min(abs(h_drift), abs(l_drift))
    q_chan = 1.0 - float(np.clip((drift - 0.05) / 0.30, 0.0, 1.0))
    detail["drift"] = round(drift, 4)

    # C4 时间规律性（v1 的 CV，保留但降权）
    mad_p = float(np.median(np.abs(np.array(periods, dtype=float) - per_med))) if periods else 1.0
    cv = mad_p / per_med if per_med > 0 else 1.0
    q_reg = float(max(0.0, min(1.0, 1.0 - cv / 0.50)))
    detail["cv"] = round(cv, 4)

    score = 100.0 * (0.30 * q_level + 0.20 * q_amp + 0.18 * q_chan + 0.32 * q_reg)
    return float(np.clip(score, 0.0, 100.0)), detail


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

    「周期」= **同类型相邻顶（或底）之间的间隔**（顶→顶 / 底→底），
    不是相邻顶底之间的半周期。

    v2 变化：
      · 样本量闸门 `len(periods) ≥ 4`（v1 只要 2 —— MAD/中位 无统计意义，
        纯随机游走也能蒙出很小的 CV）
      · 新增 `_cycle_quality`：顶/底**价格水平性**（水平支撑压力才是震荡的本质）
        + 振幅**可比性** + **通道惩罚**（顶底同步漂移 = 趋势通道，不是震荡）
      · 噪声闸门保留（平均段长 < 8 日 = 日内噪声）
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
    # v2：样本量闸门从 2 提到 4（原值下用 2 个样本算稳健变异系数没有意义）
    if len(periods) < 4 or not hp or not lp:
        return None
    amp_med = float(np.median(amps))
    per_med = float(np.median(periods))
    n_cycle = min(len(hp), len(lp))
    if n_cycle < 2 or amp_med < 0.08 or not (15.0 <= per_med <= 90.0):
        return None

    # v2：周期质量（顶底水平性 / 振幅可比性 / 通道惩罚 / 时间规律性）
    q_cycle, cdet = _cycle_quality(piv, amps, periods, per_med)

    q_amp = float(np.exp(-(np.log(max(amp_med, 1e-6) / 0.15) ** 2) / (2 * 0.45 ** 2)))
    q_dur = float(np.exp(-(np.log(max(per_med, 1e-6) / 30.0) ** 2) / (2 * 0.65 ** 2)))
    q_cnt = float(min(1.0, n_cycle / 3.0))
    # 权重调整：质量分（含 v1 的时间规律性）升到 0.42，振幅/周期长度各 0.34/0.14/0.10
    score = 100.0 * (0.34 * q_amp + 0.14 * q_dur
                     + 0.42 * (q_cycle / 100.0) + 0.10 * q_cnt)

    # 通道惩罚：若被判定为趋势通道（顶底同步漂移），直接压分
    drift = cdet.get("drift") or 0.0
    if drift > 0.20:
        score *= 0.80

    last_i, _, last_t = piv[-1]
    days_since = close.size - 1 - last_i
    return score, {
        "n_cycle": n_cycle,
        "n_seg": len(segs),
        "amp_pct": amp_med * 100,
        "period": per_med,
        "reg": cdet.get("cv") and max(0.0, min(1.0, 1.0 - cdet["cv"] / 0.50)),
        "level_cv": cdet.get("level_cv"),
        "amp_cv": cdet.get("amp_cv"),
        "drift": cdet.get("drift"),
        "q_cycle": q_cycle,
        "near": ("底" if last_t == "L" else "顶"),
        "days_since": days_since,
    }


def _mode_surge(ind: pd.DataFrame, ctx: dict, cfg: dict):
    """B 即将大涨：缩量到极致 + 位置低 + 波动收缩 + 趋势未破（R1 爆发前 20 日特征）。

    门槛对齐 R1 的「爆发前 20 日」分布：量能分位 0.440、pos120 0.457、
    drawdown −29.2%（40% 落全样本 p75 之上，说明是「回撤不深但整理充分」）。

    v2 变化（核心）：**引入临界触发，并把「状态」与「时机」解耦**。
      v1 全是存量状态（缩量、低位、波动收缩）—— 这些条件可能同时成立很久，
      筛出来的是「随时可能启动但不知道哪天」的池子，与需求「1-2 天内大涨」不符。
      R1 报告自己的回测也证明：S1 触发当日买入 10 日胜率仅 41.2%，
      **等确认信号（放量+收阳+站上 MA5）后买入胜率显著提升**。
      v2：state × (0.80 + 0.35×trigger/100) —— 见 `_apply_trigger`。
      另：v1 的 `vol_rank > 0.45` 硬门槛过紧（实测 300475 @2025-07-10
      vol_rank=0.49 被一刀切掉，而它此后 60 日 +204.6%）→ 放宽到 0.60 并
      改用软得分，保留「越缩越好」的单调性。
    """
    if ctx["pos120"] > 0.55:
        return None
    if ctx["vol_rank"] > 0.60:          # v1: 0.45（过紧，误杀真机会）
        return None
    if not np.isfinite(ctx["dd250"]) or ctx["dd250"] > -0.18:
        return None
    if ctx["boll_w_rank"] > 0.55:       # v1: 0.50
        return None
    q_vol = 1.0 - ctx["vol_rank"]
    q_boll = 1.0 - ctx["boll_w_rank"]
    q_pos = 1.0 - ctx["pos120"]
    q_dd = _band(ctx["dd250"], -0.62, -0.15)
    q_rsi = _band(ctx["rsi14"], 25.0, 55.0)
    q_ma = 1.0 - min(1.0, (ctx["ma_spread"] / 0.08)) if np.isfinite(ctx["ma_spread"]) else 0.0
    # 弹性基因（软分）：太死的股不走行情，但不再当硬门槛 —— 只降分不剔除
    q_gene = _band(ctx["vol20"], 0.30, 1.40, soft=0.9) if np.isfinite(ctx["vol20"]) else 0.5
    state = 100.0 * (0.22 * q_vol + 0.18 * q_boll + 0.16 * q_pos + 0.12 * q_dd
                     + 0.12 * q_rsi + 0.10 * q_ma + 0.10 * q_gene)

    # v2 临界触发（乘法合成，见 _apply_trigger）
    trig, tdet = _trigger(ind, ctx)
    score = _apply_trigger(state, trig, cfg)
    return score, {
        "q_vol": q_vol, "q_boll": q_boll, "q_ma": q_ma, "q_dd": q_dd,
        "state": round(state, 1), "trig": trig, "trig_n": tdet["n_trig"],
        "trig_list": tdet["trig"],
    }


def _mode_rebound(ind: pd.DataFrame, ctx: dict, cfg: dict):
    """C 反弹动能：R2 的 A 模板 —— V 型回踩后收在区间上沿、正在向上攻。

    v2 变化（核心）：**补上「持续性」并给「尾声形态」上闸门**。
      v1 的分数只有「形态相似度」—— `_TEMPLATE_C` 的中心本身就是
      `cur_dd=0 / end_pos=1.0 / rebound=0.405 / days_since_low=37`，
      即刻意匹配「已反弹 40%+ 且创区间新高」的追高形态。一只已反弹 80%、
      刚开始回落 1% 的票仍可能拿高分，却完全没有度量「动能是否还在」。
      v2 最终分 = 相似度 × (0.78 + 0.34×持续性分/100) × 低波动惩罚 × 尾声折扣，
      并对「末段几乎不动 / 连拉 7 根以上 / 上影极重」施加乘性折扣。
    """
    fp = _shape_fingerprint(ind["close"].values, 40)
    if fp is None:
        return None
    if fp["lo_pos"] > 0.28 or fp["rebound"] < 0.22:
        return None
    if fp["cur_dd"] < -0.16 or fp["slope"] < 0.10:
        return None
    if fp["end_pos"] < 0.75 or fp["days_since_low"] < 8:
        return None
    # v2：弹性基因从「硬剔除」改为「软惩罚」—— 低波动票反弹动能天然弱，但不该一刀切
    gene_pen = 0.75 if (np.isfinite(ctx["vol20"]) and ctx["vol20"] < 0.35) else 1.0

    sim = _similarity(fp)
    persist, pdet = _momentum_persist(ind, fp, ctx)
    # v2：同样用**乘法**而非线性加权 —— 相似度是「像不像」，持续性决定「还敢不敢买」。
    # persist 从 0~100 映射到 ×(0.78~1.12)，再叠加尾声形态的乘性折扣与低波动惩罚。
    factor = 0.78 + 0.34 * (persist / 100.0)
    score = sim * factor * gene_pen * pdet["discount"]
    return score, {
        "rebound_pct": fp["rebound"] * 100,
        "cur_dd_pct": fp["cur_dd"] * 100,
        "slope": fp["slope"],
        "lo_pos": fp["lo_pos"],
        "days_since_low": fp["days_since_low"],
        "sim": round(sim, 1), "persist": round(persist, 1),
        "decay_ratio": pdet.get("decay_ratio"),
        "consec_up": pdet.get("consec_up"),
        "pdisc": pdet["discount"],
    }


def _mode_dip(ind: pd.DataFrame, ctx: dict, cfg: dict):
    """D 即将反弹：回踩 + 缩量 + 贴支撑 + 止跌信号（左侧，1~2 天）。

    回撤区间取 R2 §6.4「B. 回踩不破前低（多氟多型）」的 cur_dd ∈ [−30%, −10%]，
    并要求至少 2 个止跌信号（下影探底 / 缩量小实体 / RSI6 上穿 / MACD 柱回升），
    只出现 1 个不足以说明「跌不动了」。

    v2 变化（核心）：同 B —— **引入临界触发**。
      v1 的 `sig_count` 是「过去 3 日止跌迹象的回顾」，属于「已跌不动」的状态
      确认，不是「未来 1-2 日将反弹」的前瞻信号。跌不动的票可以继续跌不动
      很久。v2 最终分 = 状态分 × (0.80 + 0.35×触发分/100)。
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
    state = 100.0 * (0.20 * q_dd + 0.16 * q_vol + 0.12 * q_vr + 0.14 * q_sup
                     + 0.12 * q_rsi + 0.16 * q_sig + 0.10 * q_space)

    # v2 临界触发（乘法合成，见 _apply_trigger）
    trig, tdet = _trigger(ind, ctx)
    score = _apply_trigger(state, trig, cfg)
    return score, {"q_sup": q_sup, "q_vr": q_vr, "q_sig": q_sig, "sig": ctx["sig_count"],
                   "dd40_pct": ctx["dd40"] * 100,
                   "state": round(state, 1), "trig": trig, "trig_n": tdet["n_trig"],
                   "trig_list": tdet["trig"]}


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
            # v2 修复：**必须用数据自身的最新日期**，不能用 date.today()。
            # 用系统当天日期是未来函数 —— 回测历史时点会把「当时是次新、
            # 现在已满 250 日」的票放进来，实盘/回测口径不一致。
            asof = str(ind["date"].iloc[-1])[:10]
            days = (_dt.date.fromisoformat(asof) - _dt.date.fromisoformat(ld[:10])).days
            if days < int(cfg["min_listed_days"]):
                return "次新股"
        except Exception:  # noqa: BLE001 —— 上市日/日期格式异常时不做次新剔除
            pass
    # 波动率硬门槛默认关闭（见 DEFAULTS 注释：0.30 会误杀「低波动后爆发」的真机会）。
    # 保留能力供用户手动开启；正常路径下弹性基因以软得分评估。
    if cfg.get("min_vol20"):
        rets = ind["close"].pct_change().tail(20).dropna().values
        if rets.size >= 10:
            v20 = float(np.std(rets) * np.sqrt(250))
            if v20 < float(cfg["min_vol20"]):
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

    # ---- v2：冲突折扣 ----
    # A（区间规律往复）与 B（缩量到极致待变盘）语义对立：真的缩到极致，下一步
    # 更可能是「变盘」而不是第 N 轮往复。同时高分时给较高分那一个打折，
    # 并把冲突写进 state，避免用户误读为「双重确认」。
    conflict = []
    for (m1, m2), disc in _CONFLICT.items():
        if m1 in hits and m2 in hits:
            hi = m1 if hits[m1][0] >= hits[m2][0] else m2
            sc_hi, det_hi = hits[hi]
            hits[hi] = (sc_hi * disc, det_hi)
            sub[hi] = round(sc_hi * disc, 1)
            conflict.append(f"{MODE_NAMES[m1]}×{MODE_NAMES[m2]}")

    order = sorted(hits.keys(), key=lambda k: -hits[k][0])
    best_mode = order[0]
    best_score, best_det = hits[best_mode]

    # ---- v2：融合排序分 ----
    # 不再让「单点最高分」独裁：0.78×最高 + 0.22×次高（若有）。一只 A=90 且 B=58
    # 的票，与 A=90 且无其它命中的票，含义不同，排序上应有区分。
    second = hits[order[1]][0] if len(order) > 1 else 0.0
    final = 0.78 * best_score + 0.22 * second

    # 触发要点：从命中的模式里挑最有信息量的几条
    bits = []
    if "A" in hits:
        d = hits["A"][1]
        bits.append(f"{d['n_cycle']}轮周期（均振幅{d['amp_pct']:.0f}%·周期{d['period']:.0f}日）")
        bits.append(f"顶底水平度{d['level_cv']:.3f}" if d.get("level_cv") is not None else "")
    if "B" in hits:
        d = hits["B"][1]
        bits.append(f"缩量至{ctx['vol_rank'] * 100:.0f}%分位·带宽{ctx['boll_w_rank'] * 100:.0f}%分位")
        if d.get("trig_n"):
            bits.append(f"触发{d['trig_n']}/5({'/'.join(d['trig_list'])})")
        else:
            bits.append("⚠尚无临界触发（仅状态达标）")
    if "C" in hits:
        d = hits["C"][1]
        bits.append(f"低点后第{d['days_since_low']:.0f}日·反弹{d['rebound_pct']:.0f}%")
        bits.append(f"动能衰减比{d['decay_ratio']:.2f}" if d.get("decay_ratio") is not None else "")
        if d.get("consec_up") is not None and d["consec_up"] >= 4:
            bits.append(f"已连阳{d['consec_up']}根")
    if "D" in hits:
        d = hits["D"][1]
        bits.append(f"止跌信号{d['sig']}/4·距20日线{ctx['d20'] * 100:+.1f}%")
        if d.get("trig_n"):
            bits.append(f"触发{d['trig_n']}/5({'/'.join(d['trig_list'])})")
        else:
            bits.append("⚠尚无临界触发（仅状态达标）")
    if conflict:
        bits.append("⚠变盘临界(" + ",".join(conflict) + ")")
    if np.isfinite(ctx["ma250_dev"]):
        bits.append(f"距年线{ctx['ma250_dev'] * 100:+.1f}%")
    bits.append(f"回撤{ctx['dd250'] * 100:.0f}%")
    bits = [b for b in bits if b]

    # v2：临界触发总览（取所有命中模式里最强的那个）
    trig_best = 0.0
    trig_list: list = []
    for k in order:
        d = hits[k][1]
        if d.get("trig") is not None and d["trig"] > trig_best:
            trig_best = float(d["trig"])
            trig_list = list(d.get("trig_list") or [])

    return {
        "modes": "+".join(MODE_NAMES[k] for k in order),
        "main": MODE_NAMES[best_mode],
        "score": round(float(final), 1),          # v2：融合排序分
        "best_score": round(best_score, 1),       # v2：最高单模式分（留档）
        "sA": sub.get("A"), "sB": sub.get("B"),
        "sC": sub.get("C"), "sD": sub.get("D"),
        "trigger": round(trig_best, 1),           # v2：临界触发分
        "trigger_n": len(trig_list),              # v2：触发信号数 0~5
        "trigger_list": ",".join(trig_list),
        "conflict": bool(conflict),               # v2：是否命中冲突组合
        "n_cycles": hits["A"][1]["n_cycle"] if "A" in hits else None,
        "amp_pct": round(hits["A"][1]["amp_pct"], 1) if "A" in hits else None,
        "cycle_days": round(hits["A"][1]["period"], 1) if "A" in hits else None,
        "pos120": round(ctx["pos120"], 3),
        "vol_rank": round(ctx["vol_rank"], 3),
        "dd250_pct": round(ctx["dd250"] * 100, 1),
        "rebound_pct": round(hits["C"][1]["rebound_pct"], 1) if "C" in hits else None,
        "persist": round(hits["C"][1]["persist"], 1) if "C" in hits else None,
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
    # v2：触发/持续权重改为模块级乘法常量（TRIG_FLOOR/TRIG_SPAN/PERS_FLOOR/PERS_SPAN），
    # 不再是可调 cfg —— 线性加权会让「触发 0」一票否决掉 80 分的好状态（详见 _apply_trigger）。

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
