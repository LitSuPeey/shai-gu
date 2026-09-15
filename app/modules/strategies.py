# -*- coding: utf-8 -*-
"""6 大经典量化策略（来自 Sequoia-X，适配本项目统一数据库）。"""
import pandas as pd

from ..core import db


def _bars(code: str, n: int = 0) -> pd.DataFrame:
    rconn = db.reader()
    sql = ("SELECT date, open, high, low, close, volume, amount, turnover "
           "FROM daily WHERE code=? ORDER BY date")
    if n and n > 0:
        sql = (f"SELECT * FROM (SELECT date, open, high, low, close, "
               f"volume, amount, turnover FROM daily WHERE code=? "
               f"ORDER BY date DESC LIMIT {int(n)}) ORDER BY date ASC")
    df = pd.read_sql_query(sql, rconn, params=(str(code),))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _codes(exchange=None, sectors=None):
    """股票池（按范围过滤）。"""
    rconn = db.reader()
    sql = "SELECT code, name, sector FROM meta"
    args = ()
    if exchange in ("SZ", "SH", "BJ"):
        sql += " WHERE exchange=?"
        args = (exchange,)
    if sectors:
        if args:
            sql += f" AND sector IN ({','.join('?'*len(sectors))})"
            args = args + tuple(sectors)
        else:
            sql += f" WHERE sector IN ({','.join('?'*len(sectors))})"
            args = tuple(sectors)
    rows = rconn.execute(sql, args).fetchall()
    return [(str(c), n, s) for c, n, s in rows]


# ========== 1. 海龟交易 ==========
def turtle_trade(exchange=None, sectors=None) -> list[dict]:
    """海龟突破：20日新高 + 成交额过亿 + 阳线防诱多。"""
    results = []
    for code, name, sector in _codes(exchange, sectors):
        try:
            df = _bars(code, 25)
            if len(df) < 21:
                continue
            df["high_20"] = df["high"].shift(1).rolling(20).max()
            last = df.iloc[-1]
            prev = df.iloc[-2]
            if pd.isna(last["high_20"]):
                continue
            breakout = last["close"] > last["high_20"]
            amount = last["amount"] if "amount" in df.columns and pd.notna(
                last.get("amount")) else (last["volume"] * last["close"] * 100)
            liquid = amount > 100_000_000
            is_yang = last["close"] > last["open"]
            is_up = last["close"] > prev["close"]
            if breakout and liquid and is_yang and is_up:
                results.append({
                    "代码": code, "名称": name, "板块": sector,
                    "收盘价": round(float(last["close"]), 3),
                    "前20日最高": round(float(last["high_20"]), 3),
                    "成交额(亿)": round(float(amount) / 1e8, 2),
                    "突破幅度(%)": round(
                        (float(last["close"]) / float(last["high_20"]) - 1) * 100, 2),
                    "数据日期": last["date"].strftime("%Y-%m-%d"),
                })
        except Exception:
            continue
    results.sort(key=lambda r: r.get("突破幅度(%)", 0), reverse=True)
    return results


# ========== 2. 均线放量 ==========
def ma_volume(exchange=None, sectors=None) -> list[dict]:
    """5日均线上穿20日均线 + 当日成交量>20日均量1.5倍。"""
    results = []
    for code, name, sector in _codes(exchange, sectors):
        try:
            df = _bars(code, 30)
            if len(df) < 20:
                continue
            df["ma5"] = df["close"].rolling(5).mean()
            df["ma20"] = df["close"].rolling(20).mean()
            df["vol_ma20"] = df["volume"].rolling(20).mean()
            last, prev = df.iloc[-1], df.iloc[-2]
            if pd.isna(last["ma5"]) or pd.isna(last["ma20"]):
                continue
            cross = prev["ma5"] < prev["ma20"] and last["ma5"] > last["ma20"]
            surge = last["volume"] > last["vol_ma20"] * 1.5
            if cross and surge:
                results.append({
                    "代码": code, "名称": name, "板块": sector,
                    "收盘价": round(float(last["close"]), 3),
                    "MA5": round(float(last["ma5"]), 3),
                    "MA20": round(float(last["ma20"]), 3),
                    "量比(vs20日均量)": round(
                        float(last["volume"]) / float(last["vol_ma20"]), 2),
                    "数据日期": last["date"].strftime("%Y-%m-%d"),
                })
        except Exception:
            continue
    return results


