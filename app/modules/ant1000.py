# -*- coding: utf-8 -*-
"""「持有个1000年试试呢？」· 蚂蚁呀欸 × 超长周期版。

与「蚂蚁呀欸」同源：同样是多因子打分 + 硬过滤的流水线。区别在于时间尺度：
普通蚂蚁看最近 60 个交易日的底部吸筹形态；本模块把窗口拉长到 10/20/25/30 年，
筛选目标变为「以宏观视角看周期波动明显 + 周期存在时间长」的股票 ——
即那些在长历史里经历过多轮大涨大落（而非一路阴跌或只是横盘）的公司。

窗口自适应：股票在所选窗口前未上市时，从上市日起分析（最少 5 年）。

数据层：独立库 data/hist.db（与主库解耦，避免主库膨胀）。
  数据源 = 新浪 stock_zh_a_daily（全历史一次返回，~1.2s/只；实测 qfq 无负值、
  单位稳定为「股」、支持北交所）。东财直连已被 WAF 封禁不可用、腾讯按年分页
  太慢（15s+/只）且不支持北交所 —— 故此处不复用 sync.fetch_daily，直接走新浪。
  指数基准 = 上证指数 sh000001（1991 起，覆盖 30 年窗口）。

分析全部在月线尺度（日线降噪）：趋势回归、最大回撤/反弹、循环配对、熊市韧性。

7 项得分（各 0/1 分，总分固定 0-7 口径，缺失项按 0 计入 —— 与蚂蚁口径一致）：
  周期循环得分 / 双向波动得分 / 均值回复得分 / 周期长存得分 /
  长期超额得分 / 熊市韧性得分 / 周期低位得分
"""
import concurrent.futures
import math
import os
import random
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..core import db
from .ant import _regression_p   # 纯数学（t 分布 p 值回归），无副作用可复用

# ============ 配置（全部可调，前端参数窗口可覆盖） ============
ANT1000_CONFIG: dict = {
    "fetch": {
        "concurrency": 6,          # 网络并发（新浪对低频并发容忍良好）
        "retries": 2,              # 单只重试次数
        "timeout": 25,
        "jitter": 0.4,             # 每只请求前随机等待上限（秒），平滑请求
        "cache_ttl_days": 30,      # 缓存有效期：超期后重新拉取（拿到最新数据）
    },
    "hard_filter": {
        "remove_st": True,
        "min_list_years": 5,       # 上市不足 5 年不参与长周期分析
        "min_months": 60,          # 窗口内最少月线数
        "liquidity_quantile": 0.30,  # 近 36 月平均成交额分位剔除
    },
    "cycle": {
        # ZigZag 反转阈值：价格自当前极值反向 ≥30% 确认一个顶/底（记录粒度）；
        # 配对循环时仍要求「跌 ≥35% + 涨 ≥70%」双重校验（严格性由配对保证）
        "zigzag_thr": 0.30,
        "swing_dd_min": 0.35,      # 循环下跌段最小回撤（顶→底，配对校验）
        "swing_up_min": 0.70,      # 循环上涨段最小涨幅（底→顶，配对校验）
        "bear_dd_min": 0.20,       # 指数熊市段判定（回撤 ≥20%）
        # 循环次数达标线：锚定所选窗口（不因上市晚而放水）
        "min_cycles": {10: 1, 20: 2, 25: 3, 30: 3},
    },
    "score": {
        "max_dd_min": 0.40,        # 双向波动：历史最大回撤 ≥40%
        "max_rebound_min": 0.60,   # 双向波动：自最低点最大反弹 ≥60%
        # 均值回复：|年化趋势| ≤ 阈值（排除极强单边趋势股，如超级成长/崩塌股）。
        # 注意不用「回归 p 值」条件 —— 20+ 年样本下任何小趋势都统计显著，
        # 会把几乎所有股票误杀；直接约束趋势幅度更稳健。
        "trend_annual_max": 10.0,
        "long_exist_ratio": 0.8,   # 周期长存：上市年数 ≥ 所选窗口 × 该比例
        "span_ratio_min": 0.5,     # 周期长存：循环时间跨度 ≥ 窗口 × 该比例
        "downside_resistance": 0.85,  # 熊市韧性：熊市段回撤/指数回撤 ≤ 0.85
        "low_pos_max": 0.50,       # 周期低位：当前价格分位 ≤ 50%
        "high_drop_min": 0.25,     # 周期低位：距窗口高点回撤 ≥ 25%
    },
}

SCORE_COLS = ["周期循环得分", "双向波动得分", "均值回复得分",
              "周期长存得分", "长期超额得分", "熊市韧性得分", "周期低位得分"]

