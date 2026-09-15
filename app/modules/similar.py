# -*- coding: utf-8 -*-
"""相似股查找（来自 scrensto similar_core，适配本项目数据库）。"""
from typing import Optional

import numpy as np
import pandas as pd

from ..core import db

DEFAULT_REF_DAYS = 30
MIN_SUB_WINDOW = 5
CORR_A_THRESHOLD = 0.90     # a 类「几乎一致」阈值（保留原始严谨标准）
CORR_B_THRESHOLD = -0.70    # b 类「走势相反」严格阈值
CORR_C_THRESHOLD = 0.85     # c 类「时移相似」阈值（保留：不降标准凑结果，无则显示暂无）
DEFAULT_MAX_LAG = 10
TOP_N = 2
W_PRICE = 0.6
W_RET = 0.4
MIN_ROWS = 40

VOL_MODE_IGNORE = "ignore"
VOL_MODE_SIMILAR = "similar"
DEFAULT_VOL_THRESHOLD = 50.0


def _norm(closes): return closes.astype(float) / closes[0] * 100.0


def _pearson(x, y):
    if len(x) < 3: return None
    sx, sy = float(np.std(x)), float(np.std(y))
    if sx == 0 or sy == 0: return None
    return float(np.corrcoef(x, y)[0, 1])


def _zscore(x):
    x = x.astype(float)
    s = float(np.std(x))
    if s == 0: return x - float(np.mean(x))
    return (x - float(np.mean(x))) / s


def volatility(closes):
    if len(closes) < 2: return 0.0
    r = np.diff(closes.astype(float)) / closes[:-1].astype(float)
    return float(np.std(r)) * 100.0


def vol_similar_ok(vt, vc, th):
    if vt <= 1e-9: return vc <= 1e-9
    return abs(vc - vt) / vt * 100.0 <= th


def combined_corr(t, c):
    if len(t) != len(c) or len(t) < 3: return None
    cp = _pearson(_zscore(t), _zscore(c))
    tr = np.diff(t.astype(float)) / t[:-1].astype(float)
    cr_ = np.diff(c.astype(float)) / c[:-1].astype(float)
    cr = _pearson(_zscore(tr), _zscore(cr_))
    if cp is None and cr is None: return None
    if cp is None: return cr
    if cr is None: return cp
    return W_PRICE * cp + W_RET * cr


def _load_target(code: str, lookback_days: int) -> tuple[pd.DataFrame, dict]:
    rconn = db.reader()
    df = pd.read_sql_query(
        f"SELECT date, close FROM daily WHERE code=? ORDER BY date DESC LIMIT ?",
        rconn, params=(str(code), int(lookback_days)))
    if df.empty:
        return df, {}
    df = df.iloc[::-1].reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    m = rconn.execute(
        "SELECT name, sector, exchange FROM meta WHERE code=?",
        (str(code),)).fetchone()
    return df, ({"name": m[0], "sector": m[1],
                 "exchange": m[2]} if m else {})


def _load_all_recent(lookback_days: int, exchange=None, sectors=None,
                     exclude_st: bool = True) -> tuple[dict, dict]:
    """一次性载入全市场近期日线（按范围过滤）。"""
    rconn = db.reader()
    sql = ("SELECT code, date, close FROM daily "
           "WHERE date >= ? ORDER BY code, date")
    cutoff = (pd.Timestamp.today() - pd.Timedelta(days=lookback_days)
              ).strftime("%Y-%m-%d")
    df = pd.read_sql_query(sql, rconn, params=(cutoff,))
    if df.empty:
        return {}, {}
    data: dict[str, pd.DataFrame] = {}
    for code, grp in df.groupby("code"):
        data[str(code)] = grp.reset_index(drop=True)
    sql_meta = "SELECT code, name, sector, exchange, listing_date FROM meta"
    args = []
    if exchange in ("SZ", "SH", "BJ"):
        sql_meta += " WHERE exchange=?"
        args.append(exchange)
    if sectors:
        if args:
            sql_meta += f" AND sector IN ({','.join('?'*len(sectors))})"
        else:
            sql_meta += f" WHERE sector IN ({','.join('?'*len(sectors))})"
        args.extend(sectors)
    meta_rows = rconn.execute(sql_meta, tuple(args)).fetchall()
    meta = {str(c): {"name": n, "sector": s, "exchange": e,
                     "listing_date": ld} for c, n, s, e, ld in meta_rows}
    if exclude_st:
        today = pd.Timestamp.today().date()
        keep = {}
        for c, m in meta.items():
            name = m.get("name", "") or ""
            if "ST" in name.upper() or "退" in name: continue
            ld = m.get("listing_date")
            if ld:
                try:
                    ld_d = pd.to_datetime(ld).date()
                    if (today - ld_d).days < 365: continue
                except Exception:
                    pass
            keep[c] = m
        meta = keep
    return data, meta


