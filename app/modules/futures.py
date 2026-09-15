# -*- coding: utf-8 -*-
"""期货视察模块 —— 只拉「主连 / 主力合约」，多因子研判商品动能与趋势强度。

设计要点（与项目既有模块同风格）：
  · 主连清单：akshare ``futures_display_main_sina``（权威主连表，落盘缓存 7 天）；
    接口失败时回落到内置 84 项主连表（symbol 形如 RB0，0 后缀即连续/主力合约）。
  · 历史日K：新浪主连日K直连（跨 shfe/dce/czce/cffex/ine/gfex 六大交易所实测全通，
    单品种 0.1~0.8s，12 线程并发全市场约 3.3s）；AKShare 封装版作为兜底。
  · 新闻：上海有色网 SHMET（商品/有色/黑色一手资讯）+ 东财快讯过滤期货关键词，
    取少量（默认 10 条）并按品种别名关联到具体合约，作为低权重情绪因子。

因子引擎（8 法加权，合成 -100 ~ +100）：
  ① 动量 mom   窗口收益 + 波动调整动量（Sharpe 化）——主看涨跌动能
  ② 效率 er    Kaufman 效率比 + 回归 R²——主看趋势强度（纯粹度）
  ③ ADX        14 周期 Wilder 平滑，方向由 ±DI 定——趋势强度与方向
  ④ 均线 ma    MA5/20/60 多空排列 + 价格位置
  ⑤ MACD       DIF 零轴 + 金死叉 + 柱动能
  ⑥ RSI        14 周期动能与超买超卖
  ⑦ 持仓 oi    持仓量变化配合价格方向（商品特有：增仓涨跌 = 趋势确认）
  ⑧ 新闻 news  关联新闻的情绪投票（低权重，仅作修正）

所有阈值/权重可通过 cfg 覆盖（前端「⚙ 视察参数」窗口），默认值即稳健设计值。
网络层复用 alert 的 _ensure_ua / _net_call（浏览器 UA + 代理↔直连切换）。

已知局限（诚实标注）：主连为「主力合约拼接」口径，换月时存在跳空，
动量因子已对日收益做 MAD 缩尾（winsorize）抑制，但无法完全消除。
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import numpy as np

from .alert import _UA, _ensure_ua, _net_call
from .ticker import _sess

ProgressCb = Optional[Callable[[float, float, str], None]]

# ============ 可调参数默认值 ============
DEFAULTS: dict = {
    "weeks": 8,             # 分析窗口：从今天往回推 n 周
    "top_n": 5,             # 最高分 / 最低分各取几种
    "min_vol": 500.0,       # 窗口内日均成交量下限（手）——过滤僵尸品种
    "max_news": 10,         # 新闻条数（用户要求「少一点」）
    "with_news": True,      # 是否纳入新闻情绪因子
    "sectors": None,        # 板块过滤，None = 全部
    "refresh_symbols": False,
    "weights": None,        # 因子权重覆盖 {mom:0.2, er:0.16, ...}，内部自动归一化
    "max_gap_days": 20,     # 数据断档上限（天）——覆盖春节等长假，超过视为僵尸品种
    "win_scale": 0.20,      # 动量归一化：窗口收益 ±20% 映射 ±1
    "sharpe_scale": 1.20,   # 波动调整动量归一化
    "er_lo": 0.15, "er_hi": 0.50,   # 效率比映射区间
    "adx_scale": 30.0,      # ADX 归一化尺度
    "rsi_scale": 22.0,      # RSI 偏离 50 的归一化尺度
    "oi_scale": 0.30,       # 持仓变化率归一化
}

# 因子权重（合计 1.00）
FACTOR_W: dict = {
    "mom": 0.20, "er": 0.16, "adx": 0.14, "ma": 0.13,
    "macd": 0.11, "rsi": 0.09, "oi": 0.08, "news": 0.09,
}

SECTORS: dict = {
    "black": "黑色煤焦钢矿", "nonfer": "有色金属", "precious": "贵金属",
    "energychem": "能源化工", "agri": "农产品", "newenergy": "新能源",
    "finance": "金融期货", "shipping": "航运指数",
}

# ============ 内置主连表（symbol 带 0 后缀 = 主力连续合约） ============
# (symbol, exchange, name, sector, 新闻别名...)
_BUILTIN: list = [
    ("V0", "dce", "PVC", "energychem", "PVC", "聚氯乙烯"),
    ("P0", "dce", "棕榈油", "agri", "棕榈", "棕榈油"),
    ("B0", "dce", "豆二", "agri", "豆二", "大豆"),
    ("M0", "dce", "豆粕", "agri", "豆粕"),
    ("I0", "dce", "铁矿石", "black", "铁矿石", "铁矿"),
    ("JD0", "dce", "鸡蛋", "agri", "鸡蛋"),
    ("L0", "dce", "塑料", "energychem", "塑料", "聚乙烯"),
    ("PP0", "dce", "聚丙烯", "energychem", "聚丙烯"),
    ("FB0", "dce", "纤维板", "agri", "纤维板"),
    ("BB0", "dce", "胶合板", "agri", "胶合板"),
    ("Y0", "dce", "豆油", "agri", "豆油"),
    ("C0", "dce", "玉米", "agri", "玉米"),
    ("A0", "dce", "豆一", "agri", "豆一", "大豆"),
    ("J0", "dce", "焦炭", "black", "焦炭"),
    ("JM0", "dce", "焦煤", "black", "焦煤"),
    ("CS0", "dce", "玉米淀粉", "agri", "淀粉", "玉米淀粉"),
    ("EG0", "dce", "乙二醇", "energychem", "乙二醇"),
    ("RR0", "dce", "粳米", "agri", "粳米", "大米"),
    ("EB0", "dce", "苯乙烯", "energychem", "苯乙烯"),
    ("PG0", "dce", "液化石油气", "energychem", "液化气", "LPG"),
    ("LH0", "dce", "生猪", "agri", "生猪", "猪肉"),
    ("LG0", "dce", "原木", "agri", "原木", "木材"),
    ("BZ0", "dce", "纯苯", "energychem", "纯苯"),
    ("TA0", "czce", "PTA", "energychem", "PTA"),
    ("OI0", "czce", "菜油", "agri", "菜油", "菜籽油"),
    ("RS0", "czce", "菜籽", "agri", "菜籽"),
    ("RM0", "czce", "菜粕", "agri", "菜粕"),
    ("WH0", "czce", "强麦", "agri", "强麦", "小麦"),
    ("JR0", "czce", "粳稻", "agri", "粳稻", "稻谷"),
    ("SR0", "czce", "白糖", "agri", "白糖", "食糖"),
    ("CF0", "czce", "棉花", "agri", "棉花", "棉"),
    ("RI0", "czce", "早籼稻", "agri", "早籼稻", "稻谷"),
    ("MA0", "czce", "甲醇", "energychem", "甲醇"),
    ("FG0", "czce", "玻璃", "energychem", "玻璃"),
    ("LR0", "czce", "晚籼稻", "agri", "晚籼稻", "稻谷"),
    ("SF0", "czce", "硅铁", "black", "硅铁"),
    ("SM0", "czce", "锰硅", "black", "锰硅"),
    ("CY0", "czce", "棉纱", "agri", "棉纱"),
    ("AP0", "czce", "苹果", "agri", "苹果"),
    ("CJ0", "czce", "红枣", "agri", "红枣"),
    ("UR0", "czce", "尿素", "energychem", "尿素"),
    ("SA0", "czce", "纯碱", "energychem", "纯碱"),
    ("PF0", "czce", "短纤", "energychem", "短纤"),
    ("PK0", "czce", "花生", "agri", "花生"),
    ("SH0", "czce", "烧碱", "energychem", "烧碱"),
    ("PX0", "czce", "对二甲苯", "energychem", "对二甲苯", "PX"),
    ("PR0", "czce", "瓶片", "energychem", "瓶片"),
    ("PL0", "czce", "丙烯", "energychem", "丙烯"),
    ("FU0", "shfe", "燃料油", "energychem", "燃料油"),
    ("AL0", "shfe", "沪铝", "nonfer", "铝"),
    ("RU0", "shfe", "天然橡胶", "energychem", "橡胶", "天然橡胶"),
    ("ZN0", "shfe", "沪锌", "nonfer", "锌"),
    ("CU0", "shfe", "沪铜", "nonfer", "铜"),
    ("AU0", "shfe", "沪金", "precious", "黄金", "沪金", "金价"),
    ("RB0", "shfe", "螺纹钢", "black", "螺纹", "螺纹钢", "钢材", "钢价"),
    ("PB0", "shfe", "沪铅", "nonfer", "铅"),
    ("AG0", "shfe", "沪银", "precious", "白银", "沪银", "银价"),
    ("BU0", "shfe", "沥青", "energychem", "沥青"),
    ("HC0", "shfe", "热卷", "black", "热卷", "热轧卷板", "钢材", "钢价"),
    ("SN0", "shfe", "沪锡", "nonfer", "锡"),
    ("NI0", "shfe", "沪镍", "nonfer", "镍"),
    ("SP0", "shfe", "纸浆", "agri", "纸浆"),
    ("SS0", "shfe", "不锈钢", "black", "不锈钢"),
    ("AO0", "shfe", "氧化铝", "nonfer", "氧化铝"),
    ("BR0", "shfe", "丁二烯橡胶", "energychem", "丁二烯橡胶", "合成橡胶"),
    ("AD0", "shfe", "铸造铝合金", "nonfer", "铸造铝合金", "铝合金"),
    ("OP0", "shfe", "胶版印刷纸", "agri", "胶版印刷纸", "纸"),
    ("SC0", "ine", "原油", "energychem", "原油", "石油", "布伦特", "WTI", "国际油价"),
    ("NR0", "ine", "20号胶", "energychem", "20号胶", "橡胶"),
    ("LU0", "ine", "低硫燃料油", "energychem", "低硫燃油", "低硫燃料油"),
    ("BC0", "ine", "国际铜", "nonfer", "国际铜"),
    ("EC0", "ine", "集运欧线", "shipping", "集运", "欧线", "航运"),
    ("IF0", "cffex", "沪深300股指", "finance", "沪深300期货", "股指期货"),
    ("IH0", "cffex", "上证50股指", "finance", "上证50期货", "股指期货"),
    ("IC0", "cffex", "中证500股指", "finance", "中证500期货", "股指期货"),
    ("IM0", "cffex", "中证1000股指", "finance", "中证1000期货", "股指期货"),
    ("TS0", "cffex", "2年期国债", "finance", "国债期货", "2年期国债"),
    ("TF0", "cffex", "5年期国债", "finance", "国债期货", "5年期国债"),
    ("T0", "cffex", "10年期国债", "finance", "国债期货", "10年期国债"),
    ("SI0", "gfex", "工业硅", "newenergy", "工业硅", "硅"),
    ("LC0", "gfex", "碳酸锂", "newenergy", "碳酸锂", "锂"),
    ("PS0", "gfex", "多晶硅", "newenergy", "多晶硅", "硅料"),
    ("PT0", "gfex", "铂", "precious", "铂"),
    ("PD0", "gfex", "钯", "precious", "钯"),
]

_SYM_META: dict = {}
_SYM_KW: dict = {}
for _t in _BUILTIN:
    _sym = _t[0]
    _SYM_META[_sym] = {"symbol": _sym, "exchange": _t[1], "name": _t[2],
                       "sector": _t[3], "sector_name": SECTORS.get(_t[3], _t[3])}
    # 新闻关键词 = 品种名 + 别名（元组第 5 项起）。单字关键词（铜/铝/纸…）
    # 极易误命中正文，命中时要求标题同时含期货上下文词，见 _link_news。
    _SYM_KW[_sym] = [_t[2]] + list(_t[4:])

# 兜底：akshare 主连清单里出现但内置表没有的品种（新上市）
_EX_SHOW = {"dce": "大商所", "czce": "郑商所", "shfe": "上期所",
            "cffex": "中金所", "ine": "上期能源", "gfex": "广期所"}

_MAIN_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "futures_main.json")
_MAIN_TTL = 7 * 86400


# ============ 主连清单 ============
_MAIN_REFRESHING = {"v": False}


def _fetch_main_rows() -> list[dict]:
    """akshare 权威主连表（同步，约 11s）——只在后台线程调用。"""
    import akshare as ak
    df = ak.futures_display_main_sina()
    rows = []
    for r in df.itertuples(index=False):
        sym = str(getattr(r, "symbol", "")).strip()
        if not sym.endswith("0"):
            continue          # 硬性保证：只要主连/连续合约
        rows.append({"symbol": sym,
                     "exchange": str(getattr(r, "exchange", "")).strip(),
                     "name": str(getattr(r, "name", "")).strip()})
    return rows


def _refresh_main_async() -> None:
    """后台线程刷新主连清单并落盘。akshare 该接口不用 V8，线程安全。"""
    if _MAIN_REFRESHING["v"]:
        return
    _MAIN_REFRESHING["v"] = True

    def _work():
        try:
            rows = _fetch_main_rows()
            if rows:
                os.makedirs(os.path.dirname(_MAIN_CACHE_PATH), exist_ok=True)
                with open(_MAIN_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "symbols": rows}, f,
                              ensure_ascii=False)
        except Exception:  # noqa: BLE001 —— 刷新失败静默，下次再试
            pass
        finally:
            _MAIN_REFRESHING["v"] = False

    import threading
    threading.Thread(target=_work, daemon=True).start()


def main_list(force: bool = False) -> list[dict]:
    """主连清单：缓存 → 内置表（首次立即返回 + 后台异步刷新 akshare 权威表）。

    只返回 symbol 以 0 结尾的连续/主力合约，绝不含具体月份合约（如 RB2610）。
    akshare 该接口实测约 11s，故绝不放在请求主路径上阻塞用户。"""
    merged: dict = {k: dict(v) for k, v in _SYM_META.items()}
    notes: list = []
    cache_ok = False
    try:
        if not force:
            st = os.stat(_MAIN_CACHE_PATH)
            if time.time() - st.st_mtime < _MAIN_TTL:
                with open(_MAIN_CACHE_PATH, encoding="utf-8") as f:
                    cached = json.load(f)
                cache_ok = True
                out = _enrich(cached.get("symbols") or [], merged, notes)
                return out
    except Exception:  # noqa: BLE001 —— 无缓存/过期/损坏
        pass

    if force:
        try:
            rows = _fetch_main_rows()
            if rows:
                os.makedirs(os.path.dirname(_MAIN_CACHE_PATH), exist_ok=True)
                with open(_MAIN_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "symbols": rows}, f,
                              ensure_ascii=False)
                return _enrich(rows, merged, notes)
        except Exception as e:  # noqa: BLE001
            notes.append(f"主连清单刷新失败（{str(e)[:60]}），已用内置主连表")
    else:
        if not cache_ok:
            _refresh_main_async()
            notes.append("主连清单首次加载中（后台刷新权威表，本次先用内置主连表）")
    return _enrich([], merged, notes)


def _enrich(rows: list, merged: dict, notes: list) -> list:
    """akshare 行 ∪ 内置表（内置表提供板块/中文名，akshare 提供新上市品种）。"""
    out = list(merged.values())
    for r in rows:
        sym = r.get("symbol")
        if not sym or sym in merged:
            continue
        name = re.sub(r"连续$", "", str(r.get("name") or sym))
        out.append({"symbol": sym, "exchange": r.get("exchange", ""),
                    "name": name, "sector": "other",
                    "sector_name": "其它", "fresh": True})
    if notes:
        out.append({"__notes__": notes})
    return out


# ============ 历史日K（新浪主连直连） ============
_KLINE_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/v1.5/"
              "InnerFuturesNewService.getDailyKLine?symbol={sym}")
_JSONP_RE = re.compile(r"\((\[.*\])\)", re.S)

_FUT_SESS = None


def _fut_sess():
    """期货专用 Session：默认连接池只有 10，而本模块 12 线程并发拉K线，
    实测会触发 "Connection pool is full, discarding connection"（连接被丢弃后
    重建 = 白做一次 TLS 握手）。这里把池子放大到 ≥ 并发数。"""
    global _FUT_SESS
    if _FUT_SESS is None:
        import requests
        from requests.adapters import HTTPAdapter
        s = requests.Session()
        ad = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)
        s.mount("https://", ad)
        s.mount("http://", ad)
        _FUT_SESS = s
    return _FUT_SESS


def fetch_kline(sym: str, timeout: int = 15) -> list[dict]:
    """新浪主连日K：返回 [{date, open, high, low, close, volume, hold, settle}]。"""

    def _get():
        r = _fut_sess().get(_KLINE_URL.format(sym=sym),
                        headers={"User-Agent": _UA,
                                 "Referer": "https://finance.sina.com.cn"},
                        timeout=timeout)
        r.encoding = "utf-8"
        return r.text

    try:
        text = _net_call(_get, retries=2, pause=0.4)
    except Exception:  # noqa: BLE001
        return []
    m = _JSONP_RE.search(text or "")
    if not m:
        return []
    try:
        raw = json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for x in raw:
        try:
            c = float(x.get("c") or 0)
            if c <= 0:
                continue
            out.append({
                "date": str(x.get("d"))[:10],
                "open": float(x.get("o") or c),
                "high": float(x.get("h") or c),
                "low": float(x.get("l") or c),
                "close": c,
                "volume": float(x.get("v") or 0),
                "hold": float(x.get("p") or 0),
                "settle": float(x.get("s") or c),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["date"])
    return out


# ============ 技术指标（纯 numpy，无第三方依赖） ============
def _ema(arr: np.ndarray, n: int) -> np.ndarray:
    """EMA（周期 n），以前 n 个值的 SMA 播种（与 cycle._ema_series 同口径）。

    原实现用首值播种。期货主连日K 有 PREHEAT=130 根预热，(1-k)^130≈1e-10，
    实测影响可忽略；统一为 SMA 播种是为了两个模块口径一致、短序列也不失真。
    """
    a = np.asarray(arr, dtype=float)
    m = len(a)
    if m == 0:
        return a
    k = 2.0 / (n + 1)
    if m < n:
        seed, i0 = float(a[0]), 0
    else:
        seed, i0 = float(np.mean(a[:n])), n - 1
    out = np.empty(m)
    for i in range(i0):
        out[i] = float(np.mean(a[:i + 1]))
    v = seed
    out[i0] = v
    for i in range(i0 + 1, m):
        v = float(a[i]) * k + v * (1 - k)
        out[i] = v
    return out


def _rsi(closes: np.ndarray, n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    d = np.diff(closes[-(n + 1):])
    up = np.clip(d, 0, None).mean()
    dn = (-np.clip(d, None, 0)).mean()
    if dn <= 1e-12:
        return 100.0 if up > 0 else 50.0
    rs = up / dn
    return float(100 - 100 / (1 + rs))


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         n: int = 14) -> tuple:
    """Wilder 平滑 ADX：返回 (adx, +di, -di)。"""
    m = len(close)
    if m < n * 2 + 2:
        return 0.0, 0.0, 0.0
    tr = np.zeros(m - 1)
    pdm = np.zeros(m - 1)
    ndm = np.zeros(m - 1)
    for i in range(1, m):
        tr[i - 1] = max(high[i] - low[i],
                        abs(high[i] - close[i - 1]),
                        abs(low[i] - close[i - 1]))
        up_move = high[i] - high[i - 1]
        dn_move = low[i - 1] - low[i]
        pdm[i - 1] = up_move if (up_move > dn_move and up_move > 0) else 0.0
        ndm[i - 1] = dn_move if (dn_move > up_move and dn_move > 0) else 0.0
    # Wilder 平滑（首值取前 n 项和，其后 递推）
    def _wilder(x: np.ndarray) -> np.ndarray:
        if len(x) < n:
            return np.array([])
        out = np.zeros(len(x) - n + 1)
        s = x[:n].sum()
        out[0] = s
        for i in range(n, len(x)):
            s = s - s / n + x[i]
            out[i - n + 1] = s
        return out

    atr = _wilder(tr)
    sp = _wilder(pdm)
    sn = _wilder(ndm)
    if not len(atr) or not len(sp):
        return 0.0, 0.0, 0.0
    k = min(len(atr), len(sp), len(sn))
    atr, sp, sn = atr[:k], sp[:k], sn[:k]
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * sp / np.where(atr > 0, atr, np.nan)
        ndi = 100 * sn / np.where(atr > 0, atr, np.nan)
    pdi = np.nan_to_num(pdi)
    ndi = np.nan_to_num(ndi)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = 100 * np.abs(pdi - ndi) / np.where((pdi + ndi) > 0, pdi + ndi, np.nan)
    dx = np.nan_to_num(dx)
    if len(dx) < n:
        return 0.0, float(pdi[-1]), float(ndi[-1])
    adx = float(dx[:n].mean())
    for v in dx[n:]:
        adx = (adx * (n - 1) + v) / n
    return adx, float(pdi[-1]), float(ndi[-1])


def _efficiency(closes: np.ndarray) -> tuple:
    """Kaufman 效率比 + 线性回归 R²（趋势纯粹度双口径）。"""
    n = len(closes)
    if n < 5:
        return 0.0, 0.0
    net = abs(float(closes[-1]) - float(closes[0]))
    path = float(np.abs(np.diff(closes)).sum())
    er = net / path if path > 1e-12 else 0.0
    x = np.arange(n, dtype=float)
    try:
        coef = np.polyfit(x, closes, 1)
        fit = np.polyval(coef, x)
        ss_res = float(((closes - fit) ** 2).sum())
        ss_tot = float(((closes - closes.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    except Exception:  # noqa: BLE001
        r2 = 0.0
    return float(min(max(er, 0.0), 1.0)), float(min(max(r2, 0.0), 1.0))


def _mad_winsor(rets: np.ndarray, k: float = 4.0) -> np.ndarray:
    """对日收益做 MAD 缩尾，抑制主连换月跳空对动量的污染。"""
    if len(rets) < 8:
        return rets
    med = float(np.median(rets))
    mad = float(np.median(np.abs(rets - med)))
    scale = mad * 1.4826 if mad > 1e-12 else float(np.std(rets) or 1e-9)
    lim = k * (scale if scale > 1e-12 else 1e-9)
    return np.clip(rets, med - lim, med + lim)


# ============ 新闻 ============
_POS_WORDS = ("上涨", "大涨", "走强", "反弹", "上调", "涨价", "提涨", "减产", "停产",
              "去库", "库存下降", "缺口", "供应紧张", "紧缺", "紧张", "需求旺盛",
              "创新高", "新高", "看涨", "利多", "支撑", "回暖", "复苏", "挺价",
              "超预期", "走高", "攀升", "提振", "涨", "增仓")
_NEG_WORDS = ("下跌", "大跌", "走弱", "回落", "下调", "降价", "累库", "库存增加",
              "增产", "复产", "过剩", "需求疲软", "疲弱", "创新低", "新低", "看空",
              "利空", "承压", "抛压", "萎缩", "低迷", "跌", "减仓", "供大于求")
_FUT_CTX = ("期货", "主力合约", "期价", "盘面", "夜盘", "持仓", "仓单",
            "现货", "内盘", "外盘", "合约", "库存", "LME", "金属",
            "大宗商品", "大宗", "期市", "商品市场")


def _sentiment(text: str) -> int:
    t = str(text or "")
    pos = sum(1 for w in _POS_WORDS if w in t)
    neg = sum(1 for w in _NEG_WORDS if w in t)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


def _news_shmet(limit: int = 10) -> list[dict]:
    """上海有色网期货快讯（商品/有色/黑色一手资讯，质量高、噪音少）。"""
    try:
        import akshare as ak
        df = ak.futures_news_shmet()
    except Exception:  # noqa: BLE001
        return []
    out = []
    try:
        for r in df.itertuples(index=False):
            ts = str(r[0])[:19]
            body = str(r[1]).strip()
            if not body:
                continue
            out.append({"time": ts, "title": body[:160], "source": "SHMET"})
            if len(out) >= limit:
                break
    except Exception:  # noqa: BLE001
        return []
    return out


# 东财资讯栏目（实测有效的商品期货相关栏目）：356=期货/能源，361=贵金属
_EM_COLUMNS = ((356, "东财期货"), (361, "东财贵金属"))


def _news_em(limit: int = 12) -> list[dict]:
    """东财期货/贵金属专栏：标题直击商品，品种关联命中率远高于泛财经快讯。"""
    out: list = []
    for col, src in _EM_COLUMNS:
        if len(out) >= limit:
            break
        url = (f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?"
               f"client=web&biz=web_news_col&column={col}&order=1&needInteractData=0&"
               f"page_index=1&page_size={max(4, limit)}&req_trace=1&"
               f"fields=code,showTime,title,summary,url,uniqueUrl&types=1,20")

        def _get(_u=url):
            return _fut_sess().get(_u, headers={"User-Agent": _UA}, timeout=10).text

        try:
            j = json.loads(_net_call(_get, retries=2, pause=0.3))
        except Exception:  # noqa: BLE001
            continue
        for it in ((j.get("data") or {}).get("list") or []):
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            out.append({"time": str(it.get("showTime") or "")[:19],
                        "title": title[:160], "source": src,
                        "url": it.get("uniqueUrl") or it.get("url") or ""})
            if len(out) >= limit:
                break
    return out


def _build_all_kw() -> list:
    ks = set()
    for v in _SYM_KW.values():
        for w in v:
            ks.add(w)
    return sorted(ks, key=len, reverse=True)


_ALL_KW = _build_all_kw()


def _norm_time(t) -> str:
    """把各源时间统一为 'YYYY-MM-DD HH:MM:SS'（无法识别时返回截断原串，缺失返回 ''）。

    SHMET 的 time 是 datetime 字符串、东财 showTime 是 'YYYY-MM-DD HH:MM:SS'，
    还有源给 'YYYY/MM/DD' 或 'MM-DD HH:MM'。格式不齐会让**排序**（字符串比较）
    和前端 `(time||'').slice(5,16)` 的**展示**同时错位，故统一归一化。"""
    s = str(t or "").strip()
    if not s:
        return ""
    s = s.replace("/", "-").replace("T", " ")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s*(\d{2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        return (f"{m.group(1)}-{m.group(2)}-{m.group(3)} "
                f"{m.group(4)}:{m.group(5)}:{m.group(6) or '00'}")
    m = re.match(r"^(\d{2})-(\d{2})\s*(\d{2}):(\d{2})", s)
    if m:   # 仅「MM-DD HH:MM」→ 补当年
        return (f"{_dt.date.today().year}-{m.group(1)}-{m.group(2)} "
                f"{m.group(3)}:{m.group(4)}:00")
    return s[:19]


def fetch_news(limit: int = 10) -> list[dict]:
    """期货市场新闻（少量）：SHMET 主源 + 东财期货/贵金属专栏补充。

    排序策略：能关联到具体品种的新闻优先（组内按时间降序），其余按时间降序。
    SHMET 是实时滚动快讯，噪音较多（展会/宏观），若纯按时间取会把真正有用的
    商品资讯挤出去，故做「命中优先」重排。

    呈现增强（2026-09-10）：
      · 时间统一归一化（见 `_norm_time`），保证排序与前端显示口径一致；
      · **单品种占位上限**：原油/铜这类热门品种在快讯里极密集，不设上限会把整列
        占满（用户反馈"翻来覆去都是同一条品种"），超限的顺延而非丢弃；
      · 每条预置 `symbols`（命中品种名）与 `sent`（情绪 -1/0/1），且**与打分同用
        `_hit_sym`**，保证「前端标签」= 「计入情绪票的新闻」，分数可解释。"""
    # 候选池要显著大于最终条数，否则「单品种占位上限」形同虚设
    # （候选刚好等于 limit 时全部都能放下，热门品种照样占满整列）。
    _pool = max(16, int(limit) * 2)
    items = _news_shmet(_pool)
    try:
        items += _news_em(_pool)
    except Exception:  # noqa: BLE001
        pass
    seen = set()
    out: list = []
    for it in items:
        key = re.sub(r"\W+", "", str(it.get("title") or ""))[:40]
        if not key or key in seen:
            continue
        seen.add(key)
        it["time"] = _norm_time(it.get("time"))
        syms = _hit_sym(it["title"])          # 只算一次（原来两条列表推导各算一遍）
        it["_syms"] = syms
        it["symbols"] = [_SYM_META.get(s, {}).get("name", s) for s in syms][:4]
        it["sent"] = _sentiment(it["title"]) if syms else 0
        out.append(it)
    hit = [x for x in out if x["_syms"]]
    rest = [x for x in out if not x["_syms"]]
    for grp in (hit, rest):
        grp.sort(key=lambda x: x.get("time") or "", reverse=True)
    # 补位池按「话题相关性」排序：① 未命中品种但含期货上下文词的 ② 超限的热门品种
    # ③ 剩下的杂讯。实测 SHMET 的「无命中」条目里含期货上下文词的为 0（多为欧央行/
    # 国债收益率之类的滚动快讯），故正常情况下不会为凑条数把杂讯顶上来。
    rest_ctx = [x for x in rest
                if any(w in str(x.get("title") or "") for w in _FUT_CTX)]
    _ctx_ids = {id(x) for x in rest_ctx}
    rest_other = [x for x in rest if id(x) not in _ctx_ids]

    # 软限额：同一品种先只取 cap 条（避免热门品种刷屏），但**不丢弃**超限项 ——
    # 它们排在非热门之后补位，保证「信息不丢、热门不刷屏」。
    # 注意不能写成 (picked + 超限项 + 其余)：超限项紧跟 picked 之后，只要 picked
    # 不满 limit 就被塞回来，等于限额失效（实测原油仍会占 7/10）。
    n_max = max(1, int(limit))
    cap = max(2, n_max // 5)
    res: list = []
    _ids: set = set()
    used: dict = {}

    def _take(x) -> None:
        if id(x) in _ids:
            return
        _ids.add(id(x))
        res.append(x)

    for x in hit:                       # ① 每品种最多 cap 条
        s0 = x["_syms"][0]
        if used.get(s0, 0) >= cap:
            continue
        used[s0] = used.get(s0, 0) + 1
        _take(x)
        if len(res) >= n_max:
            break
    for x in rest_ctx + hit + rest_other:   # ② 补位（相关性降序）
        if len(res) >= n_max:
            break
        _take(x)
    for x in res:
        x.pop("_syms", None)
    return res


def _hit_sym(title: str) -> list:
    """标题命中的品种 symbol 列表。单字关键词（铜/铝/纸/锂…）必须搭配期货上下文词，
    否则「锂电池」「纸巾」之类会把无关新闻误关联到品种上。"""
    t = str(title or "")
    ctx = any(w in t for w in _FUT_CTX)
    out = []
    for sym, kws in _SYM_KW.items():
        for k in kws:
            if k not in t:
                continue
            if len(k) <= 1 and not ctx:
                continue
            out.append(sym)
            break
    return out


def _link_news(news: list[dict]) -> tuple[dict, dict]:
    """新闻 → 品种关联：返回 ({symbol: [标题...]}, {symbol: 情绪和})。

    情绪票完全复用 `_hit_sym` 的口径（与前端品种标签同源）；`sent` 已在
    fetch_news 里算好时直接取用，避免同一标题重复跑词典。"""
    hits: dict = {}
    sent: dict = {}
    for it in news:
        t = it["title"]
        s = it.get("sent")
        if s is None:
            s = _sentiment(t)
        for sym in _hit_sym(t):
            hits.setdefault(sym, []).append(t[:70])
            sent[sym] = sent.get(sym, 0) + int(s)
    return hits, sent


# ============ 单品种打分 ============
def score_symbol(meta: dict, bars: list[dict], cfg: dict,
                 news_hits: Optional[list] = None,
                 news_sent: int = 0, n_pre: int = 0,
                 W: Optional[dict] = None) -> Optional[dict]:
    """对单个主连打分（±100）。

    bars 需按日期升序，前 n_pre 根为「指标预热段」（不参与窗口涨跌统计），
    其后为分析窗口（= 今天往回推 weeks 周内的交易日）。"""
    w0 = int(n_pre or 0)
    n_bars = len(bars)
    if n_bars < 25:
        return None
    w0 = min(max(w0, 0), n_bars - 20)      # 窗口至少保留 20 根
    if n_bars - w0 < 20:
        return None
    dates = [b["date"] for b in bars]
    close = np.array([b["close"] for b in bars], dtype=float)
    high = np.array([b["high"] for b in bars], dtype=float)
    low = np.array([b["low"] for b in bars], dtype=float)
    vol = np.array([b["volume"] for b in bars], dtype=float)
    hold = np.array([b["hold"] for b in bars], dtype=float)
    if not np.all(np.isfinite(close)) or float(close[-1]) <= 0:
        return None

    # 窗口段（含窗口首日，用于计算首尾收益）
    wc = close[w0:]
    wv = vol[w0:]
    wh = hold[w0:]
    last = float(close[-1])
    first = float(wc[0])
    ret = last / first - 1 if first > 0 else 0.0

    rets = np.diff(wc) / np.where(wc[:-1] > 0, wc[:-1], np.nan)
    rets = rets[np.isfinite(rets)]
    if len(rets) < 15:
        return None
    rets_w = _mad_winsor(rets)
    vola = float(np.std(rets_w)) * math.sqrt(len(rets_w))
    sharpe = float(np.sum(rets_w)) / vola if vola > 1e-9 else 0.0

    f: list = []

    def _add(name: str, value: str, s: float, desc: str) -> None:
        s = max(-1.0, min(1.0, float(s)))
        f.append({"name": name, "value": value, "score": round(s, 2),
                  "judge": "看多" if s > 0.15 else ("看空" if s < -0.15 else "中性"),
                  "desc": desc})

    # 窗口涨跌的方向符号：ret 恰为 0 时 math.copysign(1.0, 0.0) 会返回 +1.0，
    # 把「零涨跌」算成多头（②趋势强度、⑦持仓配合都用它定向），故显式归零为中性。
    _dir = 0.0 if abs(ret) < 1e-12 else math.copysign(1.0, ret)

    # ① 动量：窗口收益（缩尾复利口径）+ 波动调整动量
    #    主连为「主力合约拼接」，换月日会出现非真实跳空（实测 PG0 曾单日 +11.3%），
    #    故用 MAD 缩尾后的复利收益作为主口径——无跳空时与真实涨跌完全一致。
    ret_w = float(np.prod(1.0 + rets_w)) - 1
    jumped = abs(ret_w - ret) > 0.02
    s_ret = math.tanh(ret_w / max(float(cfg["win_scale"]), 0.02))
    s_shp = math.tanh(sharpe / max(float(cfg["sharpe_scale"]), 0.1))
    _add("① 动量（窗口涨跌）", f"{ret * 100:+.1f}%", 0.6 * s_ret + 0.4 * s_shp,
         f"近 {len(rets)} 个交易日累计 {ret * 100:+.1f}%"
         + (f"（缩尾后 {ret_w * 100:+.1f}%，已抑制换月跳空）" if jumped else "")
         + f"；±{float(cfg['win_scale']) * 100:.0f}% 归一，"
         + f"波动调整动量 {sharpe:+.2f}（收益/波动，越高越顺）")

    # ② 趋势强度：Kaufman 效率比 + 回归 R²
    er, r2 = _efficiency(wc)
    lo, hi = float(cfg["er_lo"]), float(cfg["er_hi"])
    er_norm = (er - lo) / max(hi - lo, 1e-6)
    er_norm = min(max(er_norm, 0.0), 1.0)
    strength = 0.6 * er_norm + 0.4 * r2
    s_er = _dir * (strength * 1.6 - 0.3)
    _add("② 趋势强度（效率比+R²）", f"ER {er:.2f} / R² {r2:.2f}", s_er,
         f"效率比 {er:.2f}（净位移/路径长度，越大越顺）· 回归 R² {r2:.2f}；"
         f"方向随窗口涨跌取{'正' if ret >= 0 else '负'}")

    # ③ ADX
    adx, pdi, ndi = _adx(high, low, close)
    d = 1.0 if pdi >= ndi else -1.0
    s_adx = d * math.tanh(adx / max(float(cfg["adx_scale"]), 5.0)) * (1.0 if adx >= 20 else 0.5)
    _add("③ ADX 趋势方向", f"ADX {adx:.0f} / +DI {pdi:.0f} / -DI {ndi:.0f}", s_adx,
         f"{'+DI 占优' if pdi >= ndi else '-DI 占优'}；ADX {adx:.0f}"
         f"（{'≥25 强趋势' if adx >= 25 else ('<20 无趋势·已半数折算' if adx < 20 else '趋势成形')}）")

    # ④ 均线排列
    n = len(close)
    ma5 = float(np.mean(close[-5:]))
    ma20 = float(np.mean(close[-20:]))
    ma60 = float(np.mean(close[-60:])) if n >= 60 else ma20
    s_ma = (0.45 * np.sign(ma5 - ma20) + 0.30 * np.sign(ma20 - ma60)
            + 0.25 * np.sign(last - ma5))
    _add("④ 均线排列（MA5/20/60）", f"{ma5:,.0f}/{ma20:,.0f}/{ma60:,.0f}", s_ma,
         "三线多头" if ma5 > ma20 > ma60 else
         ("三线空头" if ma5 < ma20 < ma60 else "均线纠缠"))

    # ⑤ MACD
    dif = _ema(close, 12) - _ema(close, 26)
    dea = _ema(dif, 9)
    hist = float(dif[-1] - dea[-1])
    h_pct = hist / last * 100 if last else 0.0
    s_macd = (0.4 * (1 if dif[-1] > 0 else -1)
              + 0.3 * (1 if dif[-1] > dea[-1] else -1)
              + 0.3 * math.tanh(h_pct * 3.0))
    _add("⑤ MACD（12/26/9）", f"DIF {dif[-1]:,.1f} / 柱 {h_pct:+.2f}%", s_macd,
         ("DIF 零轴上方" if dif[-1] > 0 else "DIF 零轴下方")
         + ("·金叉" if dif[-1] > dea[-1] else "·死叉"))

    # ⑥ RSI：与 ①③⑤⑦ 统一用 tanh 软饱和。
    #    原线性口径在极端值会撞上 _add 的 ±1 硬截断（rsi=100 → 2.27×0.85≈1.93 → 顶到 1.0），
    #    使 RSI 成为本模块唯一「无软饱和」的因子，极端区贡献被高估、与同类因子不可比。
    rsi = _rsi(close, 14)
    s_rsi = math.tanh((rsi - 50) / max(float(cfg["rsi_scale"]), 5.0))
    extra = ""
    if rsi >= 75:
        s_rsi *= 0.85
        extra = "（≥75 超买，已按 0.85 折算：追高风险）"
    elif rsi <= 25:
        s_rsi *= 0.85
        extra = "（≤25 超卖，已按 0.85 折算：反弹可能）"
    _add("⑥ RSI(14) 动能", f"{rsi:.0f}", s_rsi,
         f"RSI {rsi:.0f}（50 为多空分界）{extra}")

    # ⑦ 持仓量配合
    oi_first = float(wh[0]) if len(wh) else 0.0
    oi_last = float(wh[-1]) if len(wh) else 0.0
    doi = (oi_last / oi_first - 1) if oi_first > 0 else 0.0
    same = (doi >= 0) == (ret >= 0)
    s_oi = _dir * math.tanh(abs(doi) / max(float(cfg["oi_scale"]), 0.05)) \
        * (1.0 if same else 0.5)
    _add("⑦ 持仓量配合", f"{doi * 100:+.1f}%", s_oi,
         f"持仓 {oi_first:,.0f}→{oi_last:,.0f} 手（{doi * 100:+.1f}%）；"
         + ("增仓" if doi >= 0 else "减仓")
         + ("配合价格方向 → 趋势确认" if same else "与价格背离 → 平仓推动（半数折算）"))

    # ⑧ 新闻情绪
    n_hit = len(news_hits or [])
    if cfg.get("with_news", True):
        s_news = max(-1.0, min(1.0, news_sent / 3.0)) if n_hit else 0.0
        _add("⑧ 新闻情绪", f"{n_hit} 条", s_news,
             ("关联新闻情绪净票 %+d（±3 票归一）" % news_sent) if n_hit
             else "无关联新闻 → 中性")

    # f 的顺序与 order 严格一致（新闻因子关闭时 f 少一项）
    order = ["mom", "er", "adx", "ma", "macd", "rsi", "oi", "news"]
    _W = W or FACTOR_W
    total = 0.0
    for k, x in zip(order, f):
        total += _W.get(k, 0.0) * x["score"]
    if not cfg.get("with_news", True):
        total /= max(1e-9, 1.0 - _W.get("news", 0.0))   # 权重归一化回 ±1
    total = max(-100.0, min(100.0, total * 100))

    if total >= 50:
        label = "强势上行"
    elif total >= 20:
        label = "温和上行"
    elif total > -20:
        label = "震荡整理"
    elif total > -50:
        label = "温和下行"
    else:
        label = "强势下行"

    avg_vol = float(np.mean(wv)) if len(wv) else 0.0
    spark = [round(float(x), 4) for x in wc[::max(1, len(wc) // 60)]]
    return {
        "symbol": meta["symbol"], "name": meta["name"],
        "exchange": meta.get("exchange", ""),
        "exchange_name": _EX_SHOW.get(meta.get("exchange", ""), meta.get("exchange", "")),
        "sector": meta.get("sector", "other"),
        "sector_name": meta.get("sector_name", "其它"),
        "last": round(last, 2),
        "last_date": dates[-1],
        "chg_pct": round(ret * 100, 2),
        "avg_vol": round(avg_vol, 0),
        "oi": round(oi_last, 0),
        "score": round(total, 1),
        "label": label,
        "factors": f,
        "news_hits": list(news_hits or []),
        "news_sent": int(news_sent),
        "spark": spark,
    }


# ============ 主入口 ============
def scan(cfg: Optional[dict] = None,
         progress_cb: ProgressCb = None) -> dict:
    """期货视察主入口：主连清单 → 并发拉K → 多因子打分 → 最高/最低各 TopN。"""
    t0 = time.time()
    c = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if v is not None and k in c:
            c[k] = v
    c["weeks"] = int(min(max(float(c["weeks"]), 1), 52))
    c["top_n"] = int(min(max(float(c["top_n"]), 1), 15))
    c["max_news"] = int(min(max(float(c["max_news"]), 0), 30))

    _ensure_ua()
    notes: list = []
    news: list = []

    def _cb(a, b, msg):
        if progress_cb:
            progress_cb(a, b, msg)

    # ---- 1. 主连清单 ----
    _cb(0.05, 1.0, "获取期货主连清单…")
    syms = main_list(force=bool(c.get("refresh_symbols")))
    meta_notes = None
    for s in list(syms):
        if isinstance(s, dict) and "__notes__" in s:
            meta_notes = s["__notes__"]
            syms.remove(s)
    if meta_notes:
        notes.extend(meta_notes)
    if c.get("sectors"):
        want = set(c["sectors"])
        syms = [s for s in syms if s.get("sector") in want]
    if not syms:
        return {"error": "未获取到主连清单（或板块筛选后为空）", "rows": [],
                "top": [], "bottom": [], "news": [], "notes": notes,
                "elapsed": round(time.time() - t0, 2)}

    # ---- 2. 新闻（先取，供因子使用） ----
    if c.get("with_news", True) and c["max_news"] > 0:
        _cb(0.12, 1.0, "获取期货市场新闻…")
        news = fetch_news(c["max_news"])
        if not news:
            notes.append("新闻源暂不可用，新闻因子按中性处理")
    hits, sent = _link_news(news) if news else ({}, {})
    for it in news:
        it["symbols"] = _syms_of(it["title"])

    # ---- 2.5 因子权重（前端「⚙ 视察参数」可覆盖，传入后自动归一化） ----
    W = dict(FACTOR_W)
    _w = c.get("weights")
    if isinstance(_w, dict) and _w:
        for k in list(W):
            if k in _w:
                try:
                    v = float(_w[k])
                    if v >= 0:
                        W[k] = v
                except (TypeError, ValueError):
                    pass
        _ws = sum(W.values())
        W = {k: v / _ws for k, v in W.items()} if _ws > 0 else dict(FACTOR_W)

    # ---- 3. 并发拉主连日K ----
    # 窗口严格按「今天往回推 weeks 个星期」的自然日切分（previously 用 weeks×5
    # 估算交易日数，遇长假窗口会偏长）；窗口前另取 PREHEAT 根作指标预热
    # （MA60 / ADX Wilder 平滑都需要足够历史才收敛）。
    cutoff = (_dt.date.today() - _dt.timedelta(days=int(c["weeks"]) * 7)).isoformat()
    PREHEAT = 130
    _cb(0.2, 1.0, f"拉取 {len(syms)} 个主连日K…")
    bars_map: dict = {}
    fail = 0
    done = [0]

    def _work(m):
        try:
            bars = fetch_kline(m["symbol"])
        except Exception:  # noqa: BLE001
            bars = []
        done[0] += 1
        if progress_cb and done[0] % 8 == 0:
            _cb(0.2 + 0.55 * done[0] / len(syms), 1.0,
                f"拉取主连日K {done[0]}/{len(syms)}…")
        return m["symbol"], bars

    with ThreadPoolExecutor(max_workers=12) as ex:
        for sym, bars in ex.map(_work, syms):
            if bars:
                bars_map[sym] = bars
            else:
                fail += 1
    if fail:
        notes.append(f"{fail} 个主连日K拉取失败（已跳过）")

    # ---- 4. 打分 ----
    _cb(0.8, 1.0, "多因子打分…")
    rows: list = []
    filtered: list = []
    for m in syms:
        bars = bars_map.get(m["symbol"])
        if not bars:
            continue
        # 窗口起点：第一条日期 >= cutoff 的K线；此前 PREHEAT 根作为指标预热段
        idx = len(bars)
        for i, b in enumerate(bars):
            if b["date"] >= cutoff:
                idx = i
                break
        start = max(0, idx - PREHEAT)
        seg = bars[start:]
        # 活跃度过滤：窗口内（cutoff 之后）日均成交量 + 数据新鲜度
        win_bars = bars[idx:] or bars[-20:]
        avg_vol = sum(b["volume"] for b in win_bars) / max(len(win_bars), 1)
        try:
            last_d = _dt.date.fromisoformat(bars[-1]["date"])
            gap = (_dt.date.today() - last_d).days
        except Exception:  # noqa: BLE001
            gap = 999
        # max_gap_days 默认 20：覆盖春节等长假（最长约 11~13 天断档），
        # 否则长假后所有品种都会被误判为僵尸（僵尸品种实测断档均在 1000 天以上）
        if avg_vol < float(c["min_vol"]) or gap > float(c["max_gap_days"]):
            filtered.append({"symbol": m["symbol"], "name": m.get("name", ""),
                             "avg_vol": round(avg_vol, 0), "gap": gap})
            continue
        r = score_symbol(m, seg, c, hits.get(m["symbol"]),
                         sent.get(m["symbol"], 0), n_pre=idx - start, W=W)
        if r:
            rows.append(r)

    if not rows:
        return {"error": "无有效主连数据（全部被过滤或拉取失败）", "rows": [],
                "top": [], "bottom": [], "news": news, "notes": notes,
                "filtered": filtered, "elapsed": round(time.time() - t0, 2)}

    rows.sort(key=lambda x: -x["score"])
    top_n = int(c["top_n"])
    # top = 分数最高的前 N；bottom = 其余品种中分数最低的前 N（避免板块品种
    # 少于 2×top_n 时同品种同时出现在两侧）。组内按绝对值由大到小呈现。
    top = rows[:top_n]
    chosen = {r["symbol"] for r in top}
    bottom = [r for r in reversed(rows) if r["symbol"] not in chosen][:top_n]

    # ---- 5. 板块汇总 ----
    sec: dict = {}
    for r in rows:
        s = sec.setdefault(r["sector"], {"sector": r["sector"],
                                         "sector_name": r["sector_name"],
                                         "n": 0, "sum": 0.0, "best": None})
        s["n"] += 1
        s["sum"] += r["score"]
        if s["best"] is None or r["score"] > s["best"]["score"]:
            s["best"] = {"name": r["name"], "score": r["score"]}
    sectors = [{"sector": v["sector"], "sector_name": v["sector_name"], "n": v["n"],
                "avg_score": round(v["sum"] / v["n"], 1),
                "best_name": (v["best"] or {}).get("name"),
                "best_score": (v["best"] or {}).get("score")}
               for v in sec.values()]
    sectors.sort(key=lambda x: -x["avg_score"])

    _cb(1.0, 1.0, "完成")
    return {
        "date": _dt.date.today().strftime("%Y-%m-%d"),
        "params": {k: c[k] for k in DEFAULTS},
        "weights": W,
        "n_symbols": len(syms),
        "n_scored": len(rows),
        "rows": rows,
        "top": top,
        "bottom": bottom,
        "sectors": sectors,
        "news": news,
        "filtered": filtered,
        "notes": notes,
        "elapsed": round(time.time() - t0, 2),
    }


def _syms_of(title: str) -> list:
    """新闻标题命中的品种名（用于前端展示关联标签）。

    **必须复用 `_hit_sym`**（含「单字关键词需搭配期货上下文词」的守卫）：
    原实现直接 `any(k in title)`，会把「锂电池」标成碳酸锂、「纸巾」标成纸浆，
    与打分/情绪统计用的 `_hit_sym` 口径不一致 —— 同一条新闻标签上关联了某品种、
    评分里却没计入（或反之），用户无法解释分数从哪来。
    """
    out: list = []
    for sym in _hit_sym(title):
        nm = _SYM_META.get(sym, {}).get("name", sym)
        if nm not in out:
            out.append(nm)
    return out[:4]
