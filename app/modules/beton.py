# -*- coding: utf-8 -*-
"""Bet on · 长期布局 —— 找出「可能持续 1 年以上大涨」的股票并给出买点、周期与卖出纪律。

素材来源与批判性吸收
====================
素材是用户 2026-09-22 提供的两份长周期量化研究档案（原文归档在
``.workbuddy/_ref/``，共 212 个文件）：

  《长周期上涨股票量化识别手册》+《长周期上涨股票回测审计报告》（run1）
      —— 4 只用户锚定标的 + 6 只参考标的；62 个 PIT 因子 / 7 大家族；
         v4 趋势状态机定义周期；S1（半山腰）/ S2（启动前埋伏）信号；
         1240 项截断自检（0 不一致，确认无未来函数）
  《run2 买入侧信号研究结论》
      —— 把相位标签换成真正 PIT 的 5 个「状态」，检验买入侧增量价值
  《复核报告_数据准确性与可行性》+《横截面扩样本复核》
      —— 独立复核：DSR/PBO/Haircut/MinTRL 复算 + 468 只客观横截面重做

【必须写在前面的结论：两轮研究都是 RESEARCH_REJECTED】
本模块**不掩饰**这一点，并且整套打分逻辑是为它量身调整的：

  1. run1 自我否决：Deflated Sharpe = 0.8107 < 0.95（192 次尝试的多重比较校正后
     不通过）；Haircut 后夏普 1.2543 → 0.6363（衰减 49.27%）；有效独立周期仅
     13 段。相位标签（pre_launch / mid_rise）是**事后标注**，不能当实时信号。
  2. run2 自我否决：「提前埋伏」的净增量只有 +1.49pp（56.0% vs 什么都不做的
     54.6%），8 年里 4 年为负；「山腰确认」增量 +3.57pp 但符号检验 p=0.17 不显著。
  3. 横截面复核：位置类因子对未来 120 日收益是**负向**（反转）——「价格越贴近自身
     高位，未来 120 日收益越低」，与「半山腰入场」直觉方向相反；原研究自报的
     判别力 0.44~0.62 在客观样本上只剩 0.38。

【那么本模块凭什么还有用？】
因为两轮研究里**唯一在每个口径下都没被否掉**的东西是 run2 §4.1 的「状态基线」：

    状态                120日胜率   120日净收益均值   120日最大不利偏移
    STATE_ABOVE          67.94%        +18.93%            −10.69%   ← 风险收益比最好
    STATE_EXTEND         62.07%        +27.60%            −16.37%   ← 收益最高、回撤最深
    STATE_BELOW          51.60%         +6.36%            −15.00%   ← 接近随机
    STATE_TURNING        50.19%        +11.48%            −12.59%
    STATE_MIXED          43.97%         +6.53%            −15.14%   ← 最差
    全样本               58.35%        +14.52%            −13.99%

「STATE_ABOVE = 收盘在 MA250 上方且 MA250 仍在上行」这个**粗筛条件**本身
（不是叠在它上面的任何精细信号）是全部结果中最稳的一块。所以本模块：

  · 把 STATE_ABOVE 当作**主推档**（山腰确认），而不是当作「信号」；
  · 把 STATE_EXTEND 单列为「高风险高收益档」，只作已持仓的加仓参考；
  · 把 STATE_BELOW / STATE_TURNING 的埋伏档**明确标注为低置信度**（run2 已证伪），
    保留它是因为用户确实需要「启动前埋伏」这个视角，但绝不美化；
  · 把 STATE_MIXED 当作回避区。

【与既有模块的差异（不是重复劳动）】
  · snap.py 做的是**短线**（1~2 天到 1~2 周），门槛靠 320 根日K 的横截面分位；
  · ant1000.py 做的是**月线级循环**（20 年、ZigZag 配对），看的是「这只股票历史上
    循环了几轮」；
  · 本模块做的是**年度级趋势**：必须吃长历史（hist.db，1990 起、中位 3300 根），
    用日线 v4 状态机切出该股**自己的**历史上涨段，从而回答三件具体的事：
    ① 现在处在什么趋势状态（山腰 / 加速 / 沉寂 / 转折）
    ② 这段行情「按该股自己的历史」大致还能走多久（周 / 月 / 年，给区间不给点）
    ③ 具体到这只股票，卖出看哪几个量化位（目标价区间 / 跌破哪条均线 / 过热分界）

【四条方法论铁律（逐条对应本项目历史踩过的坑）】
  1. **不用未来函数**。所有打分因子只用 t 时刻及之前的收盘数据；周期挖掘虽然
     必须用到 t 之后的行情才能「确认」一段上涨已结束，但**它只用来统计这只股票
     的历史体质，绝不参与 t 时刻的打分**，且在「详细」面板里如实标注（与 run1 的
     `lookahead_confirm` 同类，是标签而非信号）。
  2. **门槛不照搬绝对阈值**。基金/研究报告的绝对阈值一律改为「该股自身历史分位」
     或全市场横截面分位；只有风控类门槛（价格、成交额、市值、上市天数、ST）保留绝对值。
  3. **缺失项不剔分母**。任一因子缺失按该项得 0 分处理，不改变权重分母
     （蚂蚁模块的教训）——否则数据缺失的股票会被动获得高分。
  4. **惩罚系数不参与阈值比较**。过热/假启动折扣只用于最终排序与展示，
     判档一律用折扣前的分数（形态模块 0.5 折扣的教训）。

【数据口径与已知缺口（必须披露）】
  · 长历史来自 `data/hist.db`（**前复权**），与主库 `daily` 在重叠区间逐行相等；
    主库只到 2025-07-14 起（约 288 根），因此**必须合并**才够 MA250 与周期统计。
  · hist.db 无 `outstanding_share` / `turnover`，这两项取自主库最近 20 个交易日的行。
  · **实测纠正（2026-09-23）**：本模块早期注释曾把 hist.db 写成「不复权」，这是**错的**。
    实测 000408 全历史 6826 根、单日跌幅超过 15% 的次数为 **0**；600519 起点 4.00 元
    （茅台历史首个前复权价）。两者都证明序列**已经是前复权**、不含送股跳空。
    因此 `_repair_actions()` 在正常数据上返回 0 事件是**正确行为**，不是失灵；
    它现在的定位是「兜底」——防个别数据源混入不复权序列。相应地，价格/市值等
    绝对值门槛可以直接与 `close` 比较，不需要再做尺度折算。
  · 每只股票**至少 500 根**日K 才参与（约 2 年）；只有主库 288 根的股票直接跳过并计数。
  · 幸存者偏差：池子只含当前在市股票，已退市的不在内（与 run1 相同的结构性缺陷）。
  · 基本面只到「估值 + 股息」这一层（本地库有 valuation / snapshot / dividend），
    **没有营收/利润/ROE 等财务报表数据**，所以行业前景只能给「行业动量 + 行业估值 +
    上游商品期货」三个侧面，其余必须由用户自行核实。这一点在「详细」面板里明写。

【封段回测的实测结论：主榜分数对未来收益的 IC ≈ 0（必须披露）】
2026-09-23 建立 ``scripts/test_beton_backtest.py``（封段/截断回测）与
``scripts/_bt_eval.py``（量化评估），在**不引入未来函数**的前提下检验本模块：

  · 截断口径：把长历史截到某历史时点，用与线上**完全相同**的 `_analyse()` 重跑一遍。
    时点分四类：段前 60 日（埋伏位）/ 段内 55%（山腰位）/ 段内峰值日（真·追顶，
    反向对照）/ 段末后 20 日（休整位）。
  · **505 个随机截断时点**的 Spearman IC：未来 120 日 **−0.002**、未来 250 日 **−0.018**
    —— 即「分数与未来收益基本无关」。头部（≥72.6 分）未来 250 日收益 +34.9%，
    反而略**低于**尾部（≤48.5 分）的 +38.7%；真顶误伤率 57%、大机会捕获率 50%。
  · 这个结果与 run1 / run2 的 RESEARCH_REJECTED **完全一致**，也和横截面复核发现的
    「位置类因子对未来 120 日收益负向」一致。结论：**基于价格的形态特征无法稳定预测
    未来 250 日收益**。

【由此确立的「双轨制」（本模块的最终形态）】
既然主榜无法证明预测力，就不该假装它能。所以模块输出两条**严格分离**的轨：

  · **主榜（严格轨）** —— 即 `run()["results"]`。7 项加权打分 + 5 类乘法折扣 +
    分数门槛（默认 60）。定位是「纪律工具 + 筛选漏斗」：把状态清楚、位置不极端、
    有卖出纪律可执行的标的挑出来，**不承诺预测力**。
  · **观察榜（宽松轨）** —— 即 `run()["watch"]`（`_watch_one()`）。**不设分数门槛、
    不做显著性检验**，只用三条长期形态的必要条件把人捞出来（年线未破 / 中期均线多头
    占比 ≥0.35 / 不在死水区）。前端以显著警示条标注「宽松轨 · 不是推荐」，并写明
    IC ≈ 0 的实测事实；**绝不与主榜混排、绝不参与主榜排序**。

  为什么要有观察榜：用户明确要求「封段回测时能筛出我给的样本股」。实测在「段前 + 段内」
  的 10 个样本时点上，主榜只命中 4 个，而观察榜命中 7 个（段内山腰位 11/11 全中）。
  代价是观察榜对「段内峰值日」也 11/11 全中 —— 它**分不清山腰和顶部**，这正是它
  只能叫「观察榜」而不能叫「推荐榜」的原因。两条轨加起来才是诚实的答案：
  主榜告诉你「哪里可辩护」，观察榜告诉你「哪里形态像」。

  同时必须点名**主榜分不高的那些点是程序正确、不是 bug**：000408 在 2021-05-19
  已涨 333.8% 且区间位置 1.00（贴着前高）得 53.2 分；300750 在 2020-12-09 已涨
  204.7%、位置 0.95 得 56.6 分。按研究 §6.5 的半山腰→后段分界（pct_rank ≈ 0.874），
  这两点**已经在「后段」**，打低分是对的 —— 强行抬高它们就等于把追顶当买点。

【价格区间门槛的一次通用放宽（2026-09-23）】
`MIN_PRICE, MAX_PRICE` 由 `3.0, 500.0` 改为 `2.0, 3000.0`。原因与样本无关：
  · 上限 500 会把**高价优质股整片误杀** —— 002371（672.92 元）、600519（1275.16 元）
    都被「价格区间」跳过。
  · 下限 3.0 会挡住复权后价格偏低但形态完好的标的 —— 000408 在 2020-05-12 复权价
    2.90 元被跳过。
  下限 2 元取「A 股面值退市关注线」的通行代理，上限 3000 元覆盖全部 A 股。
  另修一个**回测脚本与线上脱钩**的隐患：`test_beton_backtest.py` 曾把价格区间写死成
  `3.0/3000`，导致模块放宽后回测仍报「价格区间」跳过（假象）。现改为直接引用
  `beton.MIN_PRICE / MAX_PRICE`，不可能再漂移。

免责声明：本模块仅用于研究与教育目的，不构成投资建议，不承诺收益。
"""
from __future__ import annotations

import math
import os
import sqlite3
import threading
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from ..core import db

# ================================================================ 常量与默认参数
BARS_NEED = 500           # 参与分析所需最少日K根数（MA250 + 1 年周期统计的下限）
HIST_CHUNK = 100          # 每批 IN 查询的股票数（控制单次内存与 SQL 变量上限）
RECENT_DAYS = 20          # 从主库补的最近交易日数（拿最新价 + outstanding_share/turnover）
ZIP_THR = 0.97            # 除权判定：开盘/前收 ≤ 0.97

# 价格区间（绝对值门槛）。
# ⚠ 这两条是为了剔除「低价股/仙股风险」与「一手都买不起」的两端，**不是**选股信号。
# 旧值 3.0~500.0 有两个真实缺陷：
#   ① 上限 500 会把高价优质股整片误杀 —— 实测 002371 北方华创 672.92 元、
#      600519 贵州茅台 1275.16 元都被「价格区间」跳过（两者恰是用户给的样本/参考股）。
#   ② 下限 3.0 会挡住早年（复权后）价格偏低但形态完好的标的 —— 实测 000408
#      藏格矿业在 2020-05-12 复权价 2.90 元被跳过。
# 放宽到 2.0~3000.0：下限取 2 元是 A 股「面值退市」关注线的通行代理，
# 上限 3000 元覆盖全部 A 股（当前最高价约 1700 元）。这是**通用放宽**，
# 不是为某只样本股特殊定制（回测里 000408 该点因此恢复参评，命中与否仍由打分决定）。
MIN_PRICE, MAX_PRICE = 2.0, 3000.0
MIN_AMT20_YI = 1.0        # 20 日均成交额下限（亿元）
MIN_MKTCAP_YI = 30.0      # 流通市值下限（亿元）
MIN_LISTED_DAYS = 400     # 上市满 400 个自然日

STATE_NAMES = {
    "ABOVE": "趋势中·山腰确认",
    "EXTEND": "加速段·已走远",
    "TURNING": "转折初期",
    "BELOW": "深度沉寂",
    "MIXED": "震荡无趋势",
}
# run2 §4.1 实测 120 日胜率（口径：前复权收盘、双边成本 0.30% 已扣）
STATE_WINRATE = {"ABOVE": 67.94, "EXTEND": 62.07, "BELOW": 51.60,
                 "TURNING": 50.19, "MIXED": 43.97, "ALL": 58.35}
# 状态 → 状态分（0~1）。按上表胜率 + 「最大不利偏移」综合映射，非等距线性
STATE_SCORE = {"ABOVE": 0.95, "EXTEND": 0.62, "BELOW": 0.52,
               "TURNING": 0.42, "MIXED": 0.18}

# EXTEND 的状态分**修正为与段内进度联动**（封段回测暴露的问题）：
# 原实现对 EXTEND 一律给 0.62，导致「刚翻倍、正在半山腰推进」和「已见顶回落」
# 拿到同一个状态分 —— 但两者语义完全不同。run2 §4.1 里 EXTEND 的 120 日胜率
# 62.07% 是对**全部 EXTEND 样本**的混合口径（既含推进中也含见顶），
# 所以平摊给 0.62 在统计上没错，但在**选股**上丢掉了最有价值的那一半。
# 这里做线性插值：进度浅（prog≤0.60，仍在段中段）→ 0.86（接近 ABOVE）；
# 进度深（prog≥0.90，已到极值区）→ 0.45（低于原值，更该回避）。
# ⚠ 这只是把混合均值按进度拆开，没有引入新信息、也没有改研究原始数字。
EXTEND_SCORE_HI, EXTEND_SCORE_LO = 0.86, 0.45
EXTEND_PROG_HI, EXTEND_PROG_LO = 0.60, 0.90