def _scan_a(pool, t_close_full, t_dates_full, a_days, top_n,
            vol_mode, vol_threshold):
    best_per_stock = []
    max_l = min(a_days, len(t_close_full))
    min_l = min(max(MIN_SUB_WINDOW, a_days // 2), max_l)
    # 只测 3 个关键长度（完整窗口 / 半窗 / 最短窗），大幅提速
    lengths = sorted(set([max_l, max_l // 2, min_l]))
    for code, name, df in pool:
        c_all = df["close"].to_numpy(dtype=float)
        if len(c_all) < min_l: continue
        best = None
        for length in lengths:
            if length < MIN_SUB_WINDOW or length > len(c_all): continue
            t_seg = t_close_full[-length:]
            c_seg = c_all[-length:]
            corr = combined_corr(t_seg, c_seg)
            if corr is None: continue
            if vol_mode == VOL_MODE_SIMILAR:
                vt, vc = volatility(t_seg), volatility(c_seg)
                if not vol_similar_ok(vt, vc, vol_threshold): continue
            else:
                vt = vc = None
            if best is None or corr > best[0]:
                best = (corr, length, vt, vc)
        if best and best[0] >= CORR_A_THRESHOLD:
            corr, length, vt, vc = best
            t_seg = t_close_full[-length:]
            c_seg = c_all[-length:]
            t_dates = t_dates_full[-length:]
            c_dates = df["date"].astype(str).tolist()[-length:]
            best_per_stock.append({
                "code": code, "name": name, "corr": round(corr, 3),
                "lag": None, "note": "",
                "vol_t": round(vt, 3) if vt is not None else None,
                "vol_c": round(vc, 3) if vc is not None else None,
                "window": {"target_start": t_dates[0], "target_end": t_dates[-1],
                           "cand_start": c_dates[0], "cand_end": c_dates[-1]},
                "chart": {"target": [round(float(v), 2) for v in _norm(t_seg)],
                          "cand": [round(float(v), 2) for v in _norm(c_seg)],
                          "target_dates": t_dates, "cand_dates": c_dates},
            })
    best_per_stock.sort(key=lambda r: -abs(r["corr"]))
    return best_per_stock[:top_n]


def _scan_b(pool, t_close_full, t_dates_full, b_days, top_n,
            vol_mode, vol_threshold):
    length = min(b_days, len(t_close_full))
    if length < 3: return []
    t_seg = t_close_full[-length:]
    t_dates = t_dates_full[-length:]
    vol_t = volatility(t_seg)
    scored = []
    for code, name, df in pool:
        c_all = df["close"].to_numpy(dtype=float)
        if len(c_all) < length: continue
        c_seg = c_all[-length:]
        c_dates = df["date"].astype(str).tolist()[-length:]
        corr = combined_corr(t_seg, c_seg)
        if corr is None: continue
        vol_c = volatility(c_seg)
        if vol_mode == VOL_MODE_SIMILAR and not vol_similar_ok(
                vol_t, vol_c, vol_threshold):
            continue
        scored.append({
            "code": code, "name": name, "corr": round(corr, 3),
            "lag": None, "note": "",
            "vol_t": round(vol_t, 3), "vol_c": round(vol_c, 3),
            "window": {"target_start": t_dates[0], "target_end": t_dates[-1],
                       "cand_start": c_dates[0], "cand_end": c_dates[-1]},
            "chart": {"target": [round(float(v), 2) for v in _norm(t_seg)],
                      "cand": [round(float(v), 2) for v in _norm(c_seg)],
                      "target_dates": t_dates, "cand_dates": c_dates},
        })
    strict = [r for r in scored if r["corr"] <= CORR_B_THRESHOLD]
    if strict:
        strict.sort(key=lambda r: r["corr"])
        return strict[:top_n]
    relaxed = [r for r in scored if r["corr"] < 0]
    relaxed.sort(key=lambda r: r["corr"])
    for r in relaxed[:top_n]:
        r["note"] = "（弱负相关）"
    return relaxed[:top_n]


def _scan_c(pool, t_close_all, t_dates_all, win_end_idx, c_days,
            max_lag, top_n, vol_mode, vol_threshold):
    length = min(c_days, win_end_idx)
    if length < 3: return []
    lags = [l for l in range(-max_lag, max_lag + 1) if l != 0]
    best_per_stock = []
    for code, name, df in pool:
        c_close_all = df["close"].to_numpy(dtype=float)
        c_dates_all = df["date"].astype(str).tolist()
        if len(c_close_all) < length: continue
        best = None
        for lag in lags:
            if lag > 0:
                if win_end_idx < length: continue
                t_win = t_close_all[win_end_idx - length:win_end_idx]
                t_dates = t_dates_all[win_end_idx - length:win_end_idx]
                if len(c_close_all) < length + lag: continue
                c_win = c_close_all[-(length + lag):-lag] if lag > 0 else c_close_all[-length:]
                c_dates = c_dates_all[-(length + lag):-lag] if lag > 0 else c_dates_all[-length:]
            else:
                end_idx = win_end_idx + lag
                if end_idx < length: continue
                t_win = t_close_all[end_idx - length:end_idx]
                t_dates = t_dates_all[end_idx - length:end_idx]
                c_win = c_close_all[-length:]
                c_dates = c_dates_all[-length:]
            if len(t_win) != length or len(c_win) != length: continue
            corr = combined_corr(t_win, c_win)
            if corr is None: continue
            vt, vc = volatility(t_win), volatility(c_win)
            if vol_mode == VOL_MODE_SIMILAR and not vol_similar_ok(
                    vt, vc, vol_threshold):
                continue
            if best is None or corr > best[0]:
                best = (corr, lag, t_win, c_win, t_dates, c_dates, vt, vc)
        if best and best[0] >= CORR_C_THRESHOLD:
            corr, lag, t_win, c_win, t_d, c_d, vt, vc = best
            note = f"候选领先 {lag} 天" if lag > 0 else f"候选落后 {-lag} 天"
            best_per_stock.append({
                "code": code, "name": name, "corr": round(corr, 3),
                "lag": lag, "note": note,
                "vol_t": round(vt, 3), "vol_c": round(vc, 3),
                "window": {"target_start": t_d[0], "target_end": t_d[-1],
                           "cand_start": c_d[0], "cand_end": c_d[-1]},
                "chart": {"target": [round(float(v), 2) for v in _norm(t_win)],
                          "cand": [round(float(v), 2) for v in _norm(c_win)],
                          "target_dates": t_d, "cand_dates": c_d},
            })
    best_per_stock.sort(key=lambda r: -abs(r["corr"]))
    return best_per_stock[:top_n]


def find_similar(target_code: str, ref_days: int = DEFAULT_REF_DAYS,
                 a_days: Optional[int] = None,
                 b_days: Optional[int] = None,
                 c_days: Optional[int] = None,
                 max_lag: int = DEFAULT_MAX_LAG, top_n: int = TOP_N,
                 exclude_st: bool = True, markets: Optional[list] = None,
                 vol_mode: str = VOL_MODE_IGNORE,
                 vol_threshold: float = DEFAULT_VOL_THRESHOLD,
                 lookback_days: int = 500,
                 progress_cb=None) -> dict:
    """相似股查找。"""
    target_code = str(target_code).strip()
    if target_code.isdigit():
        target_code = target_code.zfill(6)
    empty = {"target": None, "a": [], "b": [], "c": [], "error": None}
    lookback_needed = max(lookback_days, ref_days + max_lag + 10)
    t_df, t_meta = _load_target(target_code, lookback_needed)
    if t_df is None or t_df.empty or len(t_df) < MIN_ROWS:
        empty["error"] = f"目标股 {target_code} 数据不存在或不足 {MIN_ROWS} 行"
        return empty
    if progress_cb:
        progress_cb(0.1, 1.0, "加载候选池")
    data, meta = _load_all_recent(lookback_needed,
                                  exchange=markets[0] if markets and len(markets) == 1 else None,
                                  exclude_st=exclude_st)
    if progress_cb:
        progress_cb(0.5, 1.0, f"候选池 {len(data)} 只")
    t_close_all = t_df["close"].to_numpy(dtype=float)
    t_dates_all = t_df["date"].astype(str).tolist()
    ref_days = min(ref_days, len(t_close_all))
    win_end_idx = len(t_close_all)
    t_close_ref = t_close_all[-ref_days:]
    t_dates_ref = t_dates_all[-ref_days:]
    pool = []
    for code, df in data.items():
        if code == target_code: continue
        if df is None or len(df) < MIN_ROWS: continue
        m = meta.get(code, {})
        if markets and len(markets) >= 1:
            ex = (m.get("exchange") or "").upper()
            mk = ({"SH": "沪", "SZ": "深", "BJ": "北"}.get(ex, ""))
            if mk not in markets: continue
        pool.append((code, m.get("name", "") or code, df))
    a_days = a_days or ref_days
    b_days = b_days or ref_days
    c_days = c_days or ref_days
    if progress_cb:
        progress_cb(0.7, 1.0, "并行扫描 a/b/c")
    # 三阶段互不依赖，并行执行提速
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        fa = ex.submit(_scan_a, pool, t_close_ref, t_dates_ref, a_days, top_n,
                       vol_mode, vol_threshold)
        fb = ex.submit(_scan_b, pool, t_close_ref, t_dates_ref, b_days, top_n,
                       vol_mode, vol_threshold)
        fc = ex.submit(_scan_c, pool, t_close_all, t_dates_all, win_end_idx, c_days,
                       max_lag, top_n, vol_mode, vol_threshold)
        a = fa.result()
        b = fb.result()
        c = fc.result()
    if progress_cb:
        progress_cb(1.0, 1.0, "完成")
    return {
        "target": {
            "code": target_code,
            "name": t_meta.get("name", "") or target_code,
            "window_start": t_dates_ref[0],
            "window_end": t_dates_ref[-1],
            "ref_days": len(t_close_ref),
        },
        "a": a, "b": b, "c": c, "error": None,
    }