# -*- coding: utf-8 -*-
"""前瞻预警模块 —— 以「提前量」为核心的 A 股预警系统（源自《新功能.txt》方案）。

四重领先机制：
  ① 日历预热     月度主线 + 固定事件日历 + 埋伏窗口（事件前 N 天开始提示）
  ② 产业链传导   上游涨价 → 中游提价 → 业绩兑现存在时滞，监测上游提前布局中游
  ③ 资金领先价格 5日涨幅 + 主力净流入 + 换手未放量 → 疑似资金提前埋伏
  ④ 拥挤度预警   换手分位 / 量能分位 / 20日涨幅 / 资金流出，多项触发 → 高点逃离

模块清单（对应方案 engine/ 六件套）：
  get_calendar_alerts()  规律引擎（日历预热 + 埋伏窗口）
  scan()                 主调度：采集 → 分析 → 汇总（供 /api/alert/run）
  build_report_html()    独立网页报告（深色模板，涨红跌绿）

所有阈值均可通过 cfg 覆盖（前端「⚙ 预警参数」窗口），默认值 = 方案原版设计值。
数据源：akshare 东财接口（联网）；akshare 采用函数内懒加载，避免拖慢服务启动
（与 ant.py 筹码模块同一约定：绝不放模块顶层 / 子进程）。
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import os as _os
import time as _time
from typing import Callable, Optional

import pandas as pd

ProgressCb = Optional[Callable[[float, float, str], None]]

# ============ 网络韧性层：系统代理抖动时自动改直连重试 ============
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
               "ALL_PROXY", "all_proxy")
_PREFER_DIRECT = {"v": False}   # 进程内记忆上次成功的连接模式
_UA_PATCHED = {"v": False}
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _ensure_ua() -> None:
    """东财 WAF 会直接掐断 python-requests 默认 UA 的连接（RemoteDisconnected），
    统一为本进程的所有 requests 请求注入浏览器 UA（仅当调用方未显式指定时）。"""
    if _UA_PATCHED["v"]:
        return
    try:
        import requests as _rq
        _orig = _rq.Session.request

        def _patched(self, method, url, **kw):
            try:
                headers = dict(kw.get("headers") or {})
                if not any(k.lower() == "user-agent" for k in headers):
                    headers["User-Agent"] = _UA
                    kw["headers"] = headers
            except Exception:  # noqa: BLE001 —— 注入失败不改变原行为
                pass
            return _orig(self, method, url, **kw)

        _rq.Session.request = _patched
        _UA_PATCHED["v"] = True
    except Exception:  # noqa: BLE001
        pass


def _net_call(fn, retries: int = 3, pause: float = 0.8):
    """带重试的网络调用。首次按上次成功模式（代理/直连），失败后切换模式重试。
    某些本地代理（如 127.0.0.1:xxxx）会对东财接口断连，直连即可恢复。"""
    last = None
    saved = {k: _os.environ.get(k) for k in _PROXY_KEYS}
    prefer_direct = _PREFER_DIRECT["v"]
    plans = [prefer_direct, not prefer_direct, prefer_direct][:max(int(retries), 1)]

    def _set_env(direct: bool) -> None:
        if direct:
            for k in _PROXY_KEYS:
                _os.environ.pop(k, None)
        else:
            for k, v in saved.items():
                if v is not None:
                    _os.environ[k] = v

    try:
        for i, direct in enumerate(plans):
            _set_env(direct)
            try:
                out = fn()
                if direct != prefer_direct:
                    _PREFER_DIRECT["v"] = direct
                return out
            except Exception as e:  # noqa: BLE001 —— 记录并切换模式重试
                last = e
                if i < len(plans) - 1:
                    _time.sleep(pause)
        raise last
    finally:
        _set_env(False)  # 还原环境变量，避免污染进程内其它库
        for k, v in saved.items():
            if v is not None:
                _os.environ[k] = v


# ============ 可调参数默认值（前端「⚙ 预警参数」窗口可覆盖） ============
DEFAULTS: dict = {
    "calendar": {"horizon_days": 35},          # 日历前瞻天数
    "stealth": {
        "gain_min": 6.0,                       # 异动埋伏：5日板块涨幅下限(%)
        "inflow_min": 3.0,                     # 异动埋伏：5日主力净流入下限(亿)
        "turnover_pct_max": 60.0,              # 异动埋伏：换手分位上限(%)——尚未放量=热度未起
        "hot_gain_min": 20.0,                  # 出货嫌疑：5日板块涨幅下限(%)
        "hot_turnover_pct_min": 80.0,          # 出货嫌疑：换手分位下限(%)
    },
    "cold": {
        "window": 60,                          # 冷门观察窗口(交易日)
        "top_n": 3,                            # 冷门板块 Top N
        "w_amount": 0.35,                      # 冷度权重：成交额占比
        "w_turnover": 0.35,                    # 冷度权重：换手率
        "w_gain": 0.30,                        # 冷度权重：窗口涨幅（替代方案的新闻热度，见说明）
    },
    "national": {"days": 5, "groups": None},   # 国家队：groups 为 None 时四列表全开（etf/huijin/ssf/zhengjin）
    "crowding": {
        "turnover_pct": 0.90,                  # 拥挤：换手率分位阈值（0-1）
        "amount_pct": 0.90,                    # 拥挤：成交额分位阈值（0-1）
        "gain20_min": 15.0,                    # 拥挤：20日涨幅下限(%)（动量代理）
        "trigger_n": 2,                        # 拥挤触发项数（方案原版为3；因融资/估值分位
        #                                        无板块级数据源，改用涨幅+资金流出代理后
        #                                        放宽为 2，可在参数窗口调回 3）
    },
    "foreign": {"top_n": 10},                  # 外资重仓 Top N
}

SECTIONS = ["calendar", "stealth", "cold", "national", "foreign", "crowding", "chains"]
SECTION_LABELS = {
    "calendar": "日历预警", "stealth": "异动侦测", "cold": "冷门板块",
    "national": "国家队资金", "foreign": "外资重仓", "crowding": "拥挤度监控",
    "chains": "传导链与宏观",
}


def _merge_cfg(cfg: Optional[dict]) -> dict:
    base = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for g, kv in (cfg or {}).items():
        if isinstance(kv, dict) and isinstance(base.get(g), dict):
            base[g].update({k: v for k, v in kv.items() if v is not None})
    return base


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _r(x, nd=1):
    v = _f(x)
    return None if v is None else round(v, nd)


def _ak():
    """akshare 懒加载（绝不放模块顶层，避免拖慢启动 / 子进程崩溃）。"""
    import akshare as ak
    return ak


# ==================================================================
# 一、规律引擎（编码自《规律.txt》框架，表格均为数据结构可热更新）
# ==================================================================
MONTHLY_THEMES = {
    1: ["农业政策", "年报预告", "春节前消费"],
    2: ["春节消费数据", "两会预热"],
    3: ["两会政策", "一季报预期"],
    4: ["年报/一季报密集披露", "政治局会议"],
    5: ["五一消费", "基建开工"],
    6: ["半年末流动性", "中报预告"],
    7: ["年中政策定调", "中报行情"],
    8: ["中报密集", "科技新品周期"],
    9: ["军工", "国庆消费预热", "三季报前瞻"],
    10: ["国庆消费数据", "三季报"],
    11: ["双十一", "年报高送转预期"],
    12: ["中央经济工作会议", "供暖", "跨年行情"],
}

# 埋伏窗口表：主题 → (提前起始天数, 提前结束天数)。核心反马后炮机制。
LEAD_WINDOWS = {
    "军工": (30, 10),            # 国庆催化前 30 天进入埋伏窗口
    "国庆消费预热": (25, 8),
    "三季报前瞻": (35, 15),      # 9 月初即提示预增方向埋伏
    "双十一": (40, 15),          # 10 月初提示物流/电商/美妆
    "中央经济工作会议": (25, 5),
    "两会预热": (35, 10),
}

# 主题 → 关联事件关键词（修复方案原稿的主题匹配歧义）
THEME_EVENT_HINT = {
    "军工": "国庆", "国庆消费预热": "国庆", "三季报前瞻": "三季报",
    "双十一": "双十一", "中央经济工作会议": "中央经济工作会议", "两会预热": "两会",
}

# 年度固定事件日历（自动计算距今天数）
FIXED_EVENTS = [
    (3, 5, "全国两会", "全年政策主线定调，两会预热 35 天前启动"),
    (9, 17, "美联储9月议息会议", "关注降息节奏与点阵图，影响美债收益率与外资流向"),
    (9, 18, "日本央行9月议息", "美日货币政策分化，警惕套息平仓引发全球 risk-off"),
    (9, 30, "国庆长假前最后埋伏窗口", "军工/消费/出行应在 9 月中下旬完成布局"),
    (10, 1, "国庆黄金周", "节前埋伏、节后兑现数据"),
    (10, 31, "三季报披露截止", "预增公告通常提前 2-4 周发布"),
    (11, 11, "双十一", "电商/物流/美妆景气催化"),
    (12, 10, "中央经济工作会议（惯例）", "供暖/顺周期/次年主线定调"),
]

# 宏观雷达主题表：对近端快讯做主题归类扫描（有回波才显示，无静态知识卡）
_MACRO_TOPICS = [
    {"主题": "美联储与利率", "关键词": ["美联储", "FOMC", "联邦基金", "点阵图", "鲍威尔", "议息",
                                    "非农", "就业数据", "失业率", "通胀", "CPI", "PCE", "加息", "降息"],
     "关注": "利率路径决定全球流动性与成长股估值"},
    {"主题": "美债与美元", "关键词": ["美债", "十年期美债", "美债收益率", "美元指数", "美元"],
     "关注": "贴现率与外资风险偏好"},
    {"主题": "人民币汇率", "关键词": ["人民币", "离岸人民币", "汇率", "中间价"],
     "关注": "外资流入流出的汇兑视角"},
    {"主题": "日本央行与套息", "关键词": ["日本央行", "日元", "日央行", "套息"],
     "关注": "套息平仓是全球 risk-off 的引信"},
    {"主题": "黄金与大宗", "关键词": ["黄金", "金价", "COMEX", "白银", "原油", "铜价", "大宗商品"],
     "关注": "避险情绪与通胀预期的实物映射"},
]

# 宏观信号词（方向敏感：区分「预期升温」与「预期降温/排除」，避免把历史报道误读为当前预期）
_MACRO_SIGNAL_PATTERNS = [
    ("降息预期升温", "偏宽松", [r"降息预期[^。]{0,6}(升温|增强|强化|加大|扩大)",
                              r"(押注|定价|计价)[^。]{0,8}降息",
                              r"(加大|增强)降息(幅度|押注|预期)", r"降息\s*\d+\s*个基点"]),
    ("降息预期降温", "偏紧缩", [r"降息预期[^。]{0,6}(降温|减弱|回落|降低)",
                              r"排除[^。]{0,6}降息", r"(推迟|暂停|放弃)降息",
                              r"不(再|会)降息", r"降息[^。]{0,6}(落空|渺茫|无望|存疑)"]),
    ("紧缩信号", "偏紧缩", [r"(通胀|非农|就业)[^。]{0,10}(超预期|强劲|反弹|火热)",
                           r"(重启|恢复)加息", r"(维持|更高)[^。]{0,6}利率[^。]{0,6}(更久|高位)"]),
    ("宽松信号", "偏宽松", [r"(经济|就业|非农)[^。]{0,10}(疲软|走弱|不及预期|恶化|难看)",
                           r"(衰退|降息周期|宽松周期)"]),
]
_EASE_PAT = [p for _, d, ps in _MACRO_SIGNAL_PATTERNS if d == "偏宽松" for p in ps]
_HAWK_PAT = [p for _, d, ps in _MACRO_SIGNAL_PATTERNS if d == "偏紧缩" for p in ps]


def _macro_radar() -> tuple[str, list]:
    """宏观雷达：对近端快讯做主题归类扫描 + 方向信号识别（纯新闻驱动，无静态知识卡）。
    每个有回波的主题输出一行动态总结（相关报道 + 方向小结）；无回波不显示。"""
    import re as _re2
    try:
        from .ticker import _news_items
        news = _news_items()[:120]
    except Exception as e:  # noqa: BLE001 —— 新闻源挂掉时明确说明而非静默
        return f"新闻源不可用，宏观雷达扫描跳过（{str(e)[:40]}）", []

    docs = [f"{it.get('title', '')} {it.get('text', '')}" for it in news]
    stamps = [str(it.get('time') or '')[:10] for it in news]
    titles = [str(it.get('title') or '')[:44] for it in news]

    rows: list = []
    dove = hawk = 0
    # —— 全局方向信号（跨主题，方向敏感）——
    for label, direction, patterns in _MACRO_SIGNAL_PATTERNS:
        ev = [f"「{titles[i]}」（{stamps[i]}）" for i, d in enumerate(docs)
              if any(_re2.search(p, d) for p in patterns)]
        if not ev:
            continue
        if direction == "偏宽松":
            dove += len(ev)
        else:
            hawk += len(ev)
        rows.append({"类型": "📡 方向信号", "触发": label, "细节": " ｜ ".join(ev[:3])
                     + (f" 等 {len(ev)} 条" if len(ev) > 3 else ""),
                     "方向": direction, "应对": "以美联储官网/点阵图/发布会实录为准"})

    # —— 主题扫描（有回波才显示）——
    for topic in _MACRO_TOPICS:
        idx = [i for i, d in enumerate(docs)
               if any(kw in d for kw in topic["关键词"])]
        if not idx:
            continue
        e_cnt = sum(1 for i in idx if any(_re2.search(p, docs[i]) for p in _EASE_PAT))
        h_cnt = sum(1 for i in idx if any(_re2.search(p, docs[i]) for p in _HAWK_PAT))
        if e_cnt > h_cnt:
            direction, summary = "偏宽松", f"近端报道偏宽松叙事（{e_cnt}/{len(idx)} 条含宽松信号词）"
        elif h_cnt > e_cnt:
            direction, summary = "偏紧缩", f"近端报道偏紧缩叙事（{h_cnt}/{len(idx)} 条含紧缩信号词）"
        else:
            direction, summary = "中性", f"近端报道 {len(idx)} 条，未见明确方向词"
        ev = " ｜ ".join(f"「{titles[i]}」（{stamps[i]}）" for i in idx[:3])
        rows.append({"类型": "🛰 主题扫描", "触发": topic["主题"],
                     "细节": f"{summary}：{ev}" + (f" 等 {len(idx)} 条" if len(idx) > 3 else ""),
                     "方向": direction, "应对": f"关注点：{topic['关注']}"})

    if not rows:
        return "近端快讯未见宏观主题相关报道——雷达无回波，不给方向结论", []
    if dove == 0 and hawk == 0:
        tail = "方向信号：未检出明确的宽松/紧缩表述"
    elif dove > hawk:
        tail = f"方向信号：偏宽松 {dove} 条 vs 偏紧缩 {hawk} 条（新闻词频粗筛，非利率预测）"
    elif hawk > dove:
        tail = f"方向信号：偏紧缩 {hawk} 条 vs 偏宽松 {dove} 条（新闻词频粗筛，非利率预测）"
    else:
        tail = f"方向信号：宽松与紧缩并存（{dove} vs {hawk}），分歧大——建议人工核对美联储官网与点阵图"
    return tail, rows

# 产业链传导链（上游涨价 → 中游滞后受益；上游板块/中游板块 = 东财行业名，用于实时进展）
# 实战验证案例：2026年钨精矿涨359% → 8月1日京瓷刀具全线提价25% → 中游硬质合金业绩兑现
TRANSMIT_CHAINS = [
    {"上游": "钨精矿", "监测指标": "65%WO均价同比", "上游板块": ["小金属"],
     "中游": ["硬质合金刀具", "PCB钻针"], "中游板块": ["通用设备", "电子元件"],
     "逻辑": "上游资源品暴涨 → 日韩刀具大厂被迫提价 → 中游量价齐升"},
    {"上游": "氧化镨钕", "监测指标": "轻稀土价格同比", "上游板块": ["能源金属"],
     "中游": ["钕铁硼永磁"], "下游": ["人形机器人", "新能源车"],
     "中游板块": ["汽车零部件", "电池"],
     "逻辑": "稀土供给配额收紧+缅甸雨季 → 永磁材料传导"},
    {"上游": "碳酸锂", "监测指标": "现货均价", "上游板块": ["能源金属"],
     "中游": ["电池材料", "储能"], "中游板块": ["电池"],
     "逻辑": "旺季补库 → 中游排产回升"},
    {"上游": "电力/算力", "监测指标": "AI算力订单", "上游板块": ["电力"],
     "中游": ["电网设备", "液冷", "光模块"], "中游板块": ["电网设备", "通信设备", "半导体"],
     "逻辑": "算力扩张的底层约束是电力 → 电力设备订单领先业绩 2-3 个季度"},
]


def get_calendar_alerts(cfg=None, today=None) -> dict:
    """日历预警：未来 N 天事件 + 本月主线 + 处于各主题埋伏窗口的提示。"""
    c = _merge_cfg({"calendar": cfg or {}})["calendar"]
    today = today or _dt.date.today()
    horizon = int(c.get("horizon_days", 35))
    events = []
    for m, d, name, note in FIXED_EVENTS:
        ed = _dt.date(today.year, m, d)
        if ed < today:
            ed = ed.replace(year=today.year + 1)
        delta = (ed - today).days
        events.append({"事件": name, "日期": ed.strftime("%m-%d"),
                       "距今(天)": delta, "备注": note})
    events.sort(key=lambda r: r["距今(天)"])
    alerts = [e for e in events if e["距今(天)"] <= horizon]
    # 埋伏窗口判断
    windows = []
    for theme, (start, end) in LEAD_WINDOWS.items():
        hint = THEME_EVENT_HINT.get(theme, "")
        for m, d, name, _ in FIXED_EVENTS:
            if hint and hint in name:
                ed = _dt.date(today.year, m, d)
                if ed < today:
                    ed = ed.replace(year=today.year + 1)
                delta = (ed - today).days
                if end <= delta <= start:
                    windows.append({"主题": theme, "关联事件": name,
                                    "事件日期": ed.strftime("%m-%d"),
                                    "距事件(天)": delta,
                                    "规律窗口": f"提前{start}~{end}天",
                                    "备注": "处于规律埋伏窗口内，建议分批布局"})
                break
    return {"本月主线": MONTHLY_THEMES.get(today.month, []),
            "日历预警": alerts, "埋伏窗口": windows}


# ==================================================================
# 二、数据采集（东财接口，全部带防御：缺列/缺数据则降级跳过）
# ==================================================================
def _industry_basic() -> pd.DataFrame:
    """行业板块今日行情：板块名称/涨跌幅/换手率/总市值。"""
    return _ak().stock_board_industry_name_em()


def _sector_flow() -> pd.DataFrame:
    """行业资金流 5 日排行：5日涨跌幅 / 5日主力净流入-净额。"""
    return _ak().stock_sector_fund_flow_rank(indicator="5日", sector_type="行业资金流")


def _sector_hist(name: str, days: int) -> Optional[pd.DataFrame]:
    """单板块日线历史（东财）：日期/收盘/成交量/成交额/换手率。"""
    end = _dt.date.today().strftime("%Y%m%d")
    start = (_dt.date.today() - _dt.timedelta(days=int(days) + 10)).strftime("%Y%m%d")
    df = _ak().stock_board_industry_hist_em(
        symbol=name, period="日k", start_date=start, end_date=end, adjust="")
    return df if df is not None and not df.empty else None


def _merge_basic_flow(basic: pd.DataFrame, flow: pd.DataFrame) -> pd.DataFrame:
    keep_b = [c for c in ("板块名称", "涨跌幅", "换手率") if c in basic.columns]
    keep_f = [c for c in ("名称", "5日涨跌幅", "5日主力净流入-净额") if c in flow.columns]
    b = basic[keep_b].copy()
    f = flow[keep_f].copy()
    df = b.merge(f, left_on="板块名称", right_on="名称", how="left")
    if "名称" in df.columns:
        df = df.drop(columns=["名称"])
    return df


def _pctile(hist: pd.Series, cur) -> Optional[float]:
    """cur 在 hist 中的分位（0-100）。样本 < 20 返回 None。"""
    s = pd.to_numeric(hist, errors="coerce").dropna()
    if len(s) < 20:
        return None
    cur = _f(cur)
    if cur is None:
        return None
    return round(float((s < cur).mean() * 100), 1)


def _flow5_of(merged: pd.DataFrame, name: str) -> Optional[float]:
    """板块 5 日主力净流入（亿元）。"""
    row = merged.loc[merged["板块名称"] == name]
    if row.empty:
        return None
    return _r(_f(row.iloc[0].get("5日主力净流入-净额")) / 1e8 if _f(row.iloc[0].get("5日主力净流入-净额")) is not None else None, 2)


# ==================================================================
# 三、六大功能模块
# ==================================================================
def _stealth_scan(merged: pd.DataFrame, c: dict, hist_get, notes: list):
    """异动板块侦测：疑似资金提前埋伏 / 高位借利好出货。"""
    stealth, selling = [], []
    if "5日涨跌幅" not in merged.columns or "5日主力净流入-净额" not in merged.columns:
        notes.append("资金流接口缺 5 日列，异动侦测已跳过")
        return stealth, selling
    sc = c["stealth"]
    for _, r in merged.iterrows():
        name = r.get("板块名称")
        g5 = _f(r.get("5日涨跌幅"))
        f5 = _f(r.get("5日主力净流入-净额"))
        if name is None or g5 is None or f5 is None:
            continue
        f5e = f5 / 1e8
        # ① 涨了 + 钱先动 + 尚未放量（价格涨了但热度未起 → 可疑的提前埋伏）
        if g5 > sc["gain_min"] and f5e > sc["inflow_min"]:
            hist = hist_get(name)
            t_pct = None
            if hist is not None and "换手率" in hist.columns and len(hist):
                t_pct = _pctile(hist["换手率"].iloc[:-1], hist["换手率"].iloc[-1])
            if t_pct is None or t_pct <= sc["turnover_pct_max"]:
                stealth.append({"板块": name, "5日涨幅%": _r(g5),
                                "5日主力净流入(亿)": _r(f5e, 2),
                                "换手分位%": t_pct,
                                "判定": "疑似资金提前埋伏（无公开催化、热度未起），可小仓位跟随并设止损"})
        # ② 高位放量 + 资金流出 → 警惕借利好出货
        if g5 > sc["hot_gain_min"] and f5e < 0:
            hist = hist_get(name)
            t_pct = None
            if hist is not None and "换手率" in hist.columns and len(hist):
                t_pct = _pctile(hist["换手率"].iloc[:-1], hist["换手率"].iloc[-1])
            if t_pct is None or t_pct >= sc["hot_turnover_pct_min"]:
                selling.append({"板块": name, "5日涨幅%": _r(g5),
                                "5日主力净流入(亿)": _r(f5e, 2),
                                "换手分位%": t_pct,
                                "判定": "高位放量+利好轰炸+资金流出，警惕借利好出货"})
    stealth.sort(key=lambda x: -(x["5日主力净流入(亿)"] or 0))
    selling.sort(key=lambda x: -(x["5日涨幅%"] or 0))
    return stealth, selling


def _cold_scan(hist_all: dict, c: dict, notes: list) -> list:
    """长期最冷门板块 Top N：冷度 = w1×成交额占比分位 + w2×换手率分位 + w3×涨幅分位。"""
    cc = c["cold"]
    window = int(cc["window"])
    rows = []
    for name, df in hist_all.items():
        if df is None or len(df) < 10:
            continue
        d = df.tail(window)
        g0 = _f(d["收盘"].iloc[0])
        rows.append({
            "板块": name,
            "额占比": _f(d["成交额"].sum()),
            "换手": _f(pd.to_numeric(d["换手率"], errors="coerce").mean()),
            "涨幅": (_f(d["收盘"].iloc[-1]) / g0 - 1) * 100 if g0 and g0 != 0 else None,
        })
    if len(rows) < 5:
        notes.append("冷门板块样本不足，已跳过")
        return []
    s = pd.DataFrame(rows)
    total = s["额占比"].sum() or 1.0
    s["成交额占比%"] = s["额占比"] / total * 100
    s["窗口换手%"] = s["换手"]
    s["窗口涨幅%"] = s["涨幅"]

    def _pr(col: str) -> pd.Series:
        """分位（0~1，越低越冷门）。缺失记 0.5 = 中性。

        原来直接 rank(pct=True)：某板块「期初价缺失」→ 窗口涨幅% 为 None → 该行
        加权和为 NaN。实测 pandas `nsmallest` 把 NaN 当作**最小值排到最后**，于是：
          · 冷度得分显示为空（_r(NaN)→None）；
          · 该板块被当成"最冷"垫底；当板块总数 > top_n 时直接被截掉 ——
            即"最冷门 Top N"里反而看不见它，且无任何提示。
        改为中性 0.5 后该板块按另两个维度正常参与排名，缺失信息只影响一个维度，
        并在 notes 里说明有几个板块受影响（可解释、不静默）。"""
        return pd.to_numeric(s[col], errors="coerce").rank(pct=True).fillna(0.5)

    s["冷度得分"] = (float(cc["w_amount"]) * _pr("成交额占比%")
                    + float(cc["w_turnover"]) * _pr("窗口换手%")
                    + float(cc["w_gain"]) * _pr("窗口涨幅%"))
    _n_gain_miss = int(pd.to_numeric(s["窗口涨幅%"], errors="coerce").isna().sum())
    if _n_gain_miss:
        notes.append(f"{_n_gain_miss} 个板块缺期初价，其涨幅维度按中性计分")
    # 分位升序：分位越低越冷门
    s = s.nsmallest(int(cc["top_n"]), "冷度得分")
    return [{"板块": r["板块"],
             "冷度得分": _r(r["冷度得分"], 3),
             "成交额占比%": _r(r["成交额占比%"], 2),
             f"{window}日换手%": _r(r["窗口换手%"], 2),
             f"{window}日涨幅%": _r(r["窗口涨幅%"], 2),
             "提示": "极度冷门——关注政策催化与均值回归，埋伏成本低但需耐心"}
            for _, r in s.iterrows()]


def _crowding_scan(hist_all: dict, merged: pd.DataFrame, c: dict) -> list:
    """板块拥挤度监控（高点逃离核心）。"""
    cc = c["crowding"]
    rows = []
    for name, df in hist_all.items():
        if df is None or len(df) < 30:
            continue
        last = df.iloc[-1]
        t_pct = _pctile(df["换手率"].iloc[:-1], last["换手率"])
        a_pct = _pctile(df["成交额"].iloc[:-1], last["成交额"])
        g20 = None
        if len(df) >= 21:
            c0 = _f(df["收盘"].iloc[-21])
            c1 = _f(last["收盘"])
            if c0 and c0 != 0 and c1:
                g20 = (c1 / c0 - 1) * 100
        flow5 = _flow5_of(merged, name)
        n = 0
        if t_pct is not None and t_pct >= float(cc["turnover_pct"]) * 100:
            n += 1
        if a_pct is not None and a_pct >= float(cc["amount_pct"]) * 100:
            n += 1
        if g20 is not None and g20 >= float(cc["gain20_min"]):
            n += 1
        if flow5 is not None and flow5 < 0:
            n += 1
        if n >= int(cc["trigger_n"]):
            rows.append({"板块": name, "触发项数": n,
                         "换手分位%": t_pct, "量能分位%": a_pct,
                         "20日涨幅%": _r(g20), "5日主力净流入(亿)": flow5,
                         "判定": "拥挤过热，提示分批止盈（参考2026年7月科技拥挤出清教训）"})
    rows.sort(key=lambda r: -r["触发项数"])
    return rows


# ============ 国家队资金：前十大股东名单口径（ETF/汇金/社保/证金 四列表） ============
_NT_MEM: dict = {"key": None, "df": None}   # 报告期级内存缓存（磁盘缓存见 _fetch_top10_holders）

_NT_GROUPS = [   # (key, 标题, 识别函数：股东名称 → bool)
    ("etf", "国家队 ETF 持仓（宽基）", lambda n: "ETF" in n and "联接" not in n),
    ("huijin", "中央汇金", lambda n: "汇金" in n),
    ("ssf", "社保基金", lambda n: ("社保" in n) or ("社会保障" in n) or ("养老" in n)),
    ("zhengjin", "中国证金", lambda n: "证券金融" in n),
]
_NT_ETF_WIDE = ("沪深300", "上证50", "中证500", "中证1000", "A500")   # 国家队常用宽基关键词
_NT_ETF_LIST = [   # 国家队（汇金/证金）常增持的宽基 ETF 白名单（基金代码, 名称）
    ("510300", "华泰柏瑞沪深300ETF"), ("510050", "华夏上证50ETF"),
    ("510310", "易方达沪深300ETF"), ("510330", "华夏沪深300ETF"),
    ("159919", "嘉实沪深300ETF"), ("510500", "南方中证500ETF"),
    ("512100", "南方中证1000ETF"), ("563300", "华泰柏瑞中证A500ETF"),
]


def _fetch_etf_holdings(end_date: str, prog: Callable, step: float,
                        total_steps: float, notes: list) -> list:
    """国家队宽基 ETF 的个股持仓聚合（基金季报口径：HOLDER_CODE=基金代码直查，单只 1 页秒回）。
    输出：被这些 ETF 持有的个股列表（按合计持股市值降序，Top 50）。"""
    import json as _json
    import requests

    root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    cache = _os.path.join(root, "data", f"etf_holdings_{end_date}.json")
    if _os.path.exists(cache) and _time.time() - _os.path.getmtime(cache) < 30 * 86400:
        try:
            with open(cache, encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    agg: dict = {}
    for i, (code, name) in enumerate(_NT_ETF_LIST):
        prog(step + i / len(_NT_ETF_LIST) * 0.5, total_steps, f"国家队 ETF 持仓 {name}")
        params = {
            "sortColumns": "SECURITY_CODE", "sortTypes": "-1", "pageSize": "500", "pageNumber": "1",
            "reportName": "RPT_MAINDATA_MAIN_POSITIONDETAILS", "columns": "ALL", "quoteColumns": "",
            "filter": f'(HOLDER_CODE="{code}")(REPORT_DATE=\'{end_date}\')',
            "source": "WEB", "client": "WEB",
        }
        try:
            def fn():
                r = requests.get(url, params=params, headers={"User-Agent": _UA}, timeout=25)
                return r.json()
            j = _net_call(fn)
        except Exception:  # 单只失败不影响整体
            notes.append(f"{name} 持仓拉取失败，已跳过")
            continue
        if not j.get("result"):
            continue
        for d in j["result"]["data"]:
            sc = d.get("SECURITY_CODE")
            if not sc:
                continue
            cap = pd.to_numeric(d.get("HOLD_MARKET_CAP"), errors="coerce")
            a = agg.setdefault(sc, {"code": sc, "name": str(d.get("SECURITY_NAME_ABBR") or ""),
                                    "n": 0, "cap": 0.0, "detail": []})
            a["n"] += 1
            a["cap"] += float(cap) if pd.notna(cap) else 0.0
            a["detail"].append(f"{name} {cap / 1e8:.1f}亿" if pd.notna(cap) else name)
    agg = sorted(agg.values(), key=lambda x: -x["cap"])[:50]
    out = [{"代码": a["code"], "简称": a["name"], "命中ETF只数": a["n"],
            "合计持仓市值(亿)": round(a["cap"] / 1e8, 1),
            "持有ETF明细": "；".join(a["detail"][:3]) + ("…" if len(a["detail"]) > 3 else "")}
           for a in agg]
    try:
        with open(cache, "w", encoding="utf-8") as f:
            _json.dump(out, f, ensure_ascii=False)
    except Exception:
        pass
    return out


def _latest_report_date() -> str:
    """最近一个已完成披露的报告期（Q1→4/30、H1→8/31、Q3→10/31、年报→次年4/30）。"""
    today = _dt.date.today()
    best = None
    for y in (today.year, today.year - 1):
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            rd = _dt.date(y, m, d)
            deadline = {3: _dt.date(y, 4, 30), 6: _dt.date(y, 8, 31),
                        9: _dt.date(y, 10, 31), 12: _dt.date(y + 1, 4, 30)}[m]
            if deadline <= today and (best is None or rd > best):
                best = rd
    return best.strftime("%Y-%m-%d") if best else f"{today.year - 1}-09-30"


def _fetch_top10_holders(end_date: str, prog: Callable, step: float,
                         total_steps: float, notes: list) -> Optional[pd.DataFrame]:
    """全市场十大股东明细（东财 datacenter 报表，持股≥1000 万股口径以压缩行数）。
    报告期级磁盘缓存 data/gdfx_holders_<date>.csv（30 天有效）：一个季度只联网拉一次。"""
    import requests

    root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    cache = _os.path.join(root, "data", f"gdfx_holders_{end_date}.csv")
    if _os.path.exists(cache) and _time.time() - _os.path.getmtime(cache) < 30 * 86400:
        try:
            return pd.read_csv(cache, dtype={"SECURITY_CODE": str})
        except Exception:  # 缓存损坏则重拉
            pass
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    base = {
        "sortColumns": "NOTICE_DATE,SECURITY_CODE,RANK", "sortTypes": "-1,1,1",
        "pageSize": "500", "pageNumber": "1",
        "reportName": "RPT_CUSTOM_DMSK_HOLDERS_JOIN_HOLDER_SHAREANALYSIS",
        "columns": "ALL", "source": "WEB", "client": "WEB",
        "filter": f"(END_DATE='{end_date}')(HOLD_NUM>10000000)",
    }

    def _get(page: int) -> dict:
        def fn():
            r = requests.get(url, params={**base, "pageNumber": page},
                             headers={"User-Agent": _UA}, timeout=25)
            return r.json()
        return _net_call(fn)

    first = _get(1)
    if not first.get("result"):
        notes.append(f"十大股东接口无返回（{str(first.get('message', '未知'))[:60]}），国家队名单已跳过")
        return None
    pages = int(first["result"]["pages"])
    rows = list(first["result"]["data"])
    for p in range(2, pages + 1):
        prog(step + (p - 1) / pages * 0.9, total_steps, f"十大股东明细 {p}/{pages} 页")
        j = _get(p)
        if not j.get("result"):
            notes.append(f"十大股东第 {p} 页拉取失败，已用前 {len(rows)} 条继续")
            break
        rows.extend(j["result"]["data"])
        _time.sleep(0.12)
    df = pd.DataFrame(rows)
    try:
        df.to_csv(cache, index=False, encoding="utf-8-sig")
    except Exception:  # 缓存写失败不影响结果
        pass
    return df


def _national_holders(c: dict, prog: Callable, step: float,
                      total_steps: float, notes: list) -> dict:
    """国家队机构出现在最新报告期前十大股东名单的股票，按 ETF/汇金/社保/证金 四列表分组。"""
    groups_cfg = c.get("groups")
    if groups_cfg is None:   # 未传 = 四列表全开；注意空数组 [] 表示用户全不勾，不能被 or 回退成全开
        groups_cfg = ["etf", "huijin", "ssf", "zhengjin"]
    end_date = _latest_report_date()
    key = f"{end_date}:{','.join(groups_cfg)}"
    if _NT_MEM["key"] == key and _NT_MEM["df"] is not None:
        df = _NT_MEM["df"]
    else:
        prog(step, total_steps, "十大股东名单（新报告期首次约 1~2 分钟，之后读缓存秒开）…")
        df = _fetch_top10_holders(end_date, prog, step, total_steps, notes)
        if df is None or df.empty:
            return {"date": end_date, "groups": {}}
        _NT_MEM.update(key=key, df=df)

    def _fmt_rows(g: pd.DataFrame) -> list:
        g = g.sort_values("HOLDER_MARKET_CAP", ascending=False).head(50)
        out = []
        for _, r in g.iterrows():
            num = pd.to_numeric(r.get("HOLD_NUM"), errors="coerce")
            cap = pd.to_numeric(r.get("HOLDER_MARKET_CAP"), errors="coerce")
            ratio = pd.to_numeric(r.get("HOLD_RATIO"), errors="coerce")
            rank = pd.to_numeric(r.get("RANK"), errors="coerce")
            out.append({
                "代码": str(r.get("SECURITY_CODE", "")),
                "简称": str(r.get("SECURITY_NAME_ABBR", "")),
                "股东名称": str(r.get("HOLDER_NAME", "")),
                "持股(万股)": round(num / 1e4) if pd.notna(num) else None,
                "占总股本%": round(ratio, 2) if pd.notna(ratio) else None,
                "市值(亿)": round(cap / 1e8, 1) if pd.notna(cap) else None,
                "变动": str(r.get("HOLDNUM_CHANGE_NAME") or "—"),
                "排名": int(rank) if pd.notna(rank) else None,
            })
        return out

    names = df["HOLDER_NAME"].astype(str)
    groups: dict = {}
    for key_, title, matcher in _NT_GROUPS:
        if key_ not in groups_cfg:
            continue
        if key_ == "etf":
            # ETF 不在股东名册报表中（基金走独立披露体系）→ 用宽基 ETF 基金季报持仓聚合
            rows = _fetch_etf_holdings(end_date, prog, step + 0.3, total_steps, notes)
            groups[key_] = {"title": title, "rows": rows}
            continue
        mask = names.map(matcher)
        groups[key_] = {"title": title, "rows": _fmt_rows(df[mask])}
    if groups and not any(g["rows"] for g in groups.values()):
        notes.append(f"{end_date} 报告期未发现国家队持仓记录（十大股东口径持股≥1000 万股）")
    return {"date": end_date, "groups": groups}


def _foreign_top(c: dict, notes: list) -> list:
    """外资（北向）重仓 Top N。披露口径若调整导致无数据则降级跳过。"""
    try:
        df = _ak().stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
    except Exception as e:
        notes.append(f"北向个股持仓获取失败：{e}")
        return []
    if df is None or df.empty:
        notes.append("北向个股持仓接口无数据（披露口径调整），外资重仓已跳过")
        return []
    col_amt, col_pct = "今日估算-持股市值", "今日估算-持股占比"
    if col_amt not in df.columns:
        notes.append("北向接口列名变化，外资重仓已跳过")
        return []
    top = df.nlargest(int(c["foreign"]["top_n"]), col_amt)
    out = []
    for _, r in top.iterrows():
        pct = _f(r.get(col_pct))
        out.append({"名称": r.get("名称"), "代码": str(r.get("编码", "")).zfill(6),
                    "持股市值(亿)": _r(_f(r.get(col_amt)) / 1e8),
                    "持股占比%": (_r(pct * 100, 2) if pct is not None and pct < 1
                                  else _r(pct, 2))})
    return out


def _chain_radar(merged: pd.DataFrame, c: dict) -> list:
    """产业链传导雷达：上游/中游板块实时 5 日涨幅与资金进展。"""
    rows = []
    idx = merged.set_index("板块名称") if "板块名称" in merged.columns else None
    for ch in TRANSMIT_CHAINS:
        for role, names in (("上游", ch.get("上游板块", [])),
                            ("中游", ch.get("中游板块", []))):
            for nm in names:
                g5, f5e = None, None
                if idx is not None and nm in idx.index:
                    row = idx.loc[nm]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[0]
                    f5 = _f(row.get("5日主力净流入-净额"))
                    g5, f5e = _f(row.get("5日涨跌幅")), (f5 / 1e8 if f5 is not None else None)
                rows.append({"传导链": ch["上游"], "环节": role, "板块": nm,
                             "5日涨幅%": _r(g5), "5日主力净流入(亿)": _r(f5e, 2),
                             "监测指标": ch.get("监测指标", "") if role == "上游" else "",
                             "逻辑": ch.get("逻辑", "")})
    return rows


# ==================================================================
# 四、信号合成（埋伏 / 逃离 / 趋势 三类）
# ==================================================================
def _synthesize(out: dict) -> list:
    sig = []
    cal = out.get("calendar") or {}
    for w in cal.get("埋伏窗口", []):
        sig.append({"类型": "埋伏", "对象": w["主题"],
                    "依据": f"{w['关联事件']}前 {w['距事件(天)']} 天，处于规律窗口（{w['规律窗口']}）",
                    "动作": "分批布局", "时效": "事件前完成布局"})
    for s in out.get("stealth", []):
        sig.append({"类型": "埋伏", "对象": s["板块"],
                    "依据": f"5日涨{s['5日涨幅%']}% · 主力净流入{s['5日主力净流入(亿)']}亿 · 换手未放量",
                    "动作": "小仓位跟随并设止损", "时效": "3-10 天"})
    for s in out.get("selling", []):
        sig.append({"类型": "逃离", "对象": s["板块"], "依据": s["判定"],
                    "动作": "回避追高，持仓分批止盈", "时效": "1-2 周"})
    for s in out.get("crowding", []):
        sig.append({"类型": "逃离", "对象": s["板块"],
                    "依据": f"拥挤度 {s['触发项数']} 项触发（换手/量能/涨幅/资金流出）",
                    "动作": "分批止盈，保留底仓", "时效": "1-2 周内执行"})
    for s in out.get("cold", []):
        sig.append({"类型": "趋势", "对象": s["板块"],
                    "依据": f"冷度得分 {s['冷度得分']}（成交占比/换手/涨幅均处尾部）",
                    "动作": "关注政策催化与均值回归，低位埋伏", "时效": "1-3 个月"})
    for ch in out.get("chains", []):
        if ch["环节"] == "上游" and (ch["5日涨幅%"] or -99) > 3:
            chain = next((x for x in TRANSMIT_CHAINS if x["上游"] == ch["传导链"]), None)
            mid = "、".join(chain.get("中游板块", [])) if chain else ""
            sig.append({"类型": "趋势", "对象": f"{ch['板块']}（上游）",
                        "依据": f"上游 5 日涨 {ch['5日涨幅%']}%，向中游传导（{mid}）",
                        "动作": "关注中游补涨与业绩兑现时滞", "时效": "1-3 个月"})
    return sig


# ==================================================================
# 五、主调度 scan()
# ==================================================================
def scan(cfg=None, sections=None, progress_cb: ProgressCb = None,
         logs: Optional[list] = None) -> dict:
    t0 = _dt.datetime.now()
    c = _merge_cfg(cfg)
    sections = [s for s in (sections or SECTIONS) if s in SECTIONS]
    notes: list = []
    if logs is not None:
        logs.append(f"前瞻预警启动：模块={','.join(sections)}")

    out = {"date": _dt.date.today().strftime("%Y-%m-%d"),
           "sections": sections, "notes": notes,
           "calendar": None, "stealth": [], "selling": [], "cold": [],
           "national": [], "foreign": [], "crowding": [],
           "chains": [], "macro": [], "signals": [], "elapsed": 0.0}

    def prog(a, b, msg):
        if progress_cb:
            progress_cb(a, b, msg)

    total_steps = len(sections) + 1
    step = 0

    # --- 日历预警（纯本地逻辑，不联网） ---
    if "calendar" in sections:
        prog(step, total_steps, "日历预警与埋伏窗口…")
        out["calendar"] = get_calendar_alerts(c["calendar"])
        step += 1

    # --- 需要板块级行情/资金流的模块 ---
    need_flow = any(s in sections for s in ("stealth", "cold", "crowding", "chains"))
    need_all_hist = any(s in sections for s in ("cold", "crowding"))
    merged = None
    hist_all: dict = {}

    hist_broken = {"n": 0}

    def hist_get(name, days=260):
        if name in hist_all:
            return hist_all[name]
        try:
            hist_all[name] = _sector_hist(name, days)
        except Exception as e:  # noqa: BLE001 —— 单板块失败不影响整体
            if hist_broken["n"] < 2:  # 笔记上限，避免刷屏
                notes.append(f"{name} 板块历史获取失败：{str(e)[:80]}")
            hist_broken["n"] += 1
            hist_all[name] = None
        return hist_all[name]

    if need_flow:
        prog(step, total_steps, "加载行业板块行情与资金流…")
        try:
            merged = _merge_basic_flow(_industry_basic(), _sector_flow())
        except Exception as e:  # noqa: BLE001 —— 行情/资金流失败不拖垮整次预警
            notes.append(f"板块行情/资金流获取失败（联网）：{str(e)[:120]}")
        step += 1

    if need_all_hist:
        if merged is None:
            notes.append("板块行情不可用，板块历史未拉取（冷门/拥挤度将无结果）")
        else:
            names = merged["板块名称"].tolist()
            days = max(260, int(c["cold"]["window"]) + 10)
            for i, nm in enumerate(names):
                prog(step * 1.0 + i / max(len(names), 1), total_steps,
                     f"板块历史 {nm} ({i + 1}/{len(names)})")
                hist_get(nm, days)

    # --- 异动侦测 ---
    if "stealth" in sections and merged is not None:
        prog(step, total_steps, "异动板块侦测…")
        out["stealth"], out["selling"] = _stealth_scan(merged, c, hist_get, notes)
        step += 1

    # --- 冷门板块 ---
    if "cold" in sections:
        prog(step, total_steps, "冷门板块计算…")
        out["cold"] = _cold_scan(hist_all, c, notes)
        step += 1

    # --- 国家队资金（最新报告期前十大股东名单口径，四列表可选） ---
    if "national" in sections:
        out["national"] = _national_holders(c, prog, step, total_steps, notes)
        step += 1

    # --- 外资重仓 ---
    if "foreign" in sections:
        prog(step, total_steps, "外资重仓查询…")
        out["foreign"] = _foreign_top(c, notes)
        step += 1

    # --- 拥挤度 ---
    if "crowding" in sections:
        prog(step, total_steps, "板块拥挤度监控…")
        out["crowding"] = _crowding_scan(hist_all, merged or pd.DataFrame(), c)
        step += 1

    # --- 传导链 + 宏观雷达 ---
    if "chains" in sections:
        prog(step, total_steps, "传导链与宏观雷达…")
        if merged is None:
            notes.append("板块行情不可用，传导链实时进展已跳过（宏观雷达保留）")
        else:
            out["chains"] = _chain_radar(merged, c)
        # 宏观雷达 v3：主题归类扫描 + 方向信号（纯新闻驱动，无静态知识卡）
        narrative, sig_rows = _macro_radar()
        out["macro_narrative"] = narrative
        out["macro"] = sig_rows
        step += 1

    # --- 信号合成（永远执行，基于已采集模块） ---
    prog(total_steps, total_steps, "信号合成…")
    out["signals"] = _synthesize(out)
    out["elapsed"] = round((_dt.datetime.now() - t0).total_seconds(), 1)
    if logs is not None:
        logs.append(f"前瞻预警完成：信号 {len(out['signals'])} 条，耗时 {out['elapsed']}s")
    return out


# ==================================================================
# 六、独立网页报告（2026 重制版：锚点导航 + 统计卡 + 徽章信号 + 参数附录 + 打印样式）
# ==================================================================
_CSS = """
  :root { --bg:#0b0f14; --bg2:#101722; --card:#121924; --card2:#0e141d; --line:#1e2a3a;
          --ink:#dbe4ee; --mut:#7d8ca0; --gold:#d4af37; --red:#e35d5d;
          --green:#22a06b; --blue:#4d9fff; --amber:#e0a34a; }
  * { margin:0; box-sizing:border-box; }
  html { scroll-behavior:smooth; }
  body { background:linear-gradient(160deg,var(--bg) 55%,var(--bg2)); color:var(--ink);
         font-family:"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif;
         padding:26px 20px 60px; }
  .wrap { max-width:1180px; margin:0 auto; }
  a { color:var(--blue); text-decoration:none; } a:hover { text-decoration:underline; }
  .hero { text-align:center; padding:24px 16px 14px; }
  .hero h1 { font-size:26px; letter-spacing:1px;
             background:linear-gradient(90deg,#f0b429,#e35d5d);
             -webkit-background-clip:text; background-clip:text; color:transparent; }
  .hero .sub { color:var(--mut); font-size:12.5px; margin-top:8px; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-top:14px; }
  .chip { font-size:12px; padding:4px 12px; border:1px solid var(--line);
          border-radius:999px; background:var(--card2); }
  .chip b { color:var(--gold); }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:12px; margin:18px 0 4px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:14px 16px; text-align:center; }
  .stat .n { font-size:30px; font-weight:700; line-height:1.15; }
  .stat .l { color:var(--mut); font-size:12px; margin-top:4px; }
  .stat.red .n{color:var(--red)} .stat.green .n{color:var(--green)}
  .stat.gold .n{color:var(--gold)} .stat.blue .n{color:var(--blue)}
  .stat.amber .n{color:var(--amber)}
  .rail { position:sticky; top:0; z-index:9; display:flex; flex-wrap:wrap; gap:6px;
          justify-content:center; padding:10px 8px; margin:14px 0 0;
          background:rgba(11,15,20,.88); backdrop-filter:blur(8px);
          border:1px solid var(--line); border-radius:12px; }
  .rail a { font-size:12px; color:var(--mut); padding:4px 11px;
            border-radius:999px; border:1px solid transparent; }
  .rail a:hover { color:var(--ink); border-color:var(--line); text-decoration:none; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(460px,1fr));
          gap:14px; margin-top:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:16px 18px; box-shadow:0 6px 24px rgba(0,0,0,.35); scroll-margin-top:76px; }
  .card.wide { grid-column:1 / -1; }
  .card h2 { font-size:15px; color:var(--gold); border-left:3px solid var(--gold);
             padding-left:9px; margin-bottom:12px; }
  .card h2 small { color:var(--mut); font-weight:400; font-size:11.5px; margin-left:8px; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  th,td { padding:7px 9px; border-bottom:1px solid #1a2432; text-align:left; }
  th { color:var(--mut); font-weight:600; font-size:11.5px; white-space:nowrap; }
  tbody tr:nth-child(even) { background:#131b27; }
  tbody tr:hover { background:#172231; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  td.w { white-space:normal; line-height:1.55; }
  .up{color:var(--red)} .down{color:var(--green)}
  .muted{color:var(--mut); font-size:12px;}
  .empty { color:var(--mut); font-size:12.5px; text-align:center; padding:16px 0; }
  .sig { padding:10px 12px; margin:7px 0; background:var(--card2); border-radius:10px;
         border-left:3px solid var(--line); font-size:13px; line-height:1.6; }
  .sig.b { border-left-color:var(--red); } .sig.s { border-left-color:var(--amber); }
  .sig.t { border-left-color:var(--blue); }
  .sig small { color:var(--mut); display:block; margin-top:3px; }
  .tag { display:inline-block; font-size:11px; padding:1.5px 9px; border-radius:999px;
         margin-right:8px; vertical-align:1px; }
  .tag.b{background:#3a2020;color:#ff9b8a} .tag.s{background:#3a3120;color:var(--amber)}
  .tag.t{background:#1c2f45;color:#7db5ff}
  .ttl { display:inline-block; font-size:11px; padding:1px 8px; border-radius:6px;
         margin-left:8px; background:#182130; color:var(--mut); }
  .notes { margin-top:16px; padding:12px 16px; border:1px dashed #4a3b1e;
           border-radius:12px; background:#161408; color:var(--amber);
           font-size:12.5px; line-height:1.7; }
  .params { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
            gap:6px 20px; font-size:12.5px; }
  .params div { display:flex; justify-content:space-between;
                border-bottom:1px dashed #1a2432; padding:4px 2px; }
  .params span { color:var(--mut); }
  .params b { font-variant-numeric:tabular-nums; }
  .top-btn { position:fixed; right:22px; bottom:24px; width:40px; height:40px;
             border-radius:50%; background:var(--card); border:1px solid var(--line);
             color:var(--gold); font-size:18px; cursor:pointer; }
  footer { text-align:center; color:var(--mut); font-size:11.5px; margin-top:26px; }
  @media print {
    body { background:#fff; color:#111; padding:0; }
    .rail,.top-btn { display:none; }
    .card,.stat { border-color:#ddd; background:#fff; box-shadow:none; break-inside:avoid; }
    .hero h1 { color:#b8860b; -webkit-text-fill-color:initial; background:none; }
    tbody tr:nth-child(even){background:#f6f6f6} tbody tr:hover{background:none}
    td,th{border-color:#eee} .sig{background:#fafafa} .notes{background:#fffdf2;color:#8a6d1a}
  }
"""


def _esc(v) -> str:
    return _html.escape(str(v if v is not None else "—"))


def _cls(g) -> str:
    v = _f(g)
    if v is None:
        return ""
    return "up" if v > 0 else ("down" if v < 0 else "")


def _table(headers, rows, pct_cols=(), wide_cols=()):
    """rows: list[list[值]]；pct_cols 索引列按正负红绿着色并右对齐；wide_cols 允许换行。"""
    h = "".join(f"<th>{_esc(x)}</th>" for x in headers)
    body = []
    for r in rows:
        tds = []
        for i, v in enumerate(r):
            if i in pct_cols:
                tds.append(f'<td class="{_cls(v)} num">{_esc(v)}</td>')
            elif i in wide_cols:
                tds.append('<td class="w">' + _esc(v) + "</td>")
            else:
                tds.append(f"<td>{_esc(v)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    if not body:
        body.append('<tr><td colspan="%d" class="muted">— 暂无数据 —</td></tr>' % max(len(headers), 1))
    return f"<table><thead><tr>{h}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _sig_item(s: dict) -> str:
    t = s.get("类型")
    k = "b" if t == "埋伏" else ("s" if t == "逃离" else "t")
    return (f"<div class='sig {k}'><span class='tag {k}'>{_esc(t)}</span>"
            f"<b>{_esc(s.get('对象'))}</b> — {_esc(s.get('动作'))}"
            f"<span class='ttl'>时效：{_esc(s.get('时效'))}</span>"
            f"<small>{_esc(s.get('依据'))}</small></div>")


def build_report_html(result: dict, cfg: Optional[dict] = None) -> str:
    """把 scan() 结果渲染成独立网页报告（2026 重制版，涨红跌绿，零依赖可打印）。"""
    res = result or {}
    date = res.get("date") or _dt.date.today().strftime("%Y-%m-%d")
    now = _dt.datetime.now().strftime("%H:%M")
    cal = res.get("calendar") or {}
    themes = [t for t in (cal.get("本月主线") or []) if t]
    sigs = res.get("signals") or []
    n_b = sum(1 for s in sigs if s.get("类型") == "埋伏")
    n_s = sum(1 for s in sigs if s.get("类型") == "逃离")
    n_t = len(sigs) - n_b - n_s
    sections = res.get("sections") or []
    has = lambda k: (k in sections) if sections else True  # noqa: E731

    rail = [("信号", "signals", True), ("日历", "calendar", has("calendar")),
            ("埋伏窗口", "window", has("calendar")), ("异动侦测", "stealth", has("stealth")),
            ("冷门板块", "cold", has("cold")), ("国家队", "national", has("national")),
            ("外资重仓", "foreign", has("foreign")), ("拥挤度", "crowding", has("crowding")),
            ("传导链", "chains", has("chains")), ("宏观雷达", "macro", has("chains")),
            ("参数附录", "params", True)]
    rail_html = "".join(f"<a href='#{i}'>{t}</a>" for t, i, ok in rail if ok)

    chips = []
    if themes:
        chips.append(f"<span class='chip'>📅 本月主线：<b>{_esc(' / '.join(themes))}</b></span>")
    chips.append(f"<span class='chip'>🕒 生成于 {date} {now}</span>")
    if res.get("elapsed") is not None:
        chips.append(f"<span class='chip'>⏱ 耗时 <b>{_esc(res.get('elapsed'))}s</b></span>")
    chips.append(f"<span class='chip'>🧩 扫描模块 <b>{len(sections) or 7}</b> 个</span>")

    parts = [
        "<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">",
        f"<title>A股前瞻预警报告 · {date}</title>",
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        f"<style>{_CSS}</style></head><body><div class='wrap'>",
        "<div class='hero'><h1>⏰ A股前瞻预警报告</h1>",
        "<div class='sub'>规律引擎 + 四重领先机制（日历预热 / 传导链 / 资金领先 / 拥挤度）"
        " · 仅供研究参考，不构成投资建议</div>",
        "<div class='chips'>" + "".join(chips) + "</div></div>",
        "<div class='stats'>",
        f"<div class='stat gold'><div class='n'>{len(sigs)}</div><div class='l'>核心信号</div></div>",
        f"<div class='stat red'><div class='n'>{n_b}</div><div class='l'>埋伏</div></div>",
        f"<div class='stat amber'><div class='n'>{n_s}</div><div class='l'>逃离</div></div>",
        f"<div class='stat blue'><div class='n'>{n_t}</div><div class='l'>趋势</div></div>",
        "</div>",
        f"<nav class='rail'>{rail_html}</nav>",
        "<div class='grid'>",
    ]

    # 核心信号（通栏）
    items = "".join(_sig_item(s) for s in sigs) or "<div class='empty'>— 本轮无核心信号 —</div>"
    parts.append("<div class='card wide' id='signals'><h2>🎯 核心信号"
                 "<small>埋伏 / 逃离 / 趋势 · 自动合成自各模块</small></h2>" + items + "</div>")

    if has("calendar"):
        rows = [[a["事件"], a["日期"], a["距今(天)"], a["备注"]] for a in cal.get("日历预警", [])]
        parts.append("<div class='card' id='calendar'><h2>📅 日历预警<small>未来 N 天事件预热</small></h2>"
                     + _table(["事件", "日期", "距今(天)", "备注"], rows, pct_cols=(2,), wide_cols=(3,)) + "</div>")
        rows = [[w["主题"], w["关联事件"], w["事件日期"], w["距事件(天)"], w["规律窗口"]]
                for w in cal.get("埋伏窗口", [])]
        parts.append("<div class='card' id='window'><h2>🪟 规律埋伏窗口<small>核心反马后炮机制</small></h2>"
                     + _table(["主题", "关联事件", "事件日", "距事件(天)", "窗口"], rows, pct_cols=(3,)) + "</div>")

    if has("stealth"):
        rows = [[s["板块"], s["5日涨幅%"], s["5日主力净流入(亿)"],
                 "—" if s["换手分位%"] is None else s["换手分位%"], "埋伏嫌疑"]
                for s in res.get("stealth", [])]
        rows += [[s["板块"], s["5日涨幅%"], s["5日主力净流入(亿)"],
                  "—" if s["换手分位%"] is None else s["换手分位%"], "出货嫌疑"]
                 for s in res.get("selling", [])]
        parts.append("<div class='card' id='stealth'><h2>🕵️ 异动侦测<small>资金先动 · 价格未燥</small></h2>"
                     + _table(["板块", "5日涨幅%", "主力净流入(亿)", "换手分位%", "类型"], rows,
                              pct_cols=(1, 2)) + "</div>")

    if has("cold"):
        rows = [[s["板块"], s["冷度得分"], s["成交额占比%"], s["窗口换手%"], s["提示"]]
                for s in res.get("cold", [])]
        parts.append("<div class='card' id='cold'><h2>🧊 长期最冷门板块<small>均值回归 + 政策催化观察</small></h2>"
                     + _table(["板块", "冷度", "额占比%", "换手%", "提示"], rows, wide_cols=(4,)) + "</div>")

    if has("national"):
        nat = res.get("national") or {}
        sections_html = []
        titles = {"etf": "🏛️ ETF 持仓（国家队宽基）", "huijin": "💰 中央汇金",
                  "ssf": "🧧 社保基金", "zhengjin": "🛡️ 中国证金"}
        if isinstance(nat, dict) and nat.get("groups"):
            for k in ("etf", "huijin", "ssf", "zhengjin"):
                g = nat["groups"].get(k) or {}
                rows = g.get("rows") or []
                if not rows:
                    continue
                headers = list(rows[0].keys())
                trs = [[r.get(h) for h in headers] for r in rows]
                sections_html.append(
                    f"<div style='margin:14px 0 6px;font-weight:700;color:#d8c9a3'>{titles[k]} · {len(rows)} 条</div>"
                    + _table(headers, trs, wide_cols=(2,) if k != "etf" else (4,)))
        if sections_html:
            parts.append("<div class='card wide' id='national'><h2>🏛️ 国家队资金流向<small>"
                         + str(nat.get("date") or "") + " 报告期 · 前十大股东名单口径 · 持股≥1000万股</small></h2>"
                         + "".join(sections_html) + "</div>")

    if has("foreign"):
        rows = [[s["名称"], s["代码"], s["持股市值(亿)"], s["持股占比%"],
                 f"<a href='https://stockpage.10jqka.com.cn/{s['代码']}/' target='_blank'>同花顺 ↗</a>"]
                for s in res.get("foreign", [])]
        parts.append("<div class='card' id='foreign'><h2>🌍 外资（北向）重仓<small>持股市值 Top N</small></h2>"
                     + _table(["名称", "代码", "市值(亿)", "占比%", "链接"], rows) + "</div>")

    if has("crowding"):
        rows = [[s["板块"], s["触发项数"], s["换手分位%"], s["量能分位%"],
                 s["20日涨幅%"], s["5日主力净流入(亿)"], s["判定"]]
                for s in res.get("crowding", [])]
        parts.append("<div class='card wide' id='crowding'><h2>🔥 拥挤度监控<small>高点逃离核心</small></h2>"
                     + _table(["板块", "触发", "换手分位%", "量能分位%", "20日涨幅%", "5日净流入(亿)", "判定"],
                              rows, pct_cols=(4, 5), wide_cols=(6,)) + "</div>")

    if has("chains"):
        rows = [[s["传导链"], s["环节"], s["板块"], s["5日涨幅%"], s["5日主力净流入(亿)"], s["逻辑"]]
                for s in res.get("chains", [])]
        parts.append("<div class='card wide' id='chains'><h2>⛓️ 产业链传导雷达<small>上游涨价 → 中游滞后受益</small></h2>"
                     + _table(["链", "环节", "板块", "5日涨幅%", "5日净流入(亿)", "逻辑"],
                              rows, pct_cols=(3, 4), wide_cols=(5,)) + "</div>")
        mrows = [[m.get("类型", ""), m.get("触发", ""), m.get("细节", ""), m.get("方向", ""), m.get("应对", "")]
                 for m in res.get("macro", [])]
        narr = res.get("macro_narrative") or ""
        parts.append("<div class='card wide' id='macro'><h2>🧭 宏观雷达<small>主题归类扫描 + 方向信号（纯新闻驱动 · 无回波不显示 · 非利率预测）</small></h2>"
                     + (f"<div style='margin:0 0 8px;font-size:12px;color:#d8c9a3'>🧭 {_esc(narr)}</div>" if narr else "")
                     + _table(["类型", "主题/信号", "细节", "方向", "应对"], mrows, wide_cols=(2, 4)) + "</div>")

    # 参数附录（本次运行生效的阈值）
    eff = _merge_cfg(cfg)
    prows = "".join(f"<div><span>{_esc(g + '.' + k)}</span><b>{_esc(v)}</b></div>"
                    for g, kv in eff.items() if isinstance(kv, dict)
                    for k, v in kv.items())
    parts.append("</div><div class='card wide' id='params'><h2>⚙ 本次生效阈值<small>"
                 "所有阈值均可在网页「⚙ 预警参数」窗口调整</small></h2>"
                 f"<div class='params'>{prows}</div></div>")

    if res.get("notes"):
        parts.append("<div class='notes'>📝 数据说明：" + _esc(" ｜ ".join(res["notes"])) + "</div>")
    parts.append("<footer>筛股 · P的初航 — 前瞻预警报告 · 涨红跌绿 · 生成时间 "
                 f"{date} {now}</footer>")
    parts.append("<button class='top-btn' onclick='window.scrollTo({top:0,behavior:\"smooth\"})'"
                 " title='回到顶部'>↑</button>")
    parts.append("</div></body></html>")
    return "".join(parts)