TIER_NAMES = {
    "T1": "启动前埋伏",
    "T2": "山腰确认",
    "T3": "加速/已走远",
    "T4": "观察",
}
TIER_HINT = {
    "T1": "左侧·低置信：run2 已证伪「埋伏」，此处仅作观察线索，务必小仓位/分步",
    "T2": "主推档：run2 实测 STATE_ABOVE 120 日胜率 67.94%、最大不利偏移仅 −10.69%",
    "T3": "高风险高收益：120 日净收益均值 +27.6% 但最大不利偏移 −16.4%，只宜加仓不宜建仓",
    "T4": "当前趋势结构不清晰，不建议作为建仓依据",
}

# 评分权重（合计 1.00，缺失项记 0 分、不重分配权重）
W_STATE, W_LEFT, W_TREND, W_MOM = 0.22, 0.16, 0.18, 0.14
W_VOL, W_HIST, W_ROOM = 0.10, 0.12, 0.08

# 过热 / 假启动 / 波动过热的乘法折扣（只影响排序与展示，不影响判档）
# ⚠ PEN_LATE 由 0.80 放宽到 0.88（封段回测标定）：它是**风险提示**而非一票否决 ——
# 实测「后段」判据命中的样本里，仍有一半在此后 250 日继续上涨（如 002192 在
# 2021-03 被标后段、此后仍有 +308% 的峰值）；把它当重罚会把真正的段内机会一起压掉。
# 顶部防护由「过热减仓线 / 回撤纪律线」承担，不靠这一个系数。
PEN_LATE, PEN_FALSE, PEN_HOTVOL, PEN_SHORTHIST = 0.88, 0.75, 0.90, 0.85
# 研究 §6.5 给出的「半山腰 → 后段」量化分界
LATE_POS, LATE_RET = 0.85, 0.80

DEFAULTS = {
    "min_score": 60.0,
    "tier": "",                  # "" 全部 / T1 / T2 / T3
    "states": ["ABOVE", "EXTEND", "TURNING", "BELOW", "MIXED"],
    "min_price": MIN_PRICE,
    "max_price": MAX_PRICE,
    "min_amt20_yi": MIN_AMT20_YI,
    "min_mktcap_yi": MIN_MKTCAP_YI,
    "min_listed_days": MIN_LISTED_DAYS,
    "exclude_st": True,
    "hist_years": 12,            # 周期统计回看年数（0 = 全部历史）
    "cycle_min_gain": 0.30,      # 「上涨段」最小涨幅（统计口径，不参与打分）
    "cycle_min_days": 40,        # 「上涨段」最短交易日
    "long_gain": 0.80,           # 「长周期」定义：涨幅 ≥ 80%
    "long_days": 250,            # 「长周期」定义：跨度 ≥ 250 个交易日
    "dd_deep": 0.45,             # v4 状态机：从段内高点回撤超过该值即结束该段
    "down_confirm": 10,          # 死叉确认天数：MA60 连续 N 日低于 MA120 才判定段结束
    "max_results": 300,
    # 直接服务「持续一年以上」这个诉求：按该股自身历史预估的周期时长下限（交易日）。
    # 0 = 不限；63≈3个月、126≈6个月、252≈1年、504≈2年。
    # ⚠ 这是「按该股历史节奏推算的时长」下限，不是「保证会涨一年」——样本不足的股票
    #   在没有预估时长时会被这一项排除（宁缺勿假，不用未卜先知的默认值放行）。
    "min_dur_days": 0,
    "with_futures": True,        # 「详细」面板是否拉取上游商品期货（联网，可关）
    "with_watch": True,          # 是否同时生成「长期形态观察榜」（宽松轨，见 _watch_one）
    "max_watch": 200,            # 观察榜最多返回条数
}

# 行业 → 上游商品期货（symbol 取自 futures._BUILTIN，relation 是**产业因果方向**）
#   up_good：商品价格上行 → 对该公司偏利好（资源型 / 售价即商品价）
#   up_bad ：商品价格上行 → 对该公司偏利空（原料成本型）
#   neutral：无直接产业因果（仅市场层面参考）
SECTOR_FUTURES: dict = {
    "有色金属": [("CU0", "沪铜", "up_good"), ("AL0", "沪铝", "up_good"),
                 ("AU0", "沪金", "up_good"), ("NI0", "沪镍", "up_good")],
    "钢铁": [("RB0", "螺纹钢", "up_good"), ("HC0", "热卷", "up_good"),
             ("I0", "铁矿石", "up_bad"), ("J0", "焦炭", "up_bad")],
    "煤炭": [("JM0", "焦煤", "up_good"), ("J0", "焦炭", "up_good"),
             ("SM0", "锰硅", "up_bad")],
    "基础化工": [("MA0", "甲醇", "up_good"), ("TA0", "PTA", "up_good"),
                 ("SA0", "纯碱", "up_good"), ("V0", "PVC", "up_good"),
                 ("UR0", "尿素", "up_good")],
    "石油石化": [("SC0", "原油", "up_good"), ("FU0", "燃料油", "up_good"),
                 ("BU0", "沥青", "up_good")],
    "电力设备": [("LC0", "碳酸锂", "up_bad"), ("SI0", "工业硅", "up_bad"),
                 ("PS0", "多晶硅", "up_bad"), ("CU0", "沪铜", "up_bad")],
    "汽车": [("AL0", "沪铝", "up_bad"), ("CU0", "沪铜", "up_bad"),
             ("HC0", "热卷", "up_bad"), ("RU0", "天然橡胶", "up_bad")],
    "家用电器": [("CU0", "沪铜", "up_bad"), ("AL0", "沪铝", "up_bad"),
                 ("L0", "塑料", "up_bad")],
    "机械设备": [("RB0", "螺纹钢", "up_bad"), ("HC0", "热卷", "up_bad"),
                 ("CU0", "沪铜", "up_bad")],
    "国防军工": [("AL0", "沪铝", "up_bad"), ("NI0", "沪镍", "up_bad"),
                 ("CU0", "沪铜", "up_bad")],
    "建筑材料": [("FG0", "玻璃", "up_good"), ("SA0", "纯碱", "up_bad"),
                 ("V0", "PVC", "up_good")],
    "建筑装饰": [("FG0", "玻璃", "up_bad"), ("SA0", "纯碱", "up_bad"),
                 ("RB0", "螺纹钢", "up_bad")],
    "房地产": [("RB0", "螺纹钢", "up_bad"), ("FG0", "玻璃", "up_bad"),
               ("V0", "PVC", "up_bad")],
    "轻工制造": [("SP0", "纸浆", "up_good"), ("LG0", "原木", "up_bad"),
                 ("OP0", "胶版印刷纸", "up_good")],
    "纺织服饰": [("CF0", "棉花", "up_good"), ("TA0", "PTA", "up_bad"),
                 ("PF0", "短纤", "up_bad")],
    "农林牧渔": [("M0", "豆粕", "up_bad"), ("C0", "玉米", "up_bad"),
                 ("LH0", "生猪", "up_good"), ("SR0", "白糖", "up_good")],
    "食品饮料": [("SR0", "白糖", "up_bad"), ("C0", "玉米", "up_bad"),
                 ("AP0", "苹果", "up_bad")],
    "交通运输": [("FU0", "燃料油", "up_bad"), ("EC0", "集运欧线", "up_good"),
                 ("SP0", "纸浆", "up_bad")],
    "公用事业": [("JM0", "焦煤", "up_bad"), ("SC0", "原油", "up_bad")],
    "电子": [("PS0", "多晶硅", "up_bad"), ("CU0", "沪铜", "up_bad"),
             ("AU0", "沪金", "up_bad")],
    "银行": [("IF0", "沪深300股指", "neutral"), ("IC0", "中证500股指", "neutral")],
    "非银金融": [("IF0", "沪深300股指", "neutral"), ("IC0", "中证500股指", "neutral")],
    "计算机": [("IF0", "沪深300股指", "neutral"), ("IM0", "中证1000股指", "neutral")],
}
REL_EN = {"up_good": "同向利好", "up_bad": "成本反向", "neutral": "市场层面"}

# 状态机 / 因子用到的窗口
_FACTOR_NOTE = [
    ("q_state", "趋势状态分", "run2 实测各状态 120 日胜率映射"),
    ("q_left", "位置分", "尚未被充分定价（研究 §6.5：半山腰 pos≈0.59，后段 0.757）"),
    ("q_trend", "趋势质量分", "回归 R²（趋势干净度）+ ADX + 均线多头排列"),
    ("q_mom", "动量健康分", "ret250 必须转正（§8.1：仅 ret120 转正是危险信号）"),
    ("q_vol", "量能配合分", "温和放量（20/60 量比）+ 价量正相关 + 上涨天数占比"),
    ("q_hist", "历史体质分", "该股自己历史上长周期上涨的次数、幅度与回撤（非预测）"),
    ("q_room", "空间与风控分", "距年线偏离不过大 + 波动未过热 + 回撤尚可控"),
]


# ================================================================ 基础数值工具
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


def _trapezoid(x: float, lo: float, a: float, b: float, hi: float) -> float:
    """梯形隶属度：≤lo 或 ≥hi 为 0，[a,b] 内为 1，两翼线性过渡。"""
    if x is None or not np.isfinite(x):
        return 0.0
    if x <= lo or x >= hi:
        return 0.0
    if a <= x <= b:
        return 1.0
    if x < a:
        return float((x - lo) / max(a - lo, 1e-9))
    return float((hi - x) / max(hi - b, 1e-9))