# ========== 3. 高旗形 ==========
def high_tight_flag(exchange=None, sectors=None) -> list[dict]:
    """40日涨幅>60% + 10日收敛振幅<15% + 高位+缩量。"""
    results = []
    for code, name, sector in _codes(exchange, sectors):
        try:
            df = _bars(code, 45)
            if len(df) < 40:
                continue
            tail40 = df.tail(40)
            tail10 = df.tail(10)
            high40 = tail40["high"].max()
            low40 = tail40["low"].min()
            high10 = tail10["high"].max()
            low10 = tail10["low"].min()
            if low40 <= 0 or low10 <= 0:
                continue
            momentum = high40 / low40 > 1.6
            consolidation = high10 / low10 < 1.15
            high_level = low10 >= high40 * 0.8
            vol_ma20 = df["volume"].iloc[-21:-1].mean()
            shrink = df["volume"].iloc[-1] < vol_ma20 * 0.6
            if momentum and consolidation and high_level and shrink:
                last = df.iloc[-1]
                results.append({
                    "代码": code, "名称": name, "板块": sector,
                    "收盘价": round(float(last["close"]), 3),
                    "40日涨幅(%)": round((high40 / low40 - 1) * 100, 1),
                    "10日振幅(%)": round((high10 / low10 - 1) * 100, 1),
                    "量比(vs20日)": round(
                        float(df["volume"].iloc[-1]) / float(vol_ma20), 2),
                    "数据日期": last["date"].strftime("%Y-%m-%d"),
                })
        except Exception:
            continue
    return results


# ========== 4. 涨停洗盘 ==========
def limit_up_shakeout(exchange=None, sectors=None) -> list[dict]:
    """昨日涨停 + 今日放量收阴但未破昨收。"""
    results = []
    for code, name, sector in _codes(exchange, sectors):
        try:
            df = _bars(code, 5)
            if len(df) < 3:
                continue
            prev2, prev1, today = df.iloc[-3], df.iloc[-2], df.iloc[-1]
            limit_up = prev1["close"] >= prev2["close"] * 1.095
            bearish = today["close"] < today["open"]
            surge = today["volume"] > prev1["volume"] * 2.0
            support = today["low"] >= prev1["close"]
            if limit_up and bearish and surge and support:
                results.append({
                    "代码": code, "名称": name, "板块": sector,
                    "收盘价": round(float(today["close"]), 3),
                    "昨日涨幅(%)": round(
                        (float(prev1["close"]) / float(prev2["close"]) - 1) * 100, 2),
                    "今日跌幅(%)": round(
                        (float(today["close"]) / float(today["open"]) - 1) * 100, 2),
                    "放量倍数": round(
                        float(today["volume"]) / float(prev1["volume"]), 2),
                    "数据日期": today["date"].strftime("%Y-%m-%d"),
                })
        except Exception:
            continue
    return results


# ========== 5. 上升跌停 ==========
def uptrend_limit_down(exchange=None, sectors=None) -> list[dict]:
    """上升趋势 + 放量跌停（错杀机会）。"""
    results = []
    for code, name, sector in _codes(exchange, sectors):
        try:
            df = _bars(code, 65)
            if len(df) < 60:
                continue
            df["ma20"] = df["close"].rolling(20).mean()
            df["ma60"] = df["close"].rolling(60).mean()
            df["vol_ma20"] = df["volume"].rolling(20).mean()
            prev, today = df.iloc[-2], df.iloc[-1]
            if pd.isna(prev["ma20"]) or pd.isna(prev["ma60"]) \
                    or pd.isna(today["vol_ma20"]):
                continue
            uptrend = prev["ma20"] > prev["ma60"]
            limit_down = today["close"] <= prev["close"] * 0.905
            surge = today["volume"] > today["vol_ma20"] * 2.0
            if uptrend and limit_down and surge:
                results.append({
                    "代码": code, "名称": name, "板块": sector,
                    "收盘价": round(float(today["close"]), 3),
                    "MA20": round(float(prev["ma20"]), 3),
                    "MA60": round(float(prev["ma60"]), 3),
                    "今日跌幅(%)": round(
                        (float(today["close"]) / float(prev["close"]) - 1) * 100, 2),
                    "放量倍数": round(
                        float(today["volume"]) / float(today["vol_ma20"]), 2),
                    "数据日期": today["date"].strftime("%Y-%m-%d"),
                })
        except Exception:
            continue
    return results


