# -*- coding: utf-8 -*-
"""股票范围过滤：按 深/沪/北/全部 + 申万板块过滤 meta。"""
from typing import Iterable, Optional

import pandas as pd

from . import db


def filter_meta_codes(
    exchange: Optional[str] = None,
    sectors: Optional[Iterable[str]] = None,
    extra_codes: Optional[Iterable[str]] = None,
) -> list[str]:
    """按交易所和板块过滤 meta，返回股票代码列表。

    exchange: None/''/ALL 表示全部；'SZ' 深证；'SH' 沪证；'BJ' 北证。
    sectors: 申万板块列表（OR 关系）。
    extra_codes: 额外限制到这些代码（与上述 AND）。
    """
    conn = db.reader()
    sql = "SELECT code, name, sector, exchange, listing_date FROM meta"
    df = pd.read_sql_query(sql, conn)
    if exchange in ("SZ", "SH", "BJ"):
        df = df[df["exchange"] == exchange]
    if sectors:
        df = df[df["sector"].isin(set(sectors))]
    if extra_codes is not None:
        df = df[df["code"].isin(set(extra_codes))]
    return df["code"].astype(str).tolist()


def filter_meta_df(
    exchange: Optional[str] = None,
    sectors: Optional[Iterable[str]] = None,
    codes: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """返回完整 meta DataFrame（带 name/sector/exchange/listing_date）。"""
    conn = db.reader()
    df = pd.read_sql_query(
        "SELECT code, name, sector, exchange, listing_date FROM meta", conn)
    if exchange in ("SZ", "SH", "BJ"):
        df = df[df["exchange"] == exchange]
    if sectors:
        df = df[df["sector"].isin(set(sectors))]
    if codes is not None:
        df = df[df["code"].isin(set(codes))]
    return df.reset_index(drop=True)


def sector_choices() -> list[str]:
    """从 sector_map 中读取所有出现过的板块（去重排序）。"""
    conn = db.reader()
    rows = conn.execute(
        "SELECT DISTINCT sector FROM sector_map WHERE sector IS NOT NULL "
        "AND sector != '' AND sector != '未分类' ORDER BY sector"
    ).fetchall()
    return [r[0] for r in rows]


def exchange_of(code: str) -> str:
    """代码 -> 交易所。"""
    c = str(code)
    if c.startswith(("4", "8", "92")):
        return "BJ"
    if c.startswith("6"):
        return "SH"
    if c.startswith(("0", "2", "3")):
        return "SZ"
    return "?"


def code_to_sina_symbol(code: str) -> str:
    c = str(code)
    if c.startswith(("4", "8", "92")):
        return "bj" + c
    if c.startswith("6"):
        return "sh" + c
    return "sz" + c