OUTPUT_COLS = ["代码", "名称", "板块", "总分（0-7）", "有效项数", "上市年数",
               "数据月数", "周期循环数", "历史最大回撤%", "最大反弹%",
               "年化波动率%", "趋势年化%", "趋势p值", "当前分位%",
               "距高点回撤%", "长期超额%", "熊市韧性", "熊市段数"]
OUTPUT_COLS += SCORE_COLS
OUTPUT_COLS += ["平均月成交额(亿)", "filter_pass", "failed_conditions",
                "data_quality", "scan_date", "years"]

INDEX_CODE = "IDX:SH000001"      # 上证指数（长历史基准）
_INDEX_AK_SYMBOL = "sh000001"
_HIST_DB_NAME = "hist.db"


# ============ hist.db 连接管理（线程/进程安全） ============
# 主库 db.py 只管 unified_data.db；此库独立自管：
#   - 每线程（含进程池 worker 进程）独立只读连接；
#   - 全局单写连接（WAL，写前持锁）。
_tls = threading.local()
_WRITE_CONN: Optional[sqlite3.Connection] = None
_WRITE_LOCK = threading.Lock()


def hist_path() -> str:
    """由主库路径派生 hist.db 路径（同目录）。"""
    p = db.db_path()
    if not p:
        raise RuntimeError("db.db_path 未初始化")
    return os.path.join(os.path.dirname(os.path.abspath(p)), _HIST_DB_NAME)


def _ro_conn() -> sqlite3.Connection:
    path = hist_path()
    cur = getattr(_tls, "conn", None)
    if getattr(_tls, "path", None) == path and cur is not None:
        return cur
    abs_path = os.path.abspath(path).replace("\\", "/")
    conn = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True, timeout=60)
    conn.execute("PRAGMA cache_size = -65536")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA query_only = ON")
    _tls.conn = conn
    _tls.path = path
    return conn