# ========== 6. RPS 突破 ==========
def rps_breakout(exchange=None, sectors=None,
                 rps_period: int = 120, rps_threshold: int = 90,
                 progress_cb=None) -> list[dict]:
    """欧奈尔 RPS 相对强度突破（横截面 RPS≥90 + 接近 120 日新高）。"""
    rconn = db.reader()
    meta_where = ""
    args: list = []
    if exchange in ("SZ", "SH", "BJ"):
        meta_where = " WHERE exchange=?"
        args.append(exchange)
    if sectors:
        if meta_where:
            meta_where += f" AND sector IN ({','.join('?'*len(sectors))})"
        else:
            meta_where = f" WHERE sector IN ({','.join('?'*len(sectors))})"
        args.extend(sectors)
    code_rows = rconn.execute(
        f"SELECT code, name, sector FROM meta{meta_where}", tuple(args)).fetchall()
    if not code_rows:
        return []
    code_map = {str(c): (n, s) for c, n, s in code_rows}
    code_list = list(code_map.keys())
    placeholders = ",".join("?" * len(code_list))
    df = pd.read_sql_query(
        f"SELECT code, date, close, high FROM daily "
        f"WHERE code IN ({placeholders}) ORDER BY code, date",
        rconn, params=tuple(code_list))
    if df.empty:
        return []
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["code", "date"])
    df["close_shift"] = df.groupby("code")["close"].shift(rps_period)
    df["pct_change"] = (df["close"] - df["close_shift"]) / df["close_shift"]
    latest_date = df["date"].max()
    latest = df[df["date"] == latest_date].dropna(subset=["pct_change"]).copy()
    if latest.empty:
        return []
    latest["rps"] = latest["pct_change"].rank(pct=True) * 100
    strong = latest[latest["rps"] >= rps_threshold].copy()
    if strong.empty:
        return []
    roll = df.groupby("code")["high"].rolling(
        window=rps_period, min_periods=rps_period // 2
    ).max().reset_index(level=0, drop=True)
    df["roll_high"] = roll
    roll_latest = df[df["date"] == latest_date][["code", "roll_high"]]
    strong = strong.merge(roll_latest, on="code", how="left")
    strong = strong[strong["close"] >= strong["roll_high"] * 0.90]
    results = []
    for _, r in strong.iterrows():
        code = str(r["code"])
        name, sector = code_map.get(code, (code, "未分类"))
        results.append({
            "代码": code, "名称": name, "板块": sector,
            "收盘价": round(float(r["close"]), 3),
            "RPS(%)": round(float(r["rps"]), 1),
            f"{rps_period}日新高": round(float(r["roll_high"]), 3),
            "距新高(%)": round(
                (float(r["close"]) / float(r["roll_high"]) - 1) * 100, 2),
            "数据日期": latest_date.strftime("%Y-%m-%d"),
        })
    results.sort(key=lambda x: x["RPS(%)"], reverse=True)
    return results


# ========== 调度入口 ==========
STRATEGIES = {
    "turtle":  {"label": "🐢 海龟突破",       "fn": turtle_trade},
    "ma_vol":  {"label": "📈 均线放量",       "fn": ma_volume},
    "flag":    {"label": "🚩 高旗形整理",     "fn": high_tight_flag},
    "shake":   {"label": "💥 涨停洗盘",       "fn": limit_up_shakeout},
    "limit_d": {"label": "📉 上升跌停",       "fn": uptrend_limit_down},
    "rps":     {"label": "⚡ RPS突破",        "fn": rps_breakout},
}


def run(strategy_keys: list[str], exchange=None, sectors=None,
        progress_cb=None) -> dict[str, list[dict]]:
    """运行所选策略，返回 {strategy_key: [结果…]}。"""
    out = {}
    for k in strategy_keys:
        if k not in STRATEGIES:
            continue
        if progress_cb:
            progress_cb(k, "计算中…")
        out[k] = STRATEGIES[k]["fn"](exchange=exchange, sectors=sectors)
        if progress_cb:
            progress_cb(k, f"完成: {len(out[k])} 只")
    return out