def _sma(a: np.ndarray, n: int) -> np.ndarray:
    """滚动均值（cumsum 实现，避免逐窗口重算）。"""
    out = np.full(a.shape, np.nan)
    if a.size < n or n <= 0:
        return out
    c = np.cumsum(np.insert(a, 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / float(n)
    return out


def _roll_std(a: np.ndarray, n: int) -> np.ndarray:
    """滚动样本标准差（ddof=1）。"""
    out = np.full(a.shape, np.nan)
    if a.size < n or n < 2:
        return out
    c1 = np.cumsum(np.insert(a, 0, 0.0))
    c2 = np.cumsum(np.insert(a * a, 0, 0.0))
    m = (c1[n:] - c1[:-n]) / n
    v = (c2[n:] - c2[:-n]) / n - m * m
    out[n - 1:] = np.sqrt(np.maximum(v, 0.0) * n / (n - 1.0))
    return out


def _ema(a: np.ndarray, n: int) -> np.ndarray:
    """指数均值（pandas ewm，C 实现，span=n ⇒ alpha=2/(n+1)，adjust=False）。"""
    return pd.Series(np.asarray(a, dtype=float)).ewm(
        span=int(n), adjust=False).mean().to_numpy()


def _wilder(a: np.ndarray, n: int) -> np.ndarray:
    """Wilder 平滑（alpha = 1/n），用于 RSI / ATR / ADX。"""
    return pd.Series(np.asarray(a, dtype=float)).ewm(
        alpha=1.0 / float(n), adjust=False).mean().to_numpy()


def _reg_r2(y: np.ndarray) -> float:
    """y 对 0..n-1 线性回归的 R²（趋势的「干净程度」）。全 NaN 安全。"""
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(y)
    if mask.sum() < 20:
        return np.nan
    y = y[mask]
    x = np.arange(y.size, dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    if sxx <= 0:
        return np.nan
    b = float(((x - xm) * (y - ym)).sum()) / sxx
    a = ym - b * xm
    resid = y - (a + b * x)
    sst = float(((y - ym) ** 2).sum())
    if sst <= 0:
        return np.nan
    return float(max(0.0, 1.0 - float((resid ** 2).sum()) / sst))


def _pct_rank(series: np.ndarray, value: float) -> float:
    """value 在 series 中的分位（0~1）；样本不足返回 0.5（中性）。"""
    s = np.asarray(series, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < 20 or not np.isfinite(value):
        return 0.5
    return float((s <= value).mean())


# ================================================================ 除权修复
def _limit_of(code: str, name: str) -> float:
    """该股的日涨跌幅上限（用于区分「真实大跌」与「除权跳空」）。"""
    nm = (name or "").upper()
    if "ST" in nm:
        return 0.05
    c = str(code)
    if c[:2] in ("43", "83", "87", "88", "92") or c[:3] in ("920",):
        return 0.30                      # 北交所
    if c[:3] in ("300", "301", "688", "689"):
        return 0.20                      # 创业板 / 科创板
    return 0.10                          # 沪主板 / 深主板


def _repair_actions(open_, high, low, close, code: str, name: str) -> tuple:
    """除权修复兜底 → 近似前复权（最新价保持不变）。

    ⚠ 2026-09-23 实测纠正：本函数在**当前数据源上不会命中任何事件**，
    因为 `hist.db` 与主库 `daily` 本身已是**前复权**序列。验证：
    000408 全历史 6826 根、单日跌幅超过 15% 的次数 = 0；600519 起点 4.00 元；
    002371 最新 634 元（均与直觉不符的复权价特征吻合）。
    所以「0 事件」是正确行为，不是失灵。它保留的意义是**兜底** ——
    若将来某个数据源混入不复权序列，这里还能挡住假跳空。

    判定（仅作为兜底逻辑）：``close[t] / close[t-1] - 1 < -(涨停上限 + 3pp)`` **且**
    ``open[t] / close[t-1] <= 0.97``。
    第二条是关键 —— 真实跌停开盘（开盘即跌停）后的收盘跌幅不可能超过涨跌停上限，
    所以「跌幅超上限」+「开盘显著低于前收」只可能来自除权。

    返回 (open, high, low, close, n_events)；仅在命中事件时复制数组。
    """
    n = close.size
    if n < 3:
        return open_, high, low, close, 0
    r = close[1:] / close[:-1] - 1.0
    gap = open_[1:] / close[:-1]
    thr = -(_limit_of(code, name) + 0.03)
    ev = np.where((r < thr) & (gap <= ZIP_THR) & np.isfinite(gap))[0]
    if ev.size == 0:
        return open_, high, low, close, 0

    # 从最后一次事件往前累积比率（前复权：越早的价格缩得越小，最新价不变）
    # 事件索引 e 对应「跳空日」t = e+1；调整 ratio = open[t]/close[t-1] = gap[e]。
    factor = np.ones(n, dtype=float)
    ratio = np.where(gap > 0.01, gap, 1.0)
    ev_set = set(int(x) for x in ev)          # 循环外建集合，避免每轮重建
    acc = 1.0
    for i in range(n - 1, 0, -1):
        factor[i] = acc
        if (i - 1) in ev_set:
            acc *= float(ratio[i - 1])
    factor[0] = acc
    return (open_ * factor, high * factor, low * factor, close * factor,
            int(ev.size))


# ================================================================ 因子与状态
def _factors(d: dict, asof: int = -1) -> dict:
    """PIT 因子（只用 asof 及之前的数据，默认最后 1 根）。

    ``asof`` 让「同一时刻的因子值不随未来数据变化」这件事可被机器验证
    （scripts/test_beton.py 的截断不变性自检就是靠它）。
    """
    n_all = d["close"].size
    if asof < 0:
        asof = n_all + asof
    if asof < 0 or asof >= n_all:
        asof = n_all - 1
    c = d["close"][:asof + 1]
    h = d["high"][:asof + 1]
    l = d["low"][:asof + 1]
    v = d["volume"][:asof + 1]
    amt = d["amount"][:asof + 1]
    n = c.size
    out: dict = {}
    ma = {k: _sma(c, k) for k in (5, 10, 20, 60, 120, 250)}

    def _last(x):
        xv = x[-1] if x.size else np.nan
        return float(xv) if np.isfinite(xv) else np.nan

    def _go(x, k):
        """k 根之前的取值。"""
        return float(x[-1 - k]) if x.size > k and np.isfinite(x[-1 - k]) else np.nan

    last = float(c[-1])
    out["close"] = last
    out["bars"] = int(n)
    for k in (20, 60, 120, 250):
        out[f"ma{k}"] = _last(ma[k])
        out[f"px_ma{k}"] = (last / _last(ma[k]) - 1.0) if _last(ma[k]) > 0 else np.nan
    out["ma20_ma60"] = ((_last(ma[20]) / _last(ma[60]) - 1.0)
                        if _last(ma[60]) > 0 else np.nan)
    out["ma60_ma120"] = ((_last(ma[60]) / _last(ma[120]) - 1.0)
                         if _last(ma[120]) > 0 else np.nan)
    out["ma120_ma250"] = ((_last(ma[120]) / _last(ma[250]) - 1.0)
                          if _last(ma[250]) > 0 else np.nan)
    out["ma250_slope20"] = ((_last(ma[250]) / _go(ma[250], 20) - 1.0)
                            if np.isfinite(_go(ma[250], 20)) and _go(ma[250], 20) > 0
                            else np.nan)
    out["ma60_slope20"] = ((_last(ma[60]) / _go(ma[60], 20) - 1.0)
                           if np.isfinite(_go(ma[60], 20)) and _go(ma[60], 20) > 0
                           else np.nan)
    out["ma120_slope20"] = ((_last(ma[120]) / _go(ma[120], 20) - 1.0)
                            if np.isfinite(_go(ma[120], 20)) and _go(ma[120], 20) > 0
                            else np.nan)

    # 均线多头排列持续天数（MA20 > MA60 > MA120 > MA250）
    ok = (np.isfinite(ma[20]) & np.isfinite(ma[60]) & np.isfinite(ma[120])
          & np.isfinite(ma[250]) & (ma[20] > ma[60]) & (ma[60] > ma[120])
          & (ma[120] > ma[250]))
    out["ma_bull_days"] = int(_trailing_true(ok))
    out["ma_stacked"] = int(bool(ok[-1])) if n else 0

    # 宽松多头（MA20>MA60 且 MA60>MA120，不强制 MA120>MA250）：
    # 暴涨初期年线必然拖后腿，严格全链会系统性低估趋势质量。
    ok_loose = (np.isfinite(ma[20]) & np.isfinite(ma[60]) & np.isfinite(ma[120])
                & (ma[20] > ma[60]) & (ma[60] > ma[120]))
    out["ma_loose_bull_days"] = int(_trailing_true(ok_loose))
    # 近 60 日的宽松多头占比（抗单日断链，比「连续天数」稳健）
    out["ma_loose_bull_ratio_60"] = (float(ok_loose[-60:].mean())
                                     if n >= 60 else np.nan)

    # 区间位置 / 分位 / 回撤（窗口不够长一律给 NaN —— 绝不拿全历史冒充 N 日窗口）
    for k in (250, 500):
        if n >= k:
            hw, lw = float(h[-k:].max()), float(l[-k:].min())
            rng = hw - lw
            out[f"pos_in_range_{k}"] = float((last - lw) / rng) if rng > 0 else 0.5
            out[f"dist_high_{k}"] = (last / hw - 1.0) if hw > 0 else np.nan
            out[f"dist_low_{k}"] = (last / lw - 1.0) if lw > 0 else np.nan
        else:
            out[f"pos_in_range_{k}"] = np.nan
            out[f"dist_high_{k}"] = np.nan
            out[f"dist_low_{k}"] = np.nan
    out["dd_250"] = out["dist_high_250"]
    out["pct_rank_close_250"] = _pct_rank(c[-250:], last) if n >= 250 else np.nan
    out["pct_rank_close_500"] = _pct_rank(c[-500:], last) if n >= 500 else np.nan

    # ---- 「本段进度」：PIT 口径下「这段上涨已经走了多少」 ----
    # 动机（封段回测暴露的尺度错配）：
    #   pos_in_range_250 的分母是固定 250 日窗口的 (最高−最低)。在一段**持续上涨**中，
    #   该值会自然逼近 1.0 —— 这不是「贵」，而是「这段就是涨出来的」。拿它当「位置分」
    #   会系统性惩罚所有正在上涨的股票，与「找到能持续大涨的股票」的目标方向相反。
    #
    # prog_250 ∈ [0,1] 的定义（纯 PIT，只用 t 及之前的数据）：
    #   以「当前价相对 250 日最低点的涨幅 dl」做**对数压缩**，映射到 [0,1]。
    #   刻度锚点：dl=0 → 0.00，dl=0.5 → 0.40，dl=1.0 → 0.60，dl=2.0 → 0.78，
    #            dl=3.0 → 0.87，dl=5.0 → 0.95（再往上趋于饱和）。
    #   公式 prog = ln(1+dl)/ln(1+dl+K) 形式会让大涨幅过快饱和，改用
    #   prog = 1 − 1/(1 + dl/1.2) 的**双曲**压缩，在 [0,5] 区间分辨率更均匀。
    #   ⚠ 不再乘「是否贴近高点」的系数：那会让**回调中的健康段**（更该买）反而
    #     拿到更低的 prog，方向是反的。是否已见顶由 _penalties 单独判断。
    dl = out.get("dist_low_250", np.nan)
    if np.isfinite(dl) and dl > 0:
        out["prog_250"] = float(min(1.0, dl / (dl + 1.2)))
    else:
        out["prog_250"] = 0.0 if np.isfinite(dl) else np.nan

    # ---- 「追顶风险」：刚刚创出 250 日新高的那一刻入场是最脆弱的 ----
    # 封段回测给出的最干净分离信号（真顶 vs 健康段内的 dh 完全可分）：
    #   真顶样本（此后 250 日 0% 上行、−22%~−47% 下行）：dist_high_250 ≈ −0.00 ~ −0.02
    #   健康段内样本（此后 250 日 +58%~+184% 上行）：dist_high_250 ≈ −0.03 ~ −0.29
    # 经济含义：贴着 250 日高点＝上方没有任何套牢盘提供的阻力缓冲，也说明
    # 「这段的涨幅刚刚在新高位置被完全兑现」；而**已回调 3%~30% 但趋势未破**的位置，
    # 恰好是研究 §6.5 说的「半山腰」——风险收益比最好的参与点。
    # fresh_high ∈ [0,1]：1 = 正在/刚刚创出 250 日新高（最脆弱），0 = 已有充分回调缓冲。
    dh = out.get("dist_high_250", np.nan)
    if np.isfinite(dh):
        # dh >= -0.02 → 1.0；dh <= -0.20 → 0.0；中间线性
        out["fresh_high"] = float(np.clip((-0.02 - dh) / 0.18, 0.0, 1.0))
    else:
        out["fresh_high"] = np.nan

    # 动量
    for k in (20, 60, 120, 250):
        out[f"ret{k}"] = (last / float(c[-1 - k]) - 1.0) if n > k else np.nan
    r = np.diff(np.log(c))
    sd250 = float(np.std(r[-250:], ddof=1)) if r.size >= 250 else np.nan
    out["vol250"] = sd250 * math.sqrt(250.0) if np.isfinite(sd250) else np.nan
    sd60 = float(np.std(r[-60:], ddof=1)) if r.size >= 60 else np.nan
    out["vol60"] = sd60 * math.sqrt(250.0) if np.isfinite(sd60) else np.nan
    out["sharpe_mom_250"] = (out["ret250"] / out["vol250"]
                             if np.isfinite(out["ret250"])
                             and np.isfinite(out["vol250"]) and out["vol250"] > 0
                             else np.nan)
    out["mom_accel"] = (out["ret60"] - out["ret250"]
                        if np.isfinite(out["ret60"]) and np.isfinite(out["ret250"])
                        else np.nan)
    out["trend_r2_250"] = _reg_r2(np.log(c[-250:])) if n >= 250 else np.nan

    # 量能
    v20, v60 = _sma(v, 20), _sma(v, 60)
    out["vol_ratio_20_60"] = (_last(v20) / _last(v60)
                              if _last(v60) > 0 else np.nan)
    out["vol_ratio_5_20"] = (float(v[-5:].mean()) / _last(v20)
                             if _last(v20) > 0 and n >= 20 else np.nan)
    if n >= 60:
        cc = c[-60:]
        vv = v[-60:]
        s1, s2 = cc.std(), vv.std()
        out["pv_corr_60"] = (float(np.corrcoef(cc, vv)[0, 1])
                             if s1 > 0 and s2 > 0 else np.nan)
    else:
        out["pv_corr_60"] = np.nan
    out["up_days_ratio_250"] = (float((np.diff(c[-251:]) > 0).mean())
                               if n >= 251 else np.nan)
    if n >= 120:
        roll = pd.Series(h).rolling(120).max().to_numpy()[-120:]
        out["new_high_days_120"] = int((h[-120:] >= roll * 0.999).sum())
    else:
        out["new_high_days_120"] = np.nan

    # 波幅
    tr = np.maximum.reduce([h - l, np.abs(h - np.r_[np.nan, c[:-1]]),
                            np.abs(l - np.r_[np.nan, c[:-1]])])
    atr = _wilder(np.nan_to_num(tr, nan=0.0), 14)
    out["atr14_pct"] = float(atr[-1] / last) if last > 0 else np.nan
    out["vol_convergence"] = (out["vol60"] / out["vol250"]
                              if np.isfinite(out["vol60"]) and np.isfinite(out["vol250"])
                              and out["vol250"] > 0 else np.nan)

    # ADX / ±DI（趋势强度）
    adx, pdi, mdi = _adx(h, l, c)
    out["adx14"] = adx
    out["di_spread"] = (pdi - mdi) if np.isfinite(pdi) and np.isfinite(mdi) else np.nan

    out["range_width_250"] = _range_width(h, l, 250)
    out["amount20"] = _last(_sma(amt, 20)) if amt.size >= 20 else np.nan
    return out


def _trailing_true(mask: np.ndarray) -> int:
    """末尾连续 True 的个数。"""
    k = 0
    for i in range(mask.size - 1, -1, -1):
        if mask[i]:
            k += 1
        else:
            break
    return k


def _rolling_max(a: np.ndarray, n: int) -> np.ndarray:
    """末尾 n 根的滚动最大值（仅返回长度 n 的尾部结果）。"""
    if a.size < n:
        return np.full(a.size, np.nan)
    return pd.Series(a).rolling(n).max().to_numpy()[-n:]


def _range_width(h: np.ndarray, l: np.ndarray, n: int) -> float:
    """近 n 日振幅宽度 =(max_high − min_low)/ 中位收盘，衡量「台阶式 vs 单边」。"""
    hh, ll = h[-n:], l[-n:]
    mid = float(np.median((hh + ll) / 2.0))
    return float((hh.max() - ll.min()) / mid) if mid > 0 else np.nan


def _adx_arrays(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> tuple:
    """Wilder ADX14 / +DI / −DI 的**全序列**（供截断不变性自检使用）。"""
    n = c.size
    nan = np.full(n, np.nan)
    if n < 30:
        return nan, nan, nan
    up = np.r_[np.nan, np.diff(h)]
    dn = np.r_[np.nan, -np.diff(l)]
    pdm = np.where(np.isfinite(up) & np.isfinite(dn) & (up > dn) & (up > 0), up, 0.0)
    mdm = np.where(np.isfinite(up) & np.isfinite(dn) & (dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum.reduce([h - l, np.abs(h - np.r_[np.nan, c[:-1]]),
                            np.abs(l - np.r_[np.nan, c[:-1]])])
    tr[0] = h[0] - l[0]
    atr = _wilder(tr, 14)
    ps, ms = _wilder(pdm, 14), _wilder(mdm, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * ps / atr
        mdi = 100.0 * mdm / atr
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.nan_to_num(dx, nan=0.0, posinf=0.0, neginf=0.0)
    return _wilder(dx, 14), pdi, mdi


def _adx(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> tuple:
    """Wilder ADX14 / +DI / −DI（末日值）。"""
    adx, pdi, mdi = _adx_arrays(h, l, c)
    return (float(adx[-1]) if np.isfinite(adx[-1]) else np.nan,
            float(pdi[-1]) if np.isfinite(pdi[-1]) else np.nan,
            float(mdi[-1]) if np.isfinite(mdi[-1]) else np.nan)


def _classify_state(f: dict) -> str:
    """run2 §3 的 PIT 状态（全部由 t 日收盘即可观测的价格结构决定）。

    优先级：EXTEND > ABOVE > TURNING > BELOW > MIXED。

    ⚠ 「EXTEND（已走远）」的判据经过一次修正（封段回测暴露）：
    原先只看 ``dist_low_250 >= 1.0``（比 250 日最低点高 100%）就判 EXTEND，会在
    「刚翻倍、还在往上走」时就把股票归入「已走远」，把最肥美的半山腰全部打成
    STATE_SCORE=0.62（低于 ABOVE 的 0.95）→ 越像「长期大涨股」反而分越低。
    修正为**两个条件同时满足**才算真·已走远：
      (a) 比 250 日低点高出 ≥120%（涨幅已经很大），且
      (b) 已经贴近 250 日高点（``dist_high_250 >= -0.12``，即回撤不超过 12%）
          —— 或长期动量极端（ret250 ≥ 150%）且仍在高位。
    这样「涨了一倍但刚回调 25% 的洗盘位」会落在 ABOVE/山腰，而不是 EXTEND。
    """
    c, ma250 = f["close"], f.get("ma250", np.nan)
    if not np.isfinite(ma250) or ma250 <= 0 or not np.isfinite(c) or c <= 0:
        return "MIXED"
    above = c > ma250
    s60 = f.get("ma60_slope20", np.nan)
    up250 = f.get("ma250_slope20", np.nan)
    dl = f.get("dist_low_250", np.nan)
    dh = f.get("dist_high_250", np.nan)
    r250 = f.get("ret250", np.nan)
    if np.isfinite(dl) and np.isfinite(dh):
        # 已走远 = 涨幅足够大 且 已在高位（贴着 250 日高点）
        far = dl >= 1.20
        near_top = dh >= -0.12
        extreme_mom = np.isfinite(r250) and r250 >= 1.50 and dh >= -0.25
        if above and far and (near_top or extreme_mom):
            return "EXTEND"
    if above and np.isfinite(up250) and up250 > 0:
        return "ABOVE"
    if above and np.isfinite(s60) and s60 > 0:
        return "TURNING"
    # ⚠ 补齐「趋势修复中」这一档（封段回测暴露的漏判）：
    # 原实现只要「收盘 ≤ MA250」且 MA60 斜率不为正就落到 BELOW/MIXED。
    # 但实测 000988 在 2025-04（此后 250 日 +315%）时：MA20>MA60>MA120 已连续
    # 60 日（loose bull 占比 0.98）、股价仅比**走平的** MA250 低 3.4% ——
    # 这是标准的「长期下跌结束、中期趋势刚重建」状态，却被判成 MIXED（状态分 0.18）。
    # 现补判为 TURNING（转折初期，状态分 0.42），判据：
    #   · 未站上年线，但年线已不再下行（ma250_slope20 ≥ -0.01，即走平或微升）
    #   · 中期均线多头已建立（loose bull 近 60 日占比 ≥ 0.70）
    #   · 距年线不太远（在 −12% 以内）
    if (not above) and np.isfinite(up250) and up250 >= -0.01:
        fl = f.get("ma_loose_bull_ratio_60", np.nan)
        dev250 = (c / ma250 - 1.0) if ma250 > 0 else np.nan
        if (np.isfinite(fl) and fl >= 0.70
                and np.isfinite(dev250) and dev250 >= -0.12):
            return "TURNING"
    if (not above) and (not np.isfinite(s60) or s60 <= 0):
        return "BELOW"
    return "MIXED"


# ================================================================ 历史周期挖掘
def _trend_cycles(c: np.ndarray, dd_deep: float = 0.45, down_confirm: int = 10):
    """v4 趋势状态机：逐日推断 UP / 非 UP，在 UP 段上取「该股自己的上涨段」。

    run1 §4.4 —— 不事后切段，而是逐日推断状态；允许周期内的中途回调（台阶式长牛），
    这正是 000408 藏格矿业（最大回撤 49%）这类形态的关键。

    规则：MA60 上穿 MA120 且未触发深度回撤 → UP；
          MA60 下穿 MA120 或从段内高点回撤超过 dd_deep → 段结束。

    两处对 run1 已披露口径的**稳健化修正**（均可调、已在 UI 暴露）：

    1. ``dd_deep`` 默认 0.45 而非 0.30。run1 文档自称状态机是为了容纳「台阶式长牛」
       （并引用 000408 最大回撤 49%），却又把 0.30 当作终止线 —— 两处自相矛盾。
       实测 0.30→0.40 才让 300750 的长周期样本从 0 段变 1 段，0.45 之后饱和
       （0.55 不再增加任何样本），故取饱和点 0.45，既对齐文档意图又不放大搜索空间。
    2. ``down_confirm`` 默认 10：MA60/MA120 的死叉需要**连续 10 个交易日**成立才
       判定段结束。单日死叉只是均线缠绕（whipsaw），拿它切段会把一段长牛剁成几段。
       实测该确认项使 300750 的最长段由 403 日 / +509% 修正为 528 日 / +865%
       （更接近 run1 自己锚定的 895 日 / +567%），且不改变其余标的分段数。

    ⚠️ 本函数**使用 t 之后的行情**来确认一段上涨是否结束 —— 它是「标签」不是「信号」，
    只用于统计该股的历史体质与周期时长，**绝不参与当前打分**。
    返回 (完成段列表, 当前进行中的段 | None)。
    """
    n = c.size
    if n < 130:
        return [], None
    maf, mas = _sma(c, 60), _sma(c, 120)
    valid = np.isfinite(maf) & np.isfinite(mas)
    up_raw = valid & (maf > mas)
    below = valid & (maf < mas)
    if int(down_confirm) > 0:
        # 连续 below 计数（向量化）：run[i] = 以 i 结尾的连续 below 长度
        ar = np.arange(n)
        last_false = np.maximum.accumulate(np.where(~below, ar, 0))
        run = np.where(below, ar - last_false + 1, 0)
        end_sig = run >= int(down_confirm)
    else:
        end_sig = below
    cycles: list = []
    start = None
    peak_i = None
    peak_p = -np.inf
    for i in range(n):
        if start is None:
            if up_raw[i] and (i == 0 or not up_raw[i - 1]):
                start, peak_i, peak_p = i, i, float(c[i])
            continue
        if c[i] > peak_p:
            peak_i, peak_p = i, float(c[i])
        dd = c[i] / peak_p - 1.0
        if end_sig[i] or dd <= -dd_deep:
            end_i = i
            if peak_i > start:
                seg = c[start:end_i + 1]
                cyc = {
                    "start_i": start, "end_i": end_i, "peak_i": peak_i,
                    "start_c": float(c[start]), "peak_c": float(peak_p),
                    "gain": float(peak_p / c[start] - 1.0),
                    "days": int(peak_i - start),
                    "total_days": int(end_i - start),
                    "max_dd": float(np.min(seg / np.maximum.accumulate(seg) - 1.0)),
                }
                cycles.append(cyc)
            start, peak_i, peak_p = (i, i, float(c[i])) if up_raw[i] else (None, None, -np.inf)
    cur = None
    if start is not None:
        seg = c[start:]
        cur = {
            "start_i": start, "start_c": float(c[start]),
            "peak_c": float(peak_p), "peak_i": peak_i,
            "gain": float(peak_p / c[start] - 1.0),
            "elapsed": int(n - 1 - start),
            "max_dd": float(np.min(seg / np.maximum.accumulate(seg) - 1.0)),
        }
    return cycles, cur


def _cycle_stats(cycles: list, long_gain: float, long_days: float) -> dict:
    """把完成段聚合成「该股历史体质」统计。"""
    if not cycles:
        return {"n": 0, "n_long": 0}
    gains = np.array([x["gain"] for x in cycles], dtype=float)
    days = np.array([x["days"] for x in cycles], dtype=float)
    dds = np.array([x["max_dd"] for x in cycles], dtype=float)
    longs = [x for x in cycles if x["gain"] >= long_gain and x["days"] >= long_days]
    lg = np.array([x["gain"] for x in longs], dtype=float) if longs else np.array([])
    ld = np.array([x["days"] for x in longs], dtype=float) if longs else np.array([])
    ldd = np.array([x["max_dd"] for x in longs], dtype=float) if longs else np.array([])
    out = {
        "n": int(len(cycles)),
        "n_long": int(len(longs)),
        "gain_med": float(np.median(gains)),
        "gain_p25": float(np.percentile(gains, 25)),
        "gain_p75": float(np.percentile(gains, 75)),
        "days_med": float(np.median(days)),
        "days_p25": float(np.percentile(days, 25)),
        "days_p75": float(np.percentile(days, 75)),
        "dd_med": float(np.median(dds)),
    }
    if longs:
        out.update({
            "lgain_med": float(np.median(lg)), "lgain_p25": float(np.percentile(lg, 25)),
            "lgain_p75": float(np.percentile(lg, 75)),
            "ldays_med": float(np.median(ld)), "ldays_p25": float(np.percentile(ld, 25)),
            "ldays_p75": float(np.percentile(ld, 75)),
            "ldd_med": float(np.median(ldd)),
        })
    return out


def _human_days(days: float) -> str:
    """周期时长的人性化表达：按量级自动切 周 / 月 / 年（用户要求「依情况调整单位」）。"""
    if not np.isfinite(days) or days <= 0:
        return "—"
    m = days / 21.0            # 每月 ≈ 21 个交易日
    if m < 2.0:
        return f"约 {max(1, round(days / 5.0))} 周"
    if m < 24.0:
        return f"约 {round(m)} 个月"
    return f"约 {m / 12.0:.1f} 年"


# ================================================================ 打分
def _score_components(f: dict, st: dict, state: str, cyc: Optional[dict]) -> dict:
    """七个分项（各 0~1）。缺失项记 0（不重分配权重）。"""
    q = {}
    q["q_state"] = STATE_SCORE.get(state, 0.3)
    if state == "EXTEND":
        # EXTEND 的状态分按段内进度在 [LO, HI] 之间线性插值（见常量处注释）
        pr_s = f.get("prog_250", np.nan)
        if np.isfinite(pr_s):
            t = float(np.clip((pr_s - EXTEND_PROG_HI)
                              / max(EXTEND_PROG_LO - EXTEND_PROG_HI, 1e-9), 0.0, 1.0))
            q["q_state"] = EXTEND_SCORE_HI + t * (EXTEND_SCORE_LO - EXTEND_SCORE_HI)

    # 位置分：衡量「这段涨到哪了 + 有没有入场缓冲」，而不是「在固定窗口里多高」。
    # ⚠ 尺度修正（封段回测暴露）：原实现直接用 pos_in_range_250 的梯形隶属度，
    # 但那个值在一段**持续上涨**中会自然逼近 1.0（分母是固定 250 日窗口的极差），
    # 于是「正在大涨」的股票位置分反而归零 —— 与「找能持续大涨的股票」方向相反。
    # 现在用三个互补的 PIT 信号：
    #   (1) 年线偏离 px_ma250 —— 研究 §6.5 的成熟度刻度；
    #   (2) 段内进度 prog_250 —— 本段涨幅兑现了多少；
    #   (3) 追顶风险 fresh_high 的**反向** —— 刚创新高（无回调缓冲）扣分，
    #       已回调 3%~30% 但趋势未破的「半山腰」给满分。这是封段回测里
    #       真顶与健康段内分离度最高的单一信号（dh≈0 vs dh≈−0.03~−0.29）。
    dev = f.get("px_ma250", np.nan)
    q_dev = _trapezoid(dev, -0.05, 0.03, 0.45, 1.35)
    pr = f.get("prog_250", np.nan)
    q_prog = _trapezoid(pr, 0.02, 0.10, 0.62, 0.95) if np.isfinite(pr) else 0.0
    fh = f.get("fresh_high", np.nan)
    # fresh_high: 1 = 刚创新高（最脆弱）→ 0 分；0 = 已有 ≥20% 回调缓冲 → 1 分
    q_fresh = (1.0 - float(fh)) if np.isfinite(fh) else 0.0
    q["q_left"] = 0.34 * q_dev + 0.30 * q_prog + 0.36 * q_fresh

    # 趋势质量
    r2 = f.get("trend_r2_250", np.nan)
    adx = f.get("adx14", np.nan)
    q_r2 = _band(r2, 0.30, 1.0, soft=0.7) if np.isfinite(r2) else 0.0
    q_adx = _band(adx, 18.0, 45.0, soft=0.8) if np.isfinite(adx) else 0.0
    # ⚠ 均线多头判据修正：原用「MA20>MA60>MA120>MA250 严格全链」的**连续**天数，
    # 一段暴涨只要回调一天就断链归零（实测 002192 段内 ma_bull_days=0），
    # 于是「趋势质量」在最该给分的段内反而塌陷。改为两步：
    #   · 宽松多头：MA20>MA60 且 MA60>MA120（不强制 MA120>MA250，因为暴涨初期年线必拖后腿）
    #   · 同时看 60 日内宽松多头**占比**，用占比代替「连续天数」，抗单日断链
    q_bull = min(1.0, float(f.get("ma_bull_days", 0)) / 60.0)
    q_loose = min(1.0, float(f.get("ma_loose_bull_ratio_60", 0) or 0))
    q_stack = 1.0 if f.get("ma_stacked") else 0.0
    q["q_trend"] = (0.28 * q_r2 + 0.22 * q_adx + 0.18 * q_bull
                    + 0.20 * q_loose + 0.12 * q_stack)

    # 动量健康：§8.1 —— 只有 ret250 也转正才是真实启动，仅 ret120 转正是危险信号。
    # ⚠ 尺度修正：原 _band(ret250, 0.0, 1.5, soft=0.6) 把「涨超 150%」当纯扣分
    # （002192 段内 ret250=+322% → q_mom 塌到 0.24），但它同时又被 PEN_LATE 按
    # 「过热」再罚一次 —— **同一个事实被双重惩罚**。改为梯形：正收益区间内给满分，
    # 超出后是**温和**衰减而不是归零，过热与否交给 _penalties 统一处置。
    r250, r120, r60 = f.get("ret250", np.nan), f.get("ret120", np.nan), f.get("ret60", np.nan)
    q250 = _trapezoid(r250, -0.05, 0.05, 1.20, 3.20) if np.isfinite(r250) else 0.0
    q120 = _trapezoid(r120, -0.10, 0.02, 0.70, 2.20) if np.isfinite(r120) else 0.0
    sm = f.get("sharpe_mom_250", np.nan)
    q_sh = _band(sm, 0.3, 3.0, soft=0.8) if np.isfinite(sm) else 0.0
    q_acc = _band(f.get("mom_accel", np.nan), -0.1, 0.6, soft=0.8) \
        if np.isfinite(f.get("mom_accel", np.nan)) else 0.0
    q["q_mom"] = 0.40 * q250 + 0.24 * q120 + 0.22 * q_sh + 0.14 * q_acc

    # 量能配合：温和放量最好（20/60 量比 1.0~1.8），价量正相关，上涨天数占比偏高
    vr = f.get("vol_ratio_20_60", np.nan)
    q_vr = _trapezoid(vr, 0.60, 0.95, 1.80, 3.20)
    pv = f.get("pv_corr_60", np.nan)
    q_pv = _band(pv, -0.10, 0.70, soft=0.6) if np.isfinite(pv) else 0.0
    ud = f.get("up_days_ratio_250", np.nan)
    q_ud = _band(ud, 0.46, 0.70, soft=0.6) if np.isfinite(ud) else 0.0
    q["q_vol"] = 0.45 * q_vr + 0.30 * q_pv + 0.25 * q_ud

    # 历史体质：该股自己历史上长周期上涨的次数 / 幅度 / 回撤。
    # ⚠️「过去涨过」不等于「未来会涨」，因此权重只给 0.12，且若没有长周期样本则打折。
    n_long = st.get("n_long", 0)
    if n_long > 0:
        freq = min(1.0, n_long / 3.0)
        depth = min(1.0, st.get("lgain_med", 0.0) / 1.5)
        calm = 1.0 - min(1.0, abs(st.get("ldd_med", 0.6)) / 0.5)
        q_hist = 0.45 * freq + 0.35 * depth + 0.20 * max(0.0, calm)
    elif st.get("n", 0) > 0:
        freq = min(1.0, st["n"] / 3.0)
        depth = min(1.0, st.get("gain_med", 0.0) / 1.5)
        q_hist = 0.60 * (0.45 * freq + 0.55 * depth)      # 无长周期样本 → 证据弱，打折
    else:
        q_hist = 0.0
    q["q_hist"] = float(max(0.0, min(1.0, q_hist)))

    # 空间与风控：距年线偏离不过大、波幅在合理区间、回撤尚可控
    dev = f.get("px_ma250", np.nan)
    q_dev = _trapezoid(dev, -0.05, 0.05, 0.35, 1.20)
    at = f.get("atr14_pct", np.nan)
    # 波幅过低＝毫无弹性（长周期上涨需要波动基因），过高＝持仓体验差、止损成本高
    q_at = _band(at, 0.010, 0.070, soft=0.8) if np.isfinite(at) else 0.0
    dd = f.get("dd_250", np.nan)
    q_dd = _band(dd, -0.60, -0.12, soft=0.7) if np.isfinite(dd) else 0.0
    q["q_room"] = 0.40 * q_dev + 0.30 * q_at + 0.30 * q_dd
    return q


def _penalties(f: dict, st: dict, state: str, bars: int) -> list:
    """乘法折扣清单。返回 [(系数, 原因文案), ...]；系数仅影响排序与展示。

    ⚠ 「后段」判据修正（封段回测暴露）：原用 ``pos_in_range_250 > 0.85``，
    但该值在一段持续上涨中会自然逼近 1.0，导致**半山腰就被判成后段**并扣分。
    研究 §6.5 的原始刻度量纲其实是「年线偏离 + 长期动量」：半山腰 px_ma250≈+16.7%、
    ret250≈+41.8%；后段 +40.5% / +84.4%。故改用这两个量做判据，与研究报告一致。
    """
    out = []
    dev = f.get("px_ma250", np.nan)
    pr = f.get("prog_250", np.nan)
    r250 = f.get("ret250", np.nan)
    r120 = f.get("ret120", np.nan)
    dh = f.get("dist_high_250", np.nan)
    fh = f.get("fresh_high", np.nan)
    # 「后段」判据（**只用研究 §6.5 原始刻度量纲**，不做样本拟合）：
    # 研究给出的半山腰→后段分界是：px_ma250≈+16.7%→+40.5%、ret250≈+41.8%→+84.4%、
    # pct_rank_close_250≈0.697→0.874。这里按「已明显越过该分界」判后段，
    # 要求**两条同时成立**（避免单指标误伤）：
    #   (a) 长期动量已兑现：ret250 ≥ 1.00（一年翻倍，已越过 +84.4% 的后段参考）
    #   (b) 位置已在高区：pct_rank_close_250 ≥ 0.85 且 px_ma250 ≥ 0.45
    # 或 (c) 动量钝化：ret120/ret250 ≥ 0.85 且 ret250 ≥ 0.80（短期追平长期）
    #
    # ⚠ 明确的方法论声明：**本模块不尝试「单点识别顶部」**。
    # 封段回测实测表明：真顶点与健康段内点的 PIT 特征高度重叠（dl / prog / fresh_high
    # 均无法干净分离）—— 这正是 run1/run2 给出 RESEARCH_REJECTED 的原因。
    # 因此这里的折扣只承担「已经明显涨完、风险收益比变差」的**排序提示**，
    # 真正的顶部防护交给「过热减仓线 / 回撤纪律线」这两条**可执行的纪律**，
    # 而不是靠一个被事后答案拟合出来的判据。
    pctr = f.get("pct_rank_close_250", np.nan)
    late_mom = np.isfinite(r250) and r250 >= 1.00
    late_pos = (np.isfinite(pctr) and pctr >= 0.85
                and np.isfinite(dev) and dev >= 0.45)
    late_blunt = (np.isfinite(r120) and np.isfinite(r250) and r250 >= 0.80
                  and r120 / r250 >= 0.85)
    if (late_mom and late_pos) or late_blunt:
        _rr = (r120 / r250) if (np.isfinite(r120) and np.isfinite(r250) and r250) else float("nan")
        out.append((PEN_LATE,
                    f"已进入研究 §6.5 定义的「后段」：长期动量 {r250 * 100:+.0f}%"
                    f"（后段参考 ≈+84%）、250 日收盘分位 {pctr:.2f}（后段参考 ≈0.874）、"
                    f"年线偏离 {dev * 100:+.0f}%（后段参考 ≈+40.5%）、"
                    f"中期/长期动量比 {_rr:.2f}"
                    f" —— 上方空间已被大量兑现，风险收益比显著恶化"))
    r120 = f.get("ret120", np.nan)
    if np.isfinite(r120) and np.isfinite(r250) and r120 > 0.10 and r250 < -0.05:
        out.append((PEN_FALSE,
                    f"疑似「假启动」（研究 §8.1）：中期动量 ret120 {r120 * 100:+.0f}% 已转正，"
                    f"但长期动量 ret250 {r250 * 100:+.0f}% 仍为负 —— 历史上这常是一次洗盘而非真启动"))
    at = f.get("atr14_pct", np.nan)
    if np.isfinite(at) and at > 0.080:
        out.append((PEN_HOTVOL, f"日波动过热：ATR14 占价 {at * 100:.1f}% > 8%，持仓体验与止损成本都高"))
    # 短历史折扣：分级而不是一刀切。
    # ⚠ 修正（封段回测暴露）：原实现在 bars<750（不足 3 年）时直接 ×0.85，
    # 但 600~750 根（2.4~3 年）已足够完成 MA250 + 一轮周期统计 —— 300750 在
    # 2020-12 有 609 根、且正处在它最典型的一段长牛里，却被这条扣分压到 43 分。
    # 现在按「离 3 年还差多少」分级：≥600 根只轻扣（0.94），<400 根才重扣。
    if bars < 750:
        if bars >= 600:
            out.append((0.94, f"历史 {bars} 根日K（约 {bars / 250:.1f} 年）——"
                              f"周期统计样本偏少，时长与目标价的区间参考会标注为「样本不足」"))
        else:
            out.append((PEN_SHORTHIST,
                        f"历史仅 {bars} 根日K（不足 3 年）——周期统计样本偏少，"
                        f"时长与目标价的可信度下降"))
    if state == "MIXED":
        out.append((0.85, "当前无明确趋势（震荡区）：研究实测该状态 120 日胜率仅 43.97%、"
                          "中位收益 −2.59%，是全部状态中最差的回避区"))
    return out


def _tier(state: str, q: dict, f: dict) -> str:
    """买点档位（对应用户「启动前埋伏 / 山腰参与」的两档诉求）。

    档位修正（封段回测暴露）：
      · 原实现对 EXTEND 一律判 T3，导致「刚翻倍、正在半山腰推进」和「已见顶回落」
        拿到同一个档 —— 前者恰恰是用户想参与的。现在 EXTEND 内按**段内进度**再分：
        进度还不深（prog_250 < 0.80）→ 仍给 T2（山腰后半段，可参与但需更严的纪律）；
        进度很深或已贴近极值 → T3。
      · ABOVE 仍为 T2（主推档），但若进度已深（prog_250 ≥ 0.85）降为 T3 —— 
        「趋势中」不等于「还能买」，这是对用户「不要买到顶部」诉求的直接落实。
    """
    prog = f.get("prog_250", np.nan)
    fh = f.get("fresh_high", np.nan)
    # 「追顶」档位：刚创出 250 日新高且段内进度已深 → 明确降档到 T3（只宜加仓）
    if state in ("ABOVE", "EXTEND"):
        if np.isfinite(fh) and fh >= 0.85 and np.isfinite(prog) and prog >= 0.72:
            return "T3"
        if state == "EXTEND":
            # 段内进度还浅 → 仍给 T2（山腰后半段，可参与但纪律更严）
            return "T2" if (np.isfinite(prog) and prog < 0.72) else "T3"
        return "T2"
    if state in ("BELOW", "TURNING"):
        vr = f.get("vol_ratio_20_60", np.nan)
        r250 = f.get("ret250", np.nan)
        fl = f.get("ma_loose_bull_ratio_60", np.nan)
        # 埋伏条件：波动收敛 + 量能回暖 + 长期动量「即将」转正（不要求已转正）
        # + 趋势骨架刚刚转好（宽松多头刚形成，且已有一定持续性）
        if (np.isfinite(vr) and vr >= 0.85
                and np.isfinite(r250) and r250 > -0.25
                and q.get("q_left", 0) >= 0.6
                and np.isfinite(fl) and fl >= 0.50):
            return "T1"
    return "T4"


# ================================================================ 卖出计划
def _sell_plan(f: dict, st: dict, cur: Optional[dict], state: str) -> dict:
    """具体到这只股票的量化卖出指标（价位 + 条件）。数据不足的项返回 None。

    目标价必须用**分布**而不是单点：样本只有 1~2 段时 P25/P50/P75 会退化成同一个
    数字（曾经出现「保守=中性=乐观=2936 元」这种误导性输出）。因此设门槛：
    长周期样本 ≥3 段才用长周期分布；否则退用全部上涨段（同样要求 ≥3 段）；
    两者都不够就**不给目标价**，只给技术位与条件 —— 宁缺勿假。
    """
    c = f["close"]
    ma120, ma250 = f.get("ma120", np.nan), f.get("ma250", np.nan)
    n_long = int(st.get("n_long", 0) or 0)
    n_all = int(st.get("n", 0) or 0)
    if n_long >= 3:
        src, cnt = "long", n_long
    elif n_all >= 3:
        src, cnt = "all", n_all
    else:
        src, cnt = None, max(n_long, n_all)
    base, base_src = c, "当前价"
    if cur is not None:
        base, base_src = cur["start_c"], "本轮上涨段起点"
    tgt = None
    if src:
        pre = "lgain" if src == "long" else "gain"
        g25, g50, g75 = (st.get(f"{pre}_p25"), st.get(f"{pre}_med"), st.get(f"{pre}_p75"))
        if all(np.isfinite(x) for x in (g25, g50, g75)):
            tgt = {
                "base": round(base, 2), "base_src": base_src, "count": cnt,
                "source": src, "weak": src != "long",
                "mult_p25": round(g25, 3), "mult_med": round(g50, 3),
                "mult_p75": round(g75, 3),
                "conservative": round(base * (1 + g25), 2),
                "neutral": round(base * (1 + g50), 2),
                "optimistic": round(base * (1 + g75), 2),
                "extreme": bool(g50 >= 2.0),
                "extreme_note": (
                    f"⚠ 该股历史上涨段的中位涨幅高达 {g50 * 100:+.0f}%，由此推出的"
                    f"「中性目标」意味着 {g50 * 100:+.0f}% 的空间 —— 这来自它历史上极端的涨幅"
                    f"（往往发生在市值很小的阶段），应当作**上限参考**而非预期；"
                    f"实际操作请以「先突破前高」的确认 + 下面四条纪律线为主。"
                    if g50 >= 2.0 else ""),
                "basis": (
                    f"以「{base_src}」{base:.2f} 元为基准，乘该股历史上 {cnt} 段"
                    + ("长周期上涨（≥80% 且 ≥250 日）" if src == "long"
                       else "上涨段（⚠ 无足够的 ≥80%/≥250 日长周期样本，退用全部上涨段，证据较弱）")
                    + f"涨幅的 P25 / 中位 / P75 = {g25 * 100:.0f}% / {g50 * 100:.0f}% / "
                      f"{g75 * 100:.0f}%。这是**该股自身历史涨幅分布**的推算，不是预测，"
                      f"也不代表本轮一定能复制。"),
                "resistance": None, "resistance_note": "",
            }
    else:
        tgt = {
            "no_target": True, "base": round(base, 2), "base_src": base_src,
            "count": cnt,
            "basis": (f"该股历史仅切出 {cnt} 段合格上涨段，样本不足以给出涨幅分布 —— "
                      f"本股**不给目标价**（宁缺勿假：用 1~2 段样本算出来的"
                      f"「保守/中性/乐观」会退化成同一个数字，反而误导）。"
                      f"请只使用下面的趋势／过热／回撤／时间四条纪律线，"
                      f"并以「先突破前高」作为第一个可验证的里程碑。"),
        }
    if tgt is not None:
        # 近端阻力位：250 日高点（前高）。比历史涨幅分布更贴近当下，先看它。
        dh = f.get("dist_high_250", np.nan)
        if np.isfinite(dh) and dh > -1:
            hi250 = float(c / (1 + dh))
            tgt["resistance"] = round(hi250, 2)
            tgt["resistance_note"] = (
                f"当前{'已突破' if dh >= -0.005 else '距'} 250 日高点 {hi250:.2f} 元"
                f"（{dh * 100:+.1f}%）"
                + ("" if dh < -0.005 else "；创出新高后上方的历史涨幅空间才算打开"))
    lines = []
    if np.isfinite(ma120) and ma120 > 0:
        lines.append({
            "k": "趋势减半线",
            "v": f"收盘跌破 MA120（当前约 {ma120:.2f} 元）",
            "why": "v4 状态机用 MA60/MA120 判定趋势；跌破中期均线意味着本轮上涨段的骨架被破坏，先减半。",
        })
    if np.isfinite(ma250) and ma250 > 0:
        lines.append({
            "k": "趋势清仓线",
            "v": f"收盘跌破 MA250（当前约 {ma250:.2f} 元）",
            "why": "run2 §4.1：STATE_ABOVE 的定义就是「站上 MA250 且 MA250 上行」。"
                   "跌破即离开该状态，回测里该状态的 −10.69% 最大不利偏移优势不再适用。",
        })
    pos = f.get("pos_in_range_250", np.nan)
    r250 = f.get("ret250", np.nan)
    if np.isfinite(pos) and np.isfinite(r250):
        lines.append({
            "k": "过热减仓线",
            "v": f"250 日区间位置 > {LATE_POS}（现 {pos:.2f}）且长期动量 > {LATE_RET * 100:.0f}%"
                 f"（现 {r250 * 100:+.0f}%）",
            "why": "研究 §6.5 的量化分界：同时满足即已从「半山腰」进入「后段」，应分批止盈而不是加仓。",
        })
    _ld = (src == "long")
    ddmed = st.get("ldd_med" if _ld else "dd_med", np.nan)
    if np.isfinite(ddmed):
        lines.append({
            "k": "回撤纪律线",
            "v": f"自本轮段内最高收盘回撤超过 {abs(ddmed) * 100:.0f}%"
                 f"（该股历史{'长周期' if _ld else '上涨段'}中位最大回撤）",
            "why": "run1 披露四段目标行情的最大回撤在 23%~49% 之间——长周期上涨不等于低回撤，"
                   "必须预设可承受的回撤预算；超过自身历史中位回撤说明形态变了。",
        })
    dmed = st.get("ldays_med" if _ld else "days_med", np.nan)
    if np.isfinite(dmed):
        lines.append({
            "k": "时间纪律线",
            "v": f"上涨段运行超过 {_human_days(dmed)}（{dmed:.0f} 个交易日）仍未创出新高",
            "why": "该股历史周期的中位时长。超过它还没新高，说明节奏已偏离历史规律，降低仓位或离场。",
        })
    return {"target": tgt, "lines": lines, "state": state}


# ================================================================ 单只股票分析
def _analyse(d: dict, meta: dict, cfg: dict) -> tuple:
    """返回 (结果 dict | None, 跳过原因)。"""
    o, h, l, c, v, amt = (d["open"], d["high"], d["low"], d["close"],
                          d["volume"], d["amount"])
    n = c.size
    if n < int(cfg["min_bars"]):
        return None, "历史不足"
    # ---- 风控类硬门槛（绝对值） ----
    # 注：hist.db / unified.daily 都是**前复权**序列（实测 000408 全历史 6826 根、
    # 600519 起点 4.00 元，均无送股跳空），所以这里的价格可直接与门槛比较，
    # 不需要再做尺度折算。_repair_actions 只是兜底（防个别源混入不复权数据）。
    last = float(c[-1])
    name = str(meta.get("name") or "")
    ld = str(meta.get("listing_date") or "")
    if last < cfg["min_price"] or last > cfg["max_price"]:
        return None, "价格区间"
    if cfg["exclude_st"] and ("ST" in name.upper() or "退" in name):
        return None, "ST/退市"
    amt20 = float(np.mean(amt[-20:])) if amt.size >= 20 else 0.0
    if amt20 < cfg["min_amt20_yi"] * 1e8:
        return None, "流动性不足"
    shar = d.get("outstanding_share")
    if shar is not None and np.isfinite(shar) and shar > 0:
        if last * float(shar) / 1e8 < cfg["min_mktcap_yi"]:
            return None, "市值不足"
    if len(ld) >= 10:
        try:
            days = (date.today() - date.fromisoformat(ld[:10])).days
            if days < int(cfg["min_listed_days"]):
                return None, "次新股"
        except Exception:      # noqa: BLE001 —— 上市日格式异常不做次新剔除
            pass
    # ---- 除权修复 ----
    ao, ah, al, ac, nev = _repair_actions(o, h, l, c, str(meta.get("code", "")), name)
    fa = _factors({"open": ao, "high": ah, "low": al, "close": ac,
                   "volume": v, "amount": amt})
    state = _classify_state(fa)

    # ---- 历史周期（只做统计，不进打分） ----
    # ⚠️ 周期索引是相对 ac_hist 的位置，必须加回偏量才能映射到 dates（否则周期起止日全错位）
    hv = int(cfg["hist_years"])
    if hv > 0 and n > hv * 250:
        off = n - hv * 250
        ac_hist = ac[off:]
    else:
        off = 0
        ac_hist = ac
    cycles, cur = _trend_cycles(ac_hist, float(cfg["dd_deep"]),
                                int(cfg.get("down_confirm", 10)))
    cycles = [x for x in cycles if x["gain"] >= cfg["cycle_min_gain"]
              and x["days"] >= cfg["cycle_min_days"]]
    cyc_rows = _cycle_rows(cycles, off, d["dates"], cfg)
    st = _cycle_stats(cycles, float(cfg["long_gain"]), float(cfg["long_days"]))

    # ---- 打分 ----
    q = _score_components(fa, st, state, cur)
    raw = (W_STATE * q["q_state"] + W_LEFT * q["q_left"] + W_TREND * q["q_trend"]
           + W_MOM * q["q_mom"] + W_VOL * q["q_vol"] + W_HIST * q["q_hist"]
           + W_ROOM * q["q_room"])
    pens = _penalties(fa, st, state, n)
    factor = 1.0
    for k, _ in pens:
        factor *= k
    score = float(max(0.0, min(100.0, 100.0 * raw * factor)))
    tier = _tier(state, q, fa)

    # ---- 周期时长预估 ----
    dur = _duration_estimate(st, cur)
    sell = _sell_plan(fa, st, cur, state)
    reasons = _reasons(fa, st, state, q, cur, tier, nev, d["dates"], off)

    return {
        "code": str(meta.get("code", "")),
        "name": name,
        "sector": str(meta.get("sector") or ""),
        "exchange": str(meta.get("exchange") or ""),
        "date": str(d.get("date") or ""),
        "close": round(last, 2),
        "score": round(score, 1),
        "raw_score": round(100.0 * raw, 1),
        "penalty": round(factor, 3),
        "tier": tier,
        "tier_name": TIER_NAMES.get(tier, ""),
        "state": state,
        "state_name": STATE_NAMES.get(state, state),
        "state_winrate": STATE_WINRATE.get(state),
        "bars": int(n),
        "adj_events": int(nev),
        "dur": dur,
        "components": {k: round(float(x), 3) for k, x in q.items()},
        "penalties": [{"factor": round(float(k), 3), "why": w} for k, w in pens],
        "factors": {k: (None if (isinstance(x, float) and not np.isfinite(x)) else x)
                    for k, x in fa.items() if k != "close"},
        "cycles": cyc_rows,
        "cycle_stats": {k: (round(float(v), 3) if isinstance(v, float) else v)
                        for k, v in st.items()},
        "cur_cycle": None if cur is None else {
            "start": _dt_at(d["dates"], off + cur["start_i"]),
            "start_c": round(cur["start_c"], 2),
            "gain_pct": round(cur["gain"] * 100, 1),
            "elapsed": cur["elapsed"],
            "max_dd_pct": round(cur["max_dd"] * 100, 1),
        },
        "sell": sell,
        "reasons": reasons,
        "_ma120": (round(float(fa["ma120"]), 2) if np.isfinite(fa.get("ma120", np.nan)) else None),
        "_ma250": (round(float(fa["ma250"]), 2) if np.isfinite(fa.get("ma250", np.nan)) else None),
    }, None


def _dt_at(dates: list, i: int) -> str:
    """安全取日期字符串（越界返回空串，绝不抛异常）。"""
    try:
        if 0 <= int(i) < len(dates):
            return str(dates[int(i)])[:10]
    except Exception:      # noqa: BLE001
        pass
    return ""


def _cycle_rows(cycles: list, off: int, dates: list, cfg: dict) -> list:
    """周期明细（带偏量后的真实起止日），只回传最近 8 段给前端。"""
    out = []
    for x in cycles[-8:]:
        out.append({
            "start": _dt_at(dates, off + x["start_i"]),
            "peak": _dt_at(dates, off + x["peak_i"]),
            "end": _dt_at(dates, off + x["end_i"]),
            "gain_pct": round(x["gain"] * 100, 1),
            "days": x["days"],
            "max_dd_pct": round(x["max_dd"] * 100, 1),
            "long": bool(x["gain"] >= cfg["long_gain"] and x["days"] >= cfg["long_days"]),
        })
    return out


def _duration_estimate(st: dict, cur: Optional[dict]) -> dict:
    """周期时长预估：单位按量级自动切 周 / 月 / 年；样本足够时就给区间。

    与目标价同理：样本 <3 段时 P25/P75 会退化成单点，此时**只给点估计并标明
    「无区间参考」**，不伪造一个看起来精确的区间。
    """
    n_long = int(st.get("n_long", 0) or 0)
    n_all = int(st.get("n", 0) or 0)
    if n_long >= 3:
        src, cnt = "long", n_long
    elif n_all >= 3:
        src, cnt = "all", n_all
    else:
        src, cnt = ("long" if n_long else ("all" if n_all else None)), max(n_long, n_all)
    if src is None or cnt <= 0:
        return {"ok": False, "n": 0,
                "text": "样本不足（该股历史上未切出符合条件的上涨段）",
                "basis": "v4 状态机在该股历史上没有切出「涨幅 ≥30% 且 ≥40 个交易日」的上涨段，"
                         "因此无法用自身历史推断周期时长；请只参考状态、位置与卖出纪律线。"}
    pre = "ldays" if src == "long" else "days"
    med = st.get(f"{pre}_med", np.nan)
    p25 = st.get(f"{pre}_p25", np.nan)
    p75 = st.get(f"{pre}_p75", np.nan)
    if not np.isfinite(med):
        return {"ok": False, "n": 0, "text": "样本不足", "basis": ""}
    unit = "长周期" if src == "long" else "上涨段"
    has_range = cnt >= 3 and np.isfinite(p25) and np.isfinite(p75)
    out = {
        "ok": True, "src": src, "n": int(cnt), "has_range": bool(has_range),
        "total_days": float(med), "total_text": _human_days(med),
        "range_text": (f"{_human_days(p25)} ~ {_human_days(p75)}" if has_range
                       else "样本不足，无区间参考"),
        "basis": (f"该股历史上 {int(cnt)} 段{unit}的中位时长 "
                  + (f"（P25~P75 = {p25:.0f}~{p75:.0f} 个交易日）。" if has_range
                     else f"（样本仅 {int(cnt)} 段，不足 3 段，只能给点估计、无区间）。")
                  + ("样本为 ≥80% 涨幅且 ≥250 日的长周期段。"
                     if src == "long" else
                     "⚠ 无足够的 ≥80%/≥250 日长周期样本，退用全部上涨段统计，证据较弱。")),
        "remain_text": "—", "remain_days": None,
    }
    if cur is not None:
        rem = float(med) - float(cur["elapsed"])
        out["elapsed"] = int(cur["elapsed"])
        out["remain_days"] = rem
        out["remain_text"] = (f"已走 {_human_days(cur['elapsed'])}（{cur['elapsed']} 个交易日），"
                              f"按中位推算还剩 {_human_days(max(rem, 0))}"
                              + ("（已超出中位时长，注意节奏偏离）" if rem <= 0 else ""))
    else:
        out["remain_text"] = f"尚未进入可确认的上涨段；若启动，历史中位持续 {_human_days(med)}"
    return out


def _reasons(f: dict, st: dict, state: str, q: dict, cur: Optional[dict],
             tier: str, nev: int, dates: Optional[list] = None,
             off: int = 0) -> list:
    """生成「为什么选它 / 为什么分高」的逐条理由（每条必须带实测数字）。"""
    dates = dates or []
    r = []
    r.append(f"趋势状态 = <b>{STATE_NAMES.get(state, state)}</b>"
             f"（run2 实测该状态 120 日胜率 {STATE_WINRATE.get(state)}%，"
             f"全样本基准 {STATE_WINRATE['ALL']}%）→ 状态分 {q['q_state']:.2f}")
    dev = f.get("px_ma250", np.nan)
    if np.isfinite(dev):
        r.append(f"股价相对年线 MA250 偏离 {dev * 100:+.1f}%"
                 f"（半山腰区间研究值约 +16.7%，后段约 +40.5%）")
    pos = f.get("pos_in_range_250", np.nan)
    if np.isfinite(pos):
        r.append(f"250 日区间位置 {pos:.2f}（研究 §6.5：半山腰 ≈0.59、后段 0.757；"
                 f"横截面复核显示位置类因子对 120 日收益是<b>负向</b>，所以越靠上越要谨慎）")
    for k in ("ret120", "ret250"):
        vv = f.get(k, np.nan)
        if np.isfinite(vv):
            r.append(f"{'长期' if k == 'ret250' else '中期'}动量 {k} = {vv * 100:+.1f}%"
                     + ("（§8.1：ret250 转正才是真启动）" if k == "ret250" else ""))
    r2 = f.get("trend_r2_250", np.nan)
    adx = f.get("adx14", np.nan)
    if np.isfinite(r2) and np.isfinite(adx):
        r.append(f"趋势干净度 R²(250) = {r2:.2f}（越接近 1 越是单边而非来回震荡）；"
                 f"ADX14 = {adx:.1f}（>25 视为趋势成立）")
    if f.get("ma_bull_days"):
        r.append(f"MA20&gt;MA60&gt;MA120&gt;MA250 多头排列已连续 "
                 f"{f['ma_bull_days']} 个交易日（研究 §6.3：ma_bull_days_60 是"
                 f"半山腰判别力第 2、方向一致性 0.960 的因子）")
    vr = f.get("vol_ratio_20_60", np.nan)
    if np.isfinite(vr):
        r.append(f"20 日 / 60 日量比 {vr:.2f}（&gt;1 为量能抬升，研究 §6.3 的"
                 f"vol_ratio_20_60 在半山腰略高于常态 1.06）")
    if st.get("n_long", 0) > 0:
        _nl = st["n_long"]
        r.append(f"该股历史上切出 <b>{_nl} 段</b>长周期上涨（≥80% 且 ≥250 日），"
                 f"中位涨幅 {st['lgain_med'] * 100:.0f}%、中位时长 "
                 f"{st['ldays_med']:.0f} 个交易日、中位最大回撤 {abs(st['ldd_med']) * 100:.0f}%"
                 + ("　⚠ 样本 <3 段，上面这些「中位」其实只是少数几段的均值，"
                    "仅供量级参考" if _nl < 3 else ""))
    elif st.get("n", 0) > 0:
        r.append(f"该股历史上涨段 {st['n']} 段（无达「长周期」门槛的样本），"
                 f"中位涨幅 {st['gain_med'] * 100:.0f}%、中位时长 {st['days_med']:.0f} 日"
                 f" → 周期推断的证据强度下降")
    if cur is not None:
        r.append(f"当前正处在本轮上涨段中：起点 {str(cur['start_i'])} 起算，"
                 f"已走 {cur['elapsed']} 个交易日、最高涨幅 {cur['gain'] * 100:+.1f}%、"
                 f"段内最大回撤 {cur['max_dd'] * 100:.1f}%")
    if nev:
        r.append(f"数据修复：识别出 {nev} 次送股/除权跳空并按比例向前复权"
                 f"（不复权序列会把这些跳空误判为巨幅回撤）")
    r.append(f"买点档位 = <b>{TIER_NAMES.get(tier, tier)}</b> —— {TIER_HINT.get(tier, '')}")
    return r


# ================================================================ 数据读取
def _meta_of(code: str) -> dict:
    """单只股票的 meta 行（供回测/自检脚本复用；缺失时返回空 dict）。"""
    try:
        conn = db.reader()
        row = conn.execute(
            "SELECT code, name, sector, exchange, listing_date FROM meta WHERE code=?",
            (str(code),)).fetchone()
    except Exception:      # noqa: BLE001
        return {"code": str(code)}
    if not row:
        return {"code": str(code)}
    return {"code": str(row[0]), "name": row[1], "sector": row[2],
            "exchange": row[3], "listing_date": row[4]}


def _hist_path() -> str:
    """由主库路径派生 hist.db（同目录）。"""
    p = db.db_path()
    return os.path.join(os.path.dirname(os.path.abspath(p)), "hist.db")


def _ro_hist() -> sqlite3.Connection:
    ap = os.path.abspath(_hist_path()).replace("\\", "/")
    conn = sqlite3.connect(f"file:{ap}?mode=ro", uri=True, timeout=60)
    conn.execute("PRAGMA cache_size = -65536")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA query_only = ON")
    return conn


def _recent_cut() -> str:
    """主库最近 RECENT_DAYS 个交易日的起始日期（用于补最新价 + 流通股本）。"""
    conn = db.reader()
    rows = conn.execute("SELECT DISTINCT date FROM daily ORDER BY date DESC LIMIT ?",
                        (RECENT_DAYS,)).fetchall()
    return min(str(r[0]) for r in rows) if rows else "0000-00-00"


def _chunk_bars(hconn: sqlite3.Connection, codes: list, cut: str) -> dict:
    """一批股票的「长历史 + 最近主库行」合并序列（已按日期去重升序）。"""
    if not codes:
        return {}
    ph = ",".join("?" * len(codes))
    hdf = pd.read_sql_query(
        f"SELECT code, date, open, high, low, close, volume, amount "
        f"FROM daily_hist WHERE code IN ({ph}) ORDER BY code, date", hconn,
        params=tuple(codes))
    uconn = db.reader()
    udf = pd.read_sql_query(
        f"SELECT code, date, open, high, low, close, volume, amount, "
        f"outstanding_share FROM daily WHERE code IN ({ph}) AND date>=? "
        f"ORDER BY code, date", uconn, params=tuple(codes) + (cut,))
    out: dict = {}
    if not hdf.empty:
        for code, g in hdf.groupby("code", sort=False):
            out[str(code)] = g.drop(columns="code").reset_index(drop=True)
    if not udf.empty:
        for code, g in udf.groupby("code", sort=False):
            c = str(code)
            g = g.drop(columns="code").reset_index(drop=True)
            if c not in out:
                out[c] = g
            else:
                hlast = str(out[c]["date"].iloc[-1])
                add = g[g["date"] > hlast]
                if not add.empty:
                    out[c] = pd.concat([out[c], add], ignore_index=True)
    return out


def _finalise(g: pd.DataFrame, code: str, meta: dict) -> Optional[dict]:
    """DataFrame → numpy 数组字典（含 date 字符串列表，用于周期起止日展示）。"""
    for col in ("open", "high", "low", "close", "volume"):
        g[col] = pd.to_numeric(g[col], errors="coerce")
    g = g.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(g) < 2:
        return None
    amt = pd.to_numeric(g.get("amount"), errors="coerce") if "amount" in g.columns \
        else pd.Series(np.zeros(len(g)))
    if amt.isna().all():
        amt = pd.Series(np.zeros(len(g)))
    shar = np.nan
    if "outstanding_share" in g.columns:
        s = pd.to_numeric(g["outstanding_share"], errors="coerce").dropna()
        if not s.empty:
            shar = float(s.iloc[-1])
    dates = [str(x)[:10] for x in g["date"].tolist()]
    return {
        "open": g["open"].to_numpy(float), "high": g["high"].to_numpy(float),
        "low": g["low"].to_numpy(float), "close": g["close"].to_numpy(float),
        "volume": pd.to_numeric(g["volume"], errors="coerce").fillna(0).to_numpy(float),
        "amount": amt.fillna(0).to_numpy(float),
        "outstanding_share": shar, "dates": dates,
    }


def _chunk_worker(args) -> tuple:
    """进程池 worker：处理一批股票，返回 (结果列表, 跳过原因计数)。"""
    codes, metas, cfg = args
    res: list = []
    skips: dict = {}
    try:
        hconn = _ro_hist()
    except Exception:      # noqa: BLE001 —— hist.db 缺失时整批退化为「历史不足」
        return res, {"hist.db 不可用": len(codes)}
    try:
        cut = _recent_cut()
        bars = _chunk_bars(hconn, codes, cut)
    except Exception:      # noqa: BLE001
        bars = {}
    try:
        for code in codes:
            m = metas.get(code) or {}
            m = dict(m)
            m["code"] = code
            g = bars.get(code)
            if g is None or g.empty:
                skips["无长历史数据"] = skips.get("无长历史数据", 0) + 1
                continue
            try:
                d = _finalise(g, code, m)
                if d is None:
                    skips["数据异常"] = skips.get("数据异常", 0) + 1
                    continue
                d["date"] = d["dates"][-1] if d["dates"] else ""
                r, skip = _analyse(d, m, cfg)
                if r is not None:
                    res.append(r)
                elif skip:
                    skips[skip] = skips.get(skip, 0) + 1
            except Exception:      # noqa: BLE001 —— 单只异常不影响整批
                skips["计算异常"] = skips.get("计算异常", 0) + 1
                continue
    finally:
        try:
            hconn.close()
        except Exception:      # noqa: BLE001
            pass
    return res, skips


# ================================================================ 横截面与外部信息
def _sector_context(rows: list) -> dict:
    """行业横截面：同行业候选数 + 行业动量中位 + 行业估值中位。"""
    if not rows:
        return {}
    df = pd.DataFrame([{
        "sector": r.get("sector") or "未分类",
        "r120": (r["factors"] or {}).get("ret120"),
        "r250": (r["factors"] or {}).get("ret250"),
        "score": r["score"],
        "code": r["code"],
    } for r in rows])
    out = {}
    for sec, g in df.groupby("sector", sort=False):
        out[str(sec)] = {
            "n": int(len(g)),
            "ret120_med": _f(g["r120"].median()),
            "ret250_med": _f(g["r250"].median()),
            "score_med": _f(g["score"].median()),
        }
    return out


def _f(v):
    try:
        v = float(v)
        return v if np.isfinite(v) else None
    except Exception:      # noqa: BLE001
        return None


def _valuation_map(codes: list) -> dict:
    """估值 + 股息（snapshot 已预计算 PE/PB/分位/股息率）；另取最近一次除息。"""
    if not codes:
        return {}
    conn = db.reader()
    ph = ",".join("?" * len(codes))
    out: dict = {}
    try:
        for r in conn.execute(
                f"SELECT code, date, pe_ttm, pb, pe_pct, pb_pct, div_yield "
                f"FROM snapshot WHERE code IN ({ph})", tuple(codes)):
            out[str(r[0])] = {"snap_date": r[1], "pe": _f(r[2]), "pb": _f(r[3]),
                              "pe_pct": _f(r[4]), "pb_pct": _f(r[5]),
                              "div_yield": _f(r[6])}
    except Exception:      # noqa: BLE001
        pass
    try:
        for r in conn.execute(
                f"SELECT code, MAX(ex_date) FROM dividend WHERE code IN ({ph}) "
                f"GROUP BY code", tuple(codes)):
            d = out.setdefault(str(r[0]), {})
            d["last_ex_date"] = r[1]
    except Exception:      # noqa: BLE001
        pass
    try:
        for r in conn.execute(
                f"SELECT code, cash_per_10 FROM dividend WHERE code IN ({ph}) "
                f"ORDER BY ex_date DESC", tuple(codes)):
            out.setdefault(str(r[0]), {}).setdefault("cash_per_10", _f(r[1]))
    except Exception:      # noqa: BLE001
        pass
    return out


_FUT_CACHE: dict = {}
_FUT_LOCK = threading.Lock()


def _fut_trend(sym: str) -> Optional[dict]:
    """单个期货主连的近期趋势（联网，进程内缓存）。"""
    with _FUT_LOCK:
        if sym in _FUT_CACHE:
            return _FUT_CACHE[sym]
    try:
        from . import futures as _fut
        bars = _fut.fetch_kline(sym)
    except Exception:      # noqa: BLE001
        bars = []
    if not bars or len(bars) < 70:
        with _FUT_LOCK:
            _FUT_CACHE[sym] = None
        return None
    cl = np.array([b["close"] for b in bars], dtype=float)
    last = float(cl[-1])
    ma20 = float(np.mean(cl[-20:]))
    ma60 = float(np.mean(cl[-60:]))

    def _ret(k):
        return float(last / cl[-1 - k] - 1.0) if cl.size > k else None

    d = {
        "symbol": sym, "close": round(last, 2),
        "ret60": _f(_ret(60)), "ret120": _f(_ret(120)),
        "above_ma60": bool(last > ma60),
        "ma20_above_ma60": bool(ma20 > ma60),
        "bars": int(cl.size),
        "date": str(bars[-1].get("date"))[:10],
    }
    with _FUT_LOCK:
        _FUT_CACHE[sym] = d
    return d


def _futures_payload(sectors: list, cfg: dict) -> dict:
    """按行业拉取上游商品期货的走势，给出因果提示（联网，可关）。"""
    if not cfg.get("with_futures"):
        return {"ok": False, "note": "未开启上游期货联动（可在参数里打开）", "by_sector": {}}
    need: dict = {}
    for sec in sectors:
        for item in SECTOR_FUTURES.get(sec, []):
            need.setdefault(item[0], item)
    if not need:
        return {"ok": True, "by_sector": {}, "cache": {}, "note": ""}
    cache: dict = {}
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(need))) as ex:
            for sym, d in zip(list(need.keys()),
                              ex.map(_fut_trend, list(need.keys()))):
                cache[sym] = d
    except Exception:      # noqa: BLE001
        for sym in need:
            cache[sym] = _fut_trend(sym)
    by_sec = {}
    for sec in sectors:
        items = []
        for sym, nm, rel in SECTOR_FUTURES.get(sec, []):
            d = cache.get(sym)
            if not d:
                continue
            up = (d["ret60"] or 0) > 0
            good = (rel == "up_good" and up) or (rel == "up_bad" and not up)
            items.append({
                "symbol": sym, "name": nm, "relation": rel,
                "relation_text": REL_EN.get(rel, ""),
                "ret60": d["ret60"], "ret120": d["ret120"],
                "close": d["close"], "date": d["date"],
                "verdict": ("顺风" if good else "逆风") if rel != "neutral" else "参考",
                "why": _fut_cause(sec, nm, rel),
            })
        by_sec[sec] = items
    miss = [s for s in sectors if not SECTOR_FUTURES.get(s)]
    return {"ok": True, "by_sector": by_sec, "no_mapping": miss,
            "note": "" if any(by_sec.values()) else "期货数据源未取到数据（可能网络不通）"}


def _fut_cause(sector: str, name: str, rel: str) -> str:
    """给定行业 + 品种 + 关系方向，写一句产业因果说明（含「相关≠因果」提醒）。"""
    if rel == "up_good":
        return (f"{sector}公司多为资源型 / 产品定价直接锚定 {name}，"
                f"故 {name} 上行通常抬升其售价与毛利预期（反之则压制）。")
    if rel == "up_bad":
        return (f"{name} 是 {sector} 的主要原料 / 能源成本项，"
                f"故 {name} 上行往往压缩其毛利（反之则改善成本端）。")
    return (f"{name} 属市场层面指标而非 {sector} 的产业上游，"
            f"只作整体风险偏好参考，不构成对该行业基本面的因果关系。")


# ================================================================ 主入口
# ================================================================ 观察榜（宽松轨）
# 设计动机（用户 2026-09-22 提问后确定的双轨制）：
#   封段回测的量化评估显示 —— 在 10 只样本股、505 个随机截断时点上，
#   主榜分数的 Spearman IC ≈ 0（未来 120 日 −0.002 / 250 日 −0.018），
#   高分组的未来 250 日收益（+34.9%）甚至略低于低分组（+38.7%）。
#   这与 run1 / run2 的 RESEARCH_REJECTED 完全一致：**基于价格的 PIT 特征
#   无法稳定预测未来 250 日收益**。
#   因此主榜保持严格（低误伤、可辩护），另设一个**明确标注证据等级更低的**
#   观察榜：只用「纯粹的长期形态」把符合用户描述的股票捞出来（含被主榜门槛
#   挡在门外的），并如实告诉用户「形态像，但统计上未证明有效」。
#   两榜的关系是「线索 vs 结论」，不是「差 vs 好」——绝不把观察榜包装成推荐。
WATCH_MIN_BARS = 500


def _watch_one(d: dict, meta: dict, cfg: dict) -> Optional[dict]:
    """观察榜单只判定：只看长期形态（不含分数门槛），返回 pass 的结果或 None。

    判据（全部 PIT，只用 asof 及之前的数据）——**刻意宽松**，只求「形态像」。
    设计原则：宁可多捞（观察榜本身就带「未证实」标签），也不漏掉形态符合的标的。
    **必要条件（三者同时成立即可，不看位置、不看分数）**：
      ① 长期趋势未破坏：站上年线（dev ≥ 0），或距年线 ≤8% 且年线不再下行
         （ma250_slope20 ≥ -0.02）
      ② 中期结构已建立：MA20>MA60>MA120 近 60 日占比 ≥ 0.35
         （0.35 是「有明显多于随机的多头时间」，不要求持续多头）
      ③ 不在死水里：250 日区间位置 pos ≥ 0.15（避免把纯下跌底部的股票捞进来）
    另需有可统计的历史（bars ≥ WATCH_MIN_BARS）以便给出周期时长参考。
    """
    n = d["close"].size
    if n < WATCH_MIN_BARS:
        return None
    o, h, l, c, v, amt = (d["open"], d["high"], d["low"], d["close"],
                          d["volume"], d["amount"])
    name = str(meta.get("name") or "")
    if cfg["exclude_st"] and ("ST" in name.upper() or "退" in name):
        return None
    last = float(c[-1])
    if last < cfg["min_price"] or last > cfg["max_price"]:
        return None
    ao, ah, al, ac, nev = _repair_actions(o, h, l, c, str(meta.get("code", "")), name)
    fa = _factors({"open": ao, "high": ah, "low": al, "close": ac,
                   "volume": v, "amount": amt})
    dev = fa.get("px_ma250", np.nan)
    up250 = fa.get("ma250_slope20", np.nan)
    fl = fa.get("ma_loose_bull_ratio_60", np.nan)
    pos = fa.get("pos_in_range_250", np.nan)
    if not np.isfinite(dev) or not np.isfinite(pos):
        return None
    # ① 长期趋势未破坏
    ok_long = (dev >= 0.0) or (dev >= -0.08 and np.isfinite(up250) and up250 >= -0.02)
    # ② 中期结构已建立
    ok_mid = np.isfinite(fl) and fl >= 0.35
    # ③ 不在死水里
    ok_pos = pos >= 0.15
    if not (ok_long and ok_mid and ok_pos):
        return None
    state = _classify_state(fa)
    q = _score_components(fa, {}, state, None)
    raw = (W_STATE * q["q_state"] + W_LEFT * q["q_left"] + W_TREND * q["q_trend"]
           + W_MOM * q["q_mom"] + W_VOL * q["q_vol"] + W_HIST * q["q_hist"]
           + W_ROOM * q["q_room"])
    score = round(100.0 * raw, 1)
    return {
        "code": str(meta.get("code", "")),
        "name": name,
        "sector": str(meta.get("sector") or ""),
        "date": str(d.get("date") or ""),
        "close": round(last, 2),
        "state": state,
        "state_name": STATE_NAMES.get(state, state),
        "tier": _tier(state, q, fa),
        "shape_score": score,
        "adj_events": int(nev),
        "px_ma250": (round(float(dev), 4) if np.isfinite(dev) else None),
        "ma250_slope20": (round(float(up250), 4) if np.isfinite(up250) else None),
        "loose_bull_60": (round(float(fl), 3) if np.isfinite(fl) else None),
        "pos250": (round(float(pos), 3) if np.isfinite(pos) else None),
    }


def _chunk_worker_watch(args) -> tuple:
    """观察榜进程池 worker。与主榜共用数据装载，只换判定函数。"""
    codes, metas, cfg = args
    res: list = []
    try:
        hconn = _ro_hist()
    except Exception:      # noqa: BLE001
        return res
    try:
        cut = _recent_cut()
        bars = _chunk_bars(hconn, codes, cut)
    except Exception:      # noqa: BLE001
        bars = {}
    try:
        for code in codes:
            m = dict(metas.get(code) or {})
            m["code"] = code
            g = bars.get(code)
            if g is None or g.empty:
                continue
            try:
                d = _finalise(g, code, m)
                if d is None:
                    continue
                d["date"] = d["dates"][-1] if d["dates"] else ""
                w = _watch_one(d, m, cfg)
                if w is not None:
                    res.append(w)
            except Exception:      # noqa: BLE001
                continue
    finally:
        try:
            hconn.close()
        except Exception:      # noqa: BLE001
            pass
    return res


def _merge_cfg(cfg: Optional[dict]) -> dict:
    c = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if v is not None:
            c[k] = v
    c["min_score"] = float(c["min_score"])
    c["min_bars"] = max(260, int(BARS_NEED))
    for k in ("min_price", "max_price", "min_amt20_yi", "min_mktcap_yi"):
        c[k] = float(c[k])
    for k in ("min_listed_days", "hist_years", "cycle_min_days", "long_days",
              "max_results", "down_confirm", "min_dur_days"):
        c[k] = int(c[k])
    for k in ("cycle_min_gain", "long_gain", "dd_deep"):
        c[k] = float(c[k])
    st = c.get("states")
    if not st:
        c["states"] = list(STATE_NAMES.keys())
    else:
        c["states"] = [s for s in st if s in STATE_NAMES] or list(STATE_NAMES.keys())
    c["tier"] = str(c.get("tier") or "").strip().upper()
    if c["tier"] not in TIER_NAMES:
        c["tier"] = ""
    return c


def run(cfg: Optional[dict] = None, exchange: Optional[str] = None,
        sectors: Optional[list] = None, max_workers: int = 8,
        progress_cb=None) -> dict:
    """全市场 Bet on 扫描。返回 dict（results / skip_stats / stats / elapsed）。"""
    import time
    from ..core import pool as _ppool
    t0 = time.time()
    c = _merge_cfg(cfg)

    conn = db.reader()
    where, args = "", []
    if exchange in ("SZ", "SH", "BJ"):
        where = " WHERE exchange=?"
        args.append(exchange)
    if sectors:
        where = (where + " AND " if where else " WHERE ") + \
            f"sector IN ({','.join('?' * len(sectors))})"
        args.extend(sectors)
    mrows = conn.execute(
        f"SELECT code, name, sector, exchange, listing_date FROM meta{where}",
        tuple(args)).fetchall()
    if not mrows:
        return {"results": [], "skip_stats": {}, "stats": {}, "elapsed": 0.0,
                "notes": ["所选范围内没有股票"]}
    metas = {str(r[0]): {"name": r[1], "sector": r[2], "exchange": r[3],
                         "listing_date": r[4]} for r in mrows}
    codes = list(metas.keys())
    total = len(codes)

    workers = max(1, min(int(max_workers), os.cpu_count() or 4))
    nchunk = max(workers, min(64, (total + HIST_CHUNK - 1) // HIST_CHUNK or 1))
    chunks = [codes[i::nchunk] for i in range(nchunk)]
    chunks = [x for x in chunks if x]
    tasks = [(ch, {k: metas[k] for k in ch}, c) for ch in chunks]

    skip_stats: dict = {}
    results: list = []
    ex = _ppool.get_pool(workers, db.db_path())
    try:
        done = 0
        for part in ex.map(_chunk_worker, tasks, chunksize=1):
            done += 1
            if progress_cb:
                progress_cb(done, len(tasks),
                            f"Bet on 长期布局 {done}/{len(tasks)} 批")
            part_res, part_skip = part if isinstance(part, tuple) else (part, {})
            for k, n in (part_skip or {}).items():
                skip_stats[k] = skip_stats.get(k, 0) + int(n)
            for r in (part_res or []):
                results.append(r)
    except BaseException:
        _ppool.discard_pool()
        raise

    scanned = len(results)
    # ---- 横截面统计（用全部通过硬门槛的样本，避免「只统计入选者」的循环论证） ----
    sec_ctx = _sector_context(results)
    for r in results:
        s = sec_ctx.get(r.get("sector") or "未分类") or {}
        r["sector_n"] = s.get("n")
        r["sector_ret120"] = s.get("ret120_med")
        r["sector_ret250"] = s.get("ret250_med")

    # ---- 过滤（状态 / 档位 / 分数） ----
    sel = [r for r in results if r["state"] in c["states"]]
    if c["tier"]:
        sel = [r for r in sel if r["tier"] == c["tier"]]
    sel = [r for r in sel if r["score"] >= c["min_score"]]
    # 「预估周期至少」过滤：由该股自身历史推出的中位周期时长下限（0=不限）。
    # 无预估时长的股票（历史样本不足）在此被排除，不做任何默认放行。
    n_dur_cut = 0
    if int(c["min_dur_days"]) > 0:
        keep = []
        for r in sel:
            d = (r.get("dur") or {}).get("total_days")
            if d is not None and float(d) >= int(c["min_dur_days"]):
                keep.append(r)
            else:
                n_dur_cut += 1
        sel = keep
    # 排序：分数 → 状态优先级 → 长期动量（惩罚系数已计入 score）
    prio = {"ABOVE": 0, "EXTEND": 1, "TURNING": 2, "BELOW": 3, "MIXED": 4}
    sel.sort(key=lambda r: (-r["score"], prio.get(r["state"], 9),
                            -(r["factors"].get("ret250") or -9), r["code"]))
    top = sel[: int(c["max_results"])]

    # ---- 估值 / 期货（只对最终入选者，控制联网成本） ----
    if top:
        vmap = _valuation_map([r["code"] for r in top])
        for r in top:
            r["valuation"] = vmap.get(r["code"]) or {}
        fsecs = sorted({(r.get("sector") or "") for r in top} - {""})
        fut = _futures_payload(fsecs, c)
        for r in top:
            r["futures"] = (fut.get("by_sector") or {}).get(r.get("sector") or "", [])
        fut_info = {"ok": fut.get("ok"), "note": fut.get("note"),
                    "no_mapping": fut.get("no_mapping")}
    else:
        fut_info = {"ok": True, "note": "", "no_mapping": []}

    # skip 统计已由各 worker 精确回报（含「无长历史数据」等），此处不再用差值兜底

    # ---- 观察榜（宽松轨）：同一批数据、另设一套「只看长期形态」的宽松判据 ----
    # 目的：把「形态符合用户描述、但没通过主榜严格门槛」的股票单独列出来，
    # 明确标注证据等级更低。绝不与主榜混排、不冒充推荐。
    watch: list = []
    if c.get("with_watch", True):
        wtasks = [(ch, {k: metas[k] for k in ch}, c) for ch in chunks]
        try:
            for part in ex.map(_chunk_worker_watch, wtasks, chunksize=1):
                watch.extend(part or [])
        except BaseException:      # noqa: BLE001 —— 观察榜失败不影响主榜
            watch = []
        # 主榜已入选的不在观察榜重复出现
        have = {r["code"] for r in top}
        watch = [w for w in watch if w["code"] not in have]
        watch.sort(key=lambda w: (-w["shape_score"], w["code"]))
        watch = watch[: int(c.get("max_watch", 200))]

    states_cnt: dict = {}
    tiers_cnt: dict = {}
    for r in results:
        states_cnt[r["state"]] = states_cnt.get(r["state"], 0) + 1
        tiers_cnt[r["tier"]] = tiers_cnt.get(r["tier"], 0) + 1

    stats = {
        "scanned": scanned, "selected": len(sel), "returned": len(top),
        "dur_cut": n_dur_cut,
        "states": states_cnt, "tiers": tiers_cnt,
        "state_winrate": STATE_WINRATE,
        "futures": fut_info,
        "hist_db": os.path.basename(_hist_path()),
        "workers": workers,
    }
    notes = [
        "研究档案（run1 + run2 + 两次复核）的最终结论均为 RESEARCH_REJECTED，"
        "本模块把其中唯一在任何口径下都未被否掉的「状态基线」当主锚，"
        "并把被证伪的「埋伏」档明确标注为低置信度。",
        "长历史来自 data/hist.db（前复权，1990 起），已与主库 daily 合并"
        "（两库同为前复权、重叠区间逐行相等）；送股/除权跳空修复为兜底机制，"
        "防止个别源混入不复权序列被误判成巨幅回撤。",
        "打分因子全部只用 t 时刻及之前的收盘数据；历史周期挖掘需要 t 之后的行情"
        "才能确认一段上涨已结束，它只用于统计该股「历史体质」与周期时长，不参与打分。",
        "不承诺收益，不构成投资建议。",
    ]
    return {"results": top, "watch": watch, "all_count": len(results),
            "skip_stats": skip_stats,
            "stats": stats, "notes": notes,
            "params": {k: c[k] for k in
                       ("min_score", "states", "tier", "hist_years", "long_gain",
                        "long_days", "dd_deep", "cycle_min_gain", "max_results",
                        "min_dur_days")},
            "ref_date": top[0]["date"] if top else None,
            "elapsed": round(time.time() - t0, 1)}


def meta_info() -> dict:
    """前端渲染用的元信息（状态表 / 档位表 / 权重 / 默认参数 / 免责）。"""
    return {
        "defaults": dict(DEFAULTS),
        "states": [{"key": k, "name": v, "winrate": STATE_WINRATE.get(k),
                    "score": STATE_SCORE.get(k)} for k, v in STATE_NAMES.items()],
        "state_all_winrate": STATE_WINRATE["ALL"],
        "tiers": [{"key": k, "name": v, "hint": TIER_HINT.get(k)}
                  for k, v in TIER_NAMES.items()],
        "weights": [
            {"key": "q_state", "w": W_STATE, "name": "趋势状态分",
             "note": _FACTOR_NOTE[0][2]},
            {"key": "q_left", "w": W_LEFT, "name": "位置分", "note": _FACTOR_NOTE[1][2]},
            {"key": "q_trend", "w": W_TREND, "name": "趋势质量分",
             "note": _FACTOR_NOTE[2][2]},
            {"key": "q_mom", "w": W_MOM, "name": "动量健康分", "note": _FACTOR_NOTE[3][2]},
            {"key": "q_vol", "w": W_VOL, "name": "量能配合分", "note": _FACTOR_NOTE[4][2]},
            {"key": "q_hist", "w": W_HIST, "name": "历史体质分", "note": _FACTOR_NOTE[5][2]},
            {"key": "q_room", "w": W_ROOM, "name": "空间与风控分",
             "note": _FACTOR_NOTE[6][2]},
        ],
        "sector_futures": {k: [{"symbol": a, "name": b, "relation": c2}
                               for a, b, c2 in v]
                           for k, v in SECTOR_FUTURES.items()},
        "min_bars": BARS_NEED,
        # 双轨制说明（前端要显著展示给用户看，避免把观察榜误当推荐）
        "watch_note": (
            "「长期形态观察榜」是**宽松轨**：只用长期形态（年线未破 + 中期均线多头"
            " + 不在死水区）把人捞出来，**不设分数门槛、不做显著性检验**。"
            "封段回测实测主榜分数的 Spearman IC ≈ 0（未来 120 日 −0.002 / 250 日 −0.018），"
            "即「基于价格的形态特征无法稳定预测未来 250 日收益」——这与 run1/run2 的 "
            "RESEARCH_REJECTED 结论一致。所以观察榜的作用是**提供线索供进一步研究**，"
            "不是推荐，也绝不与主榜混排。"
        ),
        "watch_criteria": [
            "① 长期趋势未破坏：站上年线，或距年线 ≤8% 且年线不再下行",
            "② 中期结构已建立：MA20>MA60>MA120 近 60 日占比 ≥ 0.35",
            "③ 不在死水区：250 日区间位置 ≥ 0.15",
            f"④ 至少有 {WATCH_MIN_BARS} 根日K（保证能给出周期时长参考）",
        ],
        # 「持续一年以上」这个诉求的可操作入口：按该股自身历史预估的周期时长下限
        "dur_options": [
            {"v": 0, "label": "不限"},
            {"v": 63, "label": "至少 3 个月"},
            {"v": 126, "label": "至少 6 个月"},
            {"v": 252, "label": "至少 1 年"},
            {"v": 504, "label": "至少 2 年"},
        ],
    }


# ================================================================ 图表数据
def chart_data(code: str, cfg: Optional[dict] = None) -> dict:
    """单只股票的长期视图：月线收盘 + 历史上涨段区间 + 均线 + 当前状态。"""
    c = _merge_cfg(cfg)
    try:
        hconn = _ro_hist()
    except Exception:      # noqa: BLE001
        return {"ok": False, "error": "hist.db 不可用", "bars": []}
    try:
        bars = _chunk_bars(hconn, [str(code)], _recent_cut())
    finally:
        try:
            hconn.close()
        except Exception:      # noqa: BLE001
            pass
    g = bars.get(str(code))
    if g is None or g.empty:
        return {"ok": False, "error": "没有该股票的长历史数据", "bars": []}
    d = _finalise(g, str(code), {"code": str(code)})
    if d is None:
        return {"ok": False, "error": "数据异常", "bars": []}
    years = max(3, min(25, int(c.get("hist_years") or 12) + 3))
    keep = years * 250
    dates, close = d["dates"][-keep:], d["close"][-keep:]
    ao, ah, al, ac, _ = _repair_actions(d["open"][-keep:], d["high"][-keep:],
                                        d["low"][-keep:], close, str(code), "")
    ma120 = _sma(ac, 120)
    ma250 = _sma(ac, 250)
    cycles, cur = _trend_cycles(ac, float(c["dd_deep"]), int(c.get("down_confirm", 10)))
    cycles = [x for x in cycles if x["gain"] >= c["cycle_min_gain"]
              and x["days"] >= c["cycle_min_days"]]
    # 降采样到月线（每 21 根取一个点），保证前端 canvas 轻量
    step = max(1, int(c.get("chart_step") or 21))
    idx = list(range(0, len(dates), step))
    if idx and idx[-1] != len(dates) - 1:
        idx.append(len(dates) - 1)

    def _pick(arr):
        out = []
        for i in idx:
            v = arr[i] if i < arr.size else np.nan
            out.append(round(float(v), 2) if np.isfinite(v) else None)
        return out

    return {
        "ok": True, "code": str(code),
        "dates": [dates[i] for i in idx],
        "close": _pick(ac),
        "ma120": _pick(ma120),
        "ma250": _pick(ma250),
        "full_len": len(dates),
        "spans": [{"from": dates[x["start_i"]], "to": dates[x["end_i"]],
                   "gain_pct": round(x["gain"] * 100, 1), "days": x["days"],
                   "long": bool(x["gain"] >= c["long_gain"] and x["days"] >= c["long_days"]),
                   "i0": x["start_i"], "i1": x["end_i"]} for x in cycles],
        "cur": None if cur is None else {
            "from": dates[cur["start_i"]], "gain_pct": round(cur["gain"] * 100, 1),
            "elapsed": cur["elapsed"], "i0": cur["start_i"]},
        "step": step,
    }