def _rw_conn() -> sqlite3.Connection:
    global _WRITE_CONN
    if _WRITE_CONN is not None:
        return _WRITE_CONN
    with _WRITE_LOCK:
        if _WRITE_CONN is None:
            path = hist_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size = -65536")
            conn.execute("PRAGMA temp_store = MEMORY")
            _WRITE_CONN = conn
            ensure_schema(conn)
    return _WRITE_CONN


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_hist (
            code TEXT, date TEXT,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, amount REAL,
            PRIMARY KEY (code, date));
        CREATE INDEX IF NOT EXISTS idx_hist_code ON daily_hist(code);
        CREATE TABLE IF NOT EXISTS hist_meta (
            code TEXT PRIMARY KEY, name TEXT, rows INTEGER,
            first_date TEXT, last_date TEXT,
            fetched_at TEXT, ok INTEGER, msg TEXT);
    """)
    conn.commit()


def ensure_db_ready() -> None:
    """确保 hist.db 与表存在（首次运行自动创建）。"""
    _rw_conn()


# ============ 数据拉取（新浪全历史） ============
def _norm_hist(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """归一化新浪日K：日期升序、close>0、去重。"""
    if df is None or df.empty or "date" not in df.columns:
        return None
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
        else:
            out[c] = np.nan
    out = out.dropna(subset=["date", "close"])
    out = out[out["close"] > 0]
    out = out.drop_duplicates(subset=["date"], keep="last")
    out = out.sort_values("date").reset_index(drop=True)
    return out if len(out) else None


def _fetch_hist_sina(code: str, timeout: int = 25) -> Optional[pd.DataFrame]:
    """新浪全历史日K（前复权），一次请求返回全部数据。"""
    import akshare as ak
    from ..core.ranges import code_to_sina_symbol
    symbol = code_to_sina_symbol(code)
    end = date.today().strftime("%Y%m%d")
    df = ak.stock_zh_a_daily(symbol=symbol, start_date="19900101",
                             end_date=end, adjust="qfq")
    return _norm_hist(df)


def _fetch_index_sina(timeout: int = 25) -> Optional[pd.DataFrame]:
    """上证指数全历史（无复权概念）。"""
    import akshare as ak
    end = date.today().strftime("%Y%m%d")
    df = ak.stock_zh_a_daily(symbol=_INDEX_AK_SYMBOL, start_date="19900101",
                             end_date=end, adjust="")
    return _norm_hist(df)


def _write_hist_rows(conn: sqlite3.Connection, code: str, df: pd.DataFrame) -> None:
    rows = [(code, d.strftime("%Y-%m-%d"), float(o), float(h), float(l),
             float(c), float(v) if pd.notna(v) else None,
             float(a) if pd.notna(a) else None)
            for d, o, h, l, c, v, a in zip(
                df["date"], df["open"], df["high"], df["low"],
                df["close"], df["volume"], df["amount"])]
    conn.executemany(
        "INSERT OR REPLACE INTO daily_hist "
        "(code, date, open, high, low, close, volume, amount) "
        "VALUES (?,?,?,?,?,?,?,?)", rows)


def _upsert_hist_meta(conn: sqlite3.Connection, code: str, name: str,
                      rows: int, first_date: str, last_date: str,
                      ok: int, msg: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO hist_meta "
        "(code, name, rows, first_date, last_date, fetched_at, ok, msg) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (code, name, rows, first_date, last_date,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ok, msg))


def _cached_codes(conn: sqlite3.Connection, ttl_days: int) -> set:
    """已缓存且未过期的代码集合。"""
    limit = (datetime.now() - timedelta(days=int(ttl_days))).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT code FROM hist_meta WHERE ok=1 AND fetched_at >= ?", (limit,)
    ).fetchall()
    return {r[0] for r in rows}


def _recent_failed_codes(conn: sqlite3.Connection, ttl_days: int = 7) -> set:
    """最近失败的代码（静默期内不再重试，避免每轮扫描都被个别失败股拖慢）。"""
    limit = (datetime.now() - timedelta(days=int(ttl_days))).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT code FROM hist_meta WHERE ok=0 AND fetched_at >= ?", (limit,)
    ).fetchall()
    return {r[0] for r in rows}


def warmup(cfg: Optional[dict] = None, codes: Optional[list] = None,
           force: bool = False, progress_cb=None) -> dict:
    """批量预热：拉取全历史日K写入 hist.db。

    codes: 指定代码列表（默认取 meta 中上市 ≥ min_list_years 的全部股票）。
    force: 忽略缓存有效期，全部重拉。
    返回 {total, fetched, ok, failed, skipped, elapsed}。
    """
    from ..core.taskctl import CANCEL, TaskCancelled
    cfg = _merge_cfg(cfg)
    fc, hf = cfg["fetch"], cfg["hard_filter"]
    _rw_conn()   # 确保库与表已建

    rconn = db.reader()
    meta = pd.read_sql_query(
        "SELECT code, name, exchange, listing_date FROM meta", rconn)
    if codes is not None:
        meta = meta[meta["code"].astype(str).isin(set(map(str, codes)))]
    else:
        cut = (date.today() - timedelta(days=int(hf["min_list_years"] * 365.25))
               ).strftime("%Y-%m-%d")
        # listing_date 为空或过短的剔除；空值保守保留（极少）
        meta = meta[(meta["listing_date"].isna()) | (meta["listing_date"] == "")
                    | (meta["listing_date"] <= cut)]

    ttl = 0 if force else int(fc["cache_ttl_days"])
    have = _cached_codes(_ro_conn(), ttl) if not force else set()
    todo = [(str(r.code), str(r.name or r.code)) for r in meta.itertuples()
            if str(r.code) not in have]

    total = len(todo)
    result = {"total": total, "fetched": 0, "ok": 0, "failed": 0,
              "skipped": len(meta) - total}
    if total == 0:
        if progress_cb:
            progress_cb(0, 0, "历史数据已全部缓存")
        return result

    t0 = time.time()
    wconn = _rw_conn()
    done = {"n": 0}
    lock = threading.Lock()

    # 指数一并预热
    try:
        if INDEX_CODE not in _cached_codes(_ro_conn(), ttl):
            idf = _fetch_index_sina(timeout=int(fc["timeout"]))
            if idf is not None and len(idf) > 100:
                with _WRITE_LOCK:
                    _write_hist_rows(wconn, INDEX_CODE, idf)
                    _upsert_hist_meta(wconn, INDEX_CODE, "上证指数",
                                      len(idf), str(idf["date"].iloc[0].date()),
                                      str(idf["date"].iloc[-1].date()), 1)
                    wconn.commit()
    except Exception:
        pass

    def _one(code: str, name: str):
        if CANCEL.is_set():
            return None
        if fc.get("jitter"):
            time.sleep(random.uniform(0, float(fc["jitter"])))
        last_err = ""
        for i in range(int(fc["retries"]) + 1):
            try:
                df = _fetch_hist_sina(code, timeout=int(fc["timeout"]))
                if df is not None and len(df) >= 250:
                    return (code, name, df)
                last_err = f"数据不足({0 if df is None else len(df)}行)"
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {str(e)[:80]}"
            if i < int(fc["retries"]):
                time.sleep(1.0 + i)
        return (code, name, last_err)

    workers = max(1, min(int(fc["concurrency"]), 12))
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    cancelled = False
    try:
        futs = {ex.submit(_one, c, n): (c, n) for c, n in todo}
        for fut in concurrent.futures.as_completed(futs):
            if CANCEL.is_set():
                cancelled = True
                for f in futs:
                    f.cancel()
                raise TaskCancelled("历史数据下载已取消")
            res = fut.result()
            if res is None:
                continue
            code, name, payload = res
            with lock:
                done["n"] += 1
                if isinstance(payload, pd.DataFrame):
                    with _WRITE_LOCK:
                        conn = _rw_conn()
                        conn.execute("DELETE FROM daily_hist WHERE code=?", (code,))
                        _write_hist_rows(conn, code, payload)
                        _upsert_hist_meta(
                            conn, code, name, len(payload),
                            str(payload["date"].iloc[0].date()),
                            str(payload["date"].iloc[-1].date()), 1)
                        conn.commit()
                    result["ok"] += 1
                else:
                    with _WRITE_LOCK:
                        conn = _rw_conn()
                        _upsert_hist_meta(conn, code, name, 0, "", "",
                                          0, str(payload))
                        conn.commit()
                    result["failed"] += 1
            result["fetched"] = done["n"]
            if progress_cb and (done["n"] % 10 == 0 or done["n"] == total):
                progress_cb(done["n"], total,
                            f"下载历史数据 {done['n']}/{total} · {name}")
    finally:
        # 取消：不等在跑的任务（cancel_futures 取消未开始的）；正常：等全部收尾。
        # 不用 with 语句 —— 其 __exit__ 的 shutdown(wait=True) 会让取消也长时间阻塞。
        try:
            ex.shutdown(wait=not cancelled, cancel_futures=cancelled)
        except Exception:
            pass
    result["elapsed"] = round(time.time() - t0, 1)
    if progress_cb and not cancelled:
        progress_cb(total, total,
                    f"历史数据就绪（成功 {result['ok']} · 失败 {result['failed']}）")
    return result


def cache_status(cfg: Optional[dict] = None) -> dict:
    """缓存覆盖状态（针对上市 ≥ min_list_years 的股票池）。"""
    cfg = _merge_cfg(cfg)
    hf = cfg["hard_filter"]
    try:
        _rw_conn()
    except Exception:
        return {"total": 0, "ok": 0, "failed": 0, "missing": 0}
    rconn = db.reader()
    meta = pd.read_sql_query(
        "SELECT code, listing_date FROM meta", rconn)
    cut = (date.today() - timedelta(days=int(hf["min_list_years"] * 365.25))
           ).strftime("%Y-%m-%d")
    meta = meta[(meta["listing_date"].isna()) | (meta["listing_date"] == "")
                | (meta["listing_date"] <= cut)]
    codes = set(meta["code"].astype(str))
    hconn = _ro_conn()
    ok = {r[0] for r in hconn.execute(
        "SELECT code FROM hist_meta WHERE ok=1").fetchall()} & codes
    failed = {r[0] for r in hconn.execute(
        "SELECT code FROM hist_meta WHERE ok=0").fetchall()} & codes
    return {"total": len(codes), "ok": len(ok), "failed": len(failed),
            "missing": len(codes - ok - failed)}


# ============ 分析层：月线化 + 周期识别 ============
def _merge_cfg(cfg) -> dict:
    out = {}
    for k, v in ANT1000_CONFIG.items():
        sub = dict(cfg.get(k) or {}) if isinstance(cfg, dict) else {}
        out[k] = {**v, **sub}
    return out


def _to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """日线 → 月线（open=首, high=最高, low=最低, close=末, volume/amount=合计）。

    按实际存在的列自适应（指数查询可能只取 date, close 两列）。
    """
    d = df.copy()
    d["m"] = d["date"].dt.to_period("M")
    aggs = {}
    for c, how in (("open", "first"), ("high", "max"), ("low", "min"),
                   ("close", "last"), ("volume", "sum"), ("amount", "sum")):
        if c in d.columns:
            aggs[c] = (c, how)
    g = d.groupby("m").agg(**aggs)
    g = g.reset_index()
    g["date"] = g["m"].dt.to_timestamp("M")
    return g.drop(columns="m")


def _zigzag(close: np.ndarray, thr: float) -> list:
    """ZigZag 顶底序列：[(idx, price, 'H'/'L')]，顶底严格交替。

    规则：价格自当前极值反向波动 ≥ thr 时确认一个顶/底并反向。
    初始方向为「找底」（上市初期大跌也能捕捉）；序列以「上升中」收尾时
    追加一个未回落确认的虚拟顶 —— 用于统计「已深蹲、正在爬升」的进行中循环。
    """
    n = len(close)
    piv = []
    direction = -1                      # -1 找底 / +1 找顶
    ext_idx, ext_val = 0, float(close[0])
    for i in range(1, n):
        v = float(close[i])
        if direction == -1:
            if v < ext_val:
                ext_idx, ext_val = i, v
            elif v >= ext_val * (1.0 + thr):
                piv.append((ext_idx, ext_val, "L"))
                direction = 1
                ext_idx, ext_val = i, v
        else:
            if v > ext_val:
                ext_idx, ext_val = i, v
            elif v <= ext_val * (1.0 - thr):
                piv.append((ext_idx, ext_val, "H"))
                direction = -1
                ext_idx, ext_val = i, v
    if direction == 1 and piv and ext_idx > piv[-1][0]:
        piv.append((ext_idx, ext_val, "H"))    # 进行中（未回落确认）的顶
    return piv


def _count_cycles(piv: list, dd_min: float, up_min: float) -> list:
    """按「顶→底（跌≥dd_min）→顶（涨≥up_min）」配对循环（不重叠）。

    ZigZag 序列为 H/L 严格交替；取每个人工三元组 (H, L, H) 校验幅度。
    """
    cyc = []
    n = len(piv)
    i = 0
    while i < n and piv[i][2] != "H":
        i += 1
    while i + 2 <= n - 1:
        h0, l0, h1 = piv[i], piv[i + 1], piv[i + 2]
        if h0[2] == "H" and l0[2] == "L" and h1[2] == "H" and h0[1] > 0 and l0[1] > 0:
            dd = 1.0 - l0[1] / h0[1]
            up = h1[1] / l0[1] - 1.0
            if dd >= dd_min and up >= up_min:
                cyc.append((h0[0], l0[0], h1[0]))
        i += 2
    return cyc


def _bear_segments(close: np.ndarray, dd_min: float = 0.20) -> list:
    """指数熊市段：ZigZag(阈值=dd_min) 顶底序列中所有「顶→底」对 [峰idx, 谷idx]。

    注：不用「回撤收复阈值」的朴素扫法 —— A 股 2015 年高点数年未收复，
    会把后续多轮熊市吞并成一个巨型未结束段。ZigZag 以「反弹确认底」切断，
    粒度自然、无吞并。
    """
    piv = _zigzag(close, dd_min)
    segs = []
    for i in range(len(piv) - 1):
        if piv[i][2] == "H" and piv[i + 1][2] == "L" and piv[i][1] > 0:
            dd = 1.0 - piv[i + 1][1] / piv[i][1]
            if dd >= dd_min:
                segs.append((piv[i][0], piv[i + 1][0]))
    return segs


# ============ 进程池 worker ============
_HIST_DF_UNSET = object()
_INDEX_M_UNSET = object()
_INDEX_M = _INDEX_M_UNSET   # worker 进程内惰性加载的指数月线


def _worker_index_monthly() -> Optional[pd.DataFrame]:
    global _INDEX_M
    if _INDEX_M is _INDEX_M_UNSET:
        try:
            conn = _ro_conn()
            df = pd.read_sql_query(
                "SELECT date, close FROM daily_hist WHERE code=? ORDER BY date",
                conn, params=(INDEX_CODE,))
            if df.empty:
                _INDEX_M = None
            else:
                df["date"] = pd.to_datetime(df["date"])
                _INDEX_M = _to_monthly(df)
        except Exception:
            _INDEX_M = None
    return _INDEX_M


def _load_hist(code: str) -> Optional[pd.DataFrame]:
    conn = _ro_conn()
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume, amount FROM daily_hist "
        "WHERE code=? ORDER BY date", conn, params=(str(code),))
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df


def _ant1000_worker(code: str, name: str, sector: str, years: int, cfg: dict,
                    listing_date: str, scan_date_s: str):
    """长周期分析 worker（进程池，纯本地计算）。"""
    hf, cy, sc = cfg["hard_filter"], cfg["cycle"], cfg["score"]
    base = {"代码": code, "名称": name, "板块": sector,
            "总分（0-7）": np.nan, "有效项数": np.nan,
            "years": years,
            "filter_pass": False, "failed_conditions": "",
            "data_quality": "OK", "scan_date": scan_date_s}
    for c in SCORE_COLS:
        base[c] = np.nan
    try:
        d = _load_hist(code)
    except Exception:
        base["data_quality"] = "读取失败"
        return base
    if d is None or d.empty:
        base["data_quality"] = "无历史数据"
        base["failed_conditions"] = "无历史数据"
        return base

    last_date = d["date"].iloc[-1]
    listed = None
    try:
        if listing_date:
            listed = pd.to_datetime(listing_date)
    except Exception:
        listed = None
    ref_start = max(last_date - timedelta(days=int(years * 365.25)),
                    listed if listed is not None else last_date - timedelta(days=int(years * 365.25)))
    sub = d[d["date"] >= ref_start].reset_index(drop=True)
    m = _to_monthly(sub)
    m = m.dropna(subset=["close"]).reset_index(drop=True)
    n = len(m)
    base["数据月数"] = n
    base["上市年数"] = round((last_date - listed).days / 365.25, 1) if listed is not None else np.nan
    if n < int(hf["min_months"]):
        base["failed_conditions"] = f"样本不足({n}月)"
        base["data_quality"] = "SKIP"
        return base

    close = m["close"].to_numpy(dtype=float)
    # ---- 基础指标 ----
    # 趋势（对数回归）
    slope, p_val = _regression_p(np.log(close))
    trend_annual = (math.exp(slope * 12.0) - 1.0) * 100.0 if np.isfinite(slope) else np.nan
    # 最大回撤（月线收盘口径）
    peak_run = np.maximum.accumulate(close)
    dd = close / np.maximum(peak_run, 1e-12) - 1.0
    trough_i = int(np.argmin(dd))
    max_dd = float(-dd.min())
    # 自最低点最大反弹
    max_rebound = float(close[trough_i:].max() / close[trough_i] - 1.0)
    # 年化波动率（月收益）
    rets = close[1:] / close[:-1] - 1.0
    vol_annual = float(np.std(rets, ddof=1) * math.sqrt(12.0) * 100.0) if len(rets) > 2 else np.nan
    # 当前分位 & 距高点回撤
    cur = float(close[-1])
    pos_pct = float((close <= cur).sum()) / n * 100.0
    drop_from_high = float(1.0 - cur / close.max())
    # 近 36 月平均成交额（亿）
    amt = m["amount"].tail(36)
    avg_amt_yi = float(amt.mean()) / 1e8 if len(amt) and pd.notna(amt.mean()) else np.nan

    # ---- 循环识别（ZigZag 顶底 + 幅度配对） ----
    piv = _zigzag(close, float(cy["zigzag_thr"]))
    cyc = _count_cycles(piv, float(cy["swing_dd_min"]), float(cy["swing_up_min"]))
    cycles_n = len(cyc)
    span_ratio = 0.0
    if cycles_n >= 1:
        span = cyc[-1][2] - cyc[0][0] + 1
        span_ratio = float(span) / n

    # ---- 指数对齐（长期超额 / 熊市韧性） ----
    im = _worker_index_monthly()
    excess = np.nan
    down_cap = np.nan
    bear_n = 0
    if im is not None and len(im):
        joint = pd.merge(m[["date", "close"]], im[["date", "close"]],
                         on="date", how="inner", suffixes=("", "_i"))
        if len(joint) >= 6:
            jc = joint["close"].to_numpy(dtype=float)
            ji = joint["close_i"].to_numpy(dtype=float)
            stk_ret = jc[-1] / jc[0] - 1.0
            idx_ret = ji[-1] / ji[0] - 1.0
            excess = float(stk_ret - idx_ret) * 100.0
            segs = _bear_segments(ji, float(cy["bear_dd_min"]))
            bear_n = len(segs)
            # 熊市韧性 = 各熊市段「股票复合跌幅 ÷ 指数复合跌幅」：
            #   <1 跌得比指数少；<0 逆势上涨；>1 跌得比指数多。
            # 用复合（乘积）而非逐段平均 —— 避免「一段大涨 + 一段大跌」在
            # 平均中被互相抵消。
            if segs:
                idx_prod, stk_prod = 1.0, 1.0
                for s0, t0 in segs:
                    idx_dd = 1.0 - ji[t0] / ji[s0]
                    if idx_dd <= 0:
                        continue
                    stk_dd = 1.0 - jc[t0] / jc[s0]
                    idx_prod *= (1.0 - idx_dd)
                    stk_prod *= (1.0 - stk_dd)
                comp_idx = idx_prod - 1.0
                if comp_idx < 0:
                    down_cap = (stk_prod - 1.0) / comp_idx

    # ---- 7 项得分 ----
    scores = {}
    min_cyc = int(cy["min_cycles"].get(int(years), int(cy["min_cycles"].get(10, 1))))
    scores["周期循环得分"] = 1 if cycles_n >= min_cyc else 0
    scores["双向波动得分"] = 1 if (max_dd >= float(sc["max_dd_min"]) and
                                  max_rebound >= float(sc["max_rebound_min"])) else 0
    scores["均值回复得分"] = 1 if (np.isfinite(trend_annual) and
                                  abs(trend_annual) <= float(sc["trend_annual_max"])) else 0
    list_years = base["上市年数"]
    long_ok = (np.isfinite(list_years) and list_years >= float(years) * float(sc["long_exist_ratio"]))
    scores["周期长存得分"] = 1 if (long_ok and span_ratio >= float(sc["span_ratio_min"])) else 0
    scores["长期超额得分"] = (1 if (np.isfinite(excess) and excess > 0) else
                              (np.nan if not np.isfinite(excess) else 0))
    scores["熊市韧性得分"] = (1 if (np.isfinite(down_cap) and
                                    down_cap <= float(sc["downside_resistance"]))
                              else (np.nan if not np.isfinite(down_cap) else 0))
    scores["周期低位得分"] = 1 if (pos_pct <= float(sc["low_pos_max"]) * 100.0 and
                                  drop_from_high >= float(sc["high_drop_min"])) else 0

    items = [(k, v) for k, v in scores.items() if pd.notna(v)]
    base["有效项数"] = len(items)
    if not items:
        base["failed_conditions"] = "无有效评分项"
        return base
    base["总分（0-7）"] = round(float(sum(int(v) for _, v in items)), 3)

    for k in SCORE_COLS:
        base[k] = scores.get(k, np.nan)
    base["周期循环数"] = cycles_n
    base["历史最大回撤%"] = round(max_dd * 100.0, 1)
    base["最大反弹%"] = round(max_rebound * 100.0, 1)
    base["年化波动率%"] = round(vol_annual, 1) if np.isfinite(vol_annual) else np.nan
    base["趋势年化%"] = round(trend_annual, 2) if np.isfinite(trend_annual) else np.nan
    base["趋势p值"] = round(float(p_val), 4)
    base["当前分位%"] = round(pos_pct, 1)
    base["距高点回撤%"] = round(drop_from_high * 100.0, 1)
    base["长期超额%"] = round(float(excess), 1) if np.isfinite(excess) else np.nan
    base["熊市韧性"] = round(float(down_cap), 2) if np.isfinite(down_cap) else np.nan
    base["熊市段数"] = bear_n
    base["平均月成交额(亿)"] = round(avg_amt_yi, 2) if np.isfinite(avg_amt_yi) else np.nan
    base["filter_pass"] = True
    base["failed_conditions"] = ""
    return base


# ============ 主流程 ============
def scan(cfg: Optional[dict] = None, years: int = 20,
         exchange: Optional[str] = None, sectors: Optional[list] = None,
         only_pass: bool = True, auto_fetch: bool = True,
         progress_cb=None) -> pd.DataFrame:
    """长周期扫描。auto_fetch=True 时自动补拉缺失的历史数据。"""
    from ..core import pool as _ppool
    cfg = _merge_cfg(cfg)
    if years not in (10, 20, 25, 30):
        years = 20
    hf = cfg["hard_filter"]
    ensure_db_ready()

    rconn = db.reader()
    meta = pd.read_sql_query(
        "SELECT code, name, sector, exchange, listing_date FROM meta", rconn)
    if exchange in ("SZ", "SH", "BJ"):
        meta = meta[meta["exchange"] == exchange]
    if sectors:
        meta = meta[meta["sector"].isin(set(sectors))]
    if hf.get("remove_st", True):
        meta = meta[~meta["name"].astype(str).str.upper().str.contains("ST|退", na=False)]
    # 上市年限硬过滤（含窗口自适应：上市晚则短窗口，但至少 min_list_years）
    cut = (date.today() - timedelta(days=int(hf["min_list_years"] * 365.25))
           ).strftime("%Y-%m-%d")
    meta = meta[(meta["listing_date"].isna()) | (meta["listing_date"] == "")
                | (meta["listing_date"] <= cut)]
    total = len(meta)
    if total == 0:
        return pd.DataFrame()

    # ---- 1) 补拉缺失历史数据 ----
    if auto_fetch:
        need = [str(c) for c in meta["code"].astype(str)]
        have = _cached_codes(_ro_conn(), int(cfg["fetch"]["cache_ttl_days"]))
        skip_failed = _recent_failed_codes(_ro_conn(), ttl_days=7)
        missing = [c for c in need if c not in have and c not in skip_failed]
        if missing and progress_cb:
            est_min = round(len(missing) * 2.45 / max(1, int(cfg["fetch"]["concurrency"])) / 60, 1)
            progress_cb(0, len(missing),
                        f"首次运行需下载 {len(missing)} 只历史数据（约 {est_min} 分钟，仅一次）")
        if missing:
            warmup(cfg, codes=missing, force=False, progress_cb=progress_cb)

    scan_date_s = date.today().strftime("%Y-%m-%d")
    name_map = dict(zip(meta["code"].astype(str), meta["name"]))
    sector_map = dict(zip(meta["code"].astype(str), meta["sector"]))
    list_map = dict(zip(meta["code"].astype(str),
                        meta["listing_date"].fillna("").astype(str)))
    codes = [str(c) for c in meta["code"]]

    # ---- 2) 进程池分析 ----
    workers = max(1, min(os.cpu_count() or 4, 8))
    ex = _ppool.get_pool(workers, db.db_path())
    records = []
    try:
        done = 0
        for r in ex.map(_ant1000_worker, codes,
                        [name_map.get(c, c) for c in codes],
                        [sector_map.get(c, "未分类") for c in codes],
                        [years] * len(codes), [cfg] * len(codes),
                        [list_map.get(c, "") for c in codes],
                        [scan_date_s] * len(codes),
                        chunksize=16):
            done += 1
            if progress_cb and (done % 100 == 0 or done == total):
                progress_cb(done, total, f"长周期扫描 {done}/{total}")
            if r is not None:
                records.append(r)
    except BaseException:
        _ppool.discard_pool()
        raise
    out = pd.DataFrame(records)
    if out.empty:
        return out

    # ---- 3) 流动性分位剔除（候选池内 30% 分位） ----
    q = float(hf.get("liquidity_quantile", 0.30))
    if q > 0 and "平均月成交额(亿)" in out.columns:
        amt = pd.to_numeric(out["平均月成交额(亿)"], errors="coerce")
        valid = amt.dropna()
        if len(valid) >= 10:
            thresh = float(valid.quantile(q))
            mask = amt.notna() & (amt < thresh) & out["filter_pass"]
            out.loc[mask, "filter_pass"] = False
            out.loc[mask, "failed_conditions"] = "流动性不足"

    # ---- 4) 排序输出 ----
    ranked = out[out["filter_pass"]].copy()
    failed = out[~out["filter_pass"]].copy()
    if not ranked.empty:
        ranked["_amt_sort"] = pd.to_numeric(ranked["平均月成交额(亿)"], errors="coerce").fillna(0)
        ranked = ranked.sort_values(
            ["总分（0-7）", "周期循环数", "年化波动率%", "长期超额%", "_amt_sort"],
            ascending=[False, False, False, False, False],
            na_position="last").drop(columns="_amt_sort").reset_index(drop=True)
        out = ranked if only_pass else pd.concat([ranked, failed], ignore_index=True)
    else:
        out = ranked if only_pass else failed.sort_values("代码").reset_index(drop=True)
    cols = [c for c in OUTPUT_COLS if c in out.columns]
    return out[cols]


# ============ 图表数据（单只股票长周期视图） ============
def chart_data(code: str, years: int = 20, cfg: Optional[dict] = None) -> dict:
    """返回单只股票的长周期月线图数据：月线序列 + 循环顶底 + 指数对比。"""
    cfg = _merge_cfg(cfg or {})
    years = years if years in (10, 20, 25, 30) else 20
    try:
        ensure_db_ready()
    except Exception:
        pass
    conn = _ro_conn()
    rconn = db.reader()
    nm = rconn.execute("SELECT name FROM meta WHERE code=?", (str(code),)).fetchone()
    name = nm[0] if nm else str(code)
    d = _load_hist(str(code))
    if d is None or d.empty:
        return {"code": code, "name": name, "months": [], "pivots": [],
                "cycles": [], "index": [], "error": "无历史数据（请先预热）"}
    last_date = d["date"].iloc[-1]
    ref_start = last_date - timedelta(days=int(years * 365.25))
    sub = d[d["date"] >= ref_start]
    m = _to_monthly(sub).dropna(subset=["close"]).reset_index(drop=True)
    if len(m) < 6:
        return {"code": code, "name": name, "months": [], "pivots": [],
                "cycles": [], "index": [], "error": "样本不足"}

    close = m["close"].to_numpy(dtype=float)
    piv = _zigzag(close, float(cfg["cycle"]["zigzag_thr"]))
    cyc_idx = _count_cycles(piv, float(cfg["cycle"]["swing_dd_min"]),
                            float(cfg["cycle"]["swing_up_min"]))

    pivots = [{"date": m["date"].iloc[i].strftime("%Y-%m"),
               "price": round(float(p), 2), "kind": k} for i, p, k in piv]
    cycles = []
    for h0, l0, h1 in cyc_idx:
        cycles.append({
            "peak0": m["date"].iloc[h0].strftime("%Y-%m"),
            "trough": m["date"].iloc[l0].strftime("%Y-%m"),
            "peak1": m["date"].iloc[h1].strftime("%Y-%m"),
            "dd": round(float(1 - close[l0] / close[h0]) * 100, 1),
            "up": round(float(close[h1] / close[l0] - 1) * 100, 1),
        })

    im = _worker_index_monthly()
    idx_line = []
    if im is not None and len(im):
        joint = pd.merge(m[["date"]], im[["date", "close"]], on="date", how="left")
        base_i = joint["close"].dropna()
        if len(base_i) >= 2:
            b0 = float(base_i.iloc[0])
            for dt_, v in zip(joint["date"], joint["close"]):
                if pd.notna(v):
                    idx_line.append({"date": dt_.strftime("%Y-%m"),
                                     "v": round(float(v) / b0 * 100.0, 2)})

    months = [{"date": dt_.strftime("%Y-%m"), "close": round(float(c), 2),
               "high": round(float(h), 2), "low": round(float(l), 2)}
              for dt_, c, h, l in zip(m["date"], m["close"], m["high"], m["low"])]
    return {"code": code, "name": name, "years": years,
            "months": months, "pivots": pivots, "cycles": cycles,
            "index": idx_line, "error": ""}
