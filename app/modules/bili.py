# -*- coding: utf-8 -*-
"""「仅供学习」模块 —— B站指定 UP主 内容中的股票提及挖掘。

======================================================================
定位：从**用户指定的 UP主本人发布**的 视频 / 动态 / 字幕 / 评论
      中提取股票（代码或简称）提及，按权重排序，并支持一键总结。

【内容来源口径（2026-09-10 修正，重要）】
  · 「UP主内容」严格指 **该 uid 账号本人发布/撰写** 的内容：
      - 投稿视频（archive/cursor 只返回本人投稿，天然正确）
      - 动态：**只取本人撰写的正文（desc / opus）**。
            纯转发（自己一句话不说）= **完全不计入**；
            转发并附言 = 附言算本人观点（高权重），被转的原文另算低权重。
            这样既不会把别人的搬运/转载当成 UP主 观点，也不会漏掉转评里的点评。
  · 其它用户在你的评论区里说的话 → 永远是低权重，绝不冒充 UP主 观点。

【风控安全设计（第一优先级）】—— 实测标定见 .workbuddy/probe_bili*.py
  1. **TLS 指纹**：用 `curl_cffi` + impersonate="chrome124" 复刻真实 Chrome 的
     TLS/JA3 指纹。这是关键：python-requests 下 `x/polymer/web-dynamic` 恒定
     返回 -352/-412（实测 0/5 成功），换 curl_cffi 后同一会话即可成功。
  2. **会话预热**：home(取 buvid3/b_nut) → finger/spi(取 buvid3/buvid4) →
     GenWebTicket(POST, HMAC-SHA256 取 bili_ticket) → nav(取 WBI img_key/sub_key)。
     预热结果落盘 data/bili_session.json 复用（默认 6h），避免每次开扫描都预热。
  3. **全局限速**：所有请求串行 + 最小间隔（默认 4.5s，±1.5s 抖动）。绝不并发。
  4. **退避重试**：-352（风控校验失败）/ -412（request was banned）时指数退避，
     最多 5 次；连续 -412 会重建会话（换 buvid/ticket）后继续。
  5. **硬闸**：单次扫描请求总数上限（默认 260），达到即停止并如实告知未抓完。
  6. **磁盘缓存**：所有抓取结果按 TTL 落盘，重复扫描不再打网络。
  7. **不做的事**：不碰会员/充电专属内容、不自动登录（不实现扫码/密码）。
     拿不到的内容直接跳过，不尝试绕过。
  8. **本地视频文稿（可选，off by default）**：不抓音视频流，仅当本机已存在
     `tools/yt-dlp`/`ffmpeg` 且用户显式开启时，才把视频**落盘到本地**再用
     语音识别转文字；默认关闭。详见 `video_local` 配置与 `_local_transcript()`。

【数据源】
  · UP主信息      x/space/wbi/acc/info                 （WBI）
  · 投稿视频列表  app.bilibili.com/x/v2/space/archive/cursor（APP 签名，最稳）
  · 动态列表      x/polymer/web-dynamic/v1/feed/space   （WBI，需重试）
  · 视频详情+正文 x/web-interface/view                  （含 desc）
  · 视频字幕      x/player/wbi/v2 → subtitle_url         （免费字幕，常有 AI 字幕）
  · 评论          x/v2/reply（type=1 视频 / 17 动态）+ x/v2/reply/reply（楼中楼）

【权重模型】
  高权重（w_up，默认 3.0）：所选 UP主 **本人撰写** 的文字
                            （视频标题/简介/字幕、动态正文、转发附言）
                            以及 UP主 自己在自己内容下发的评论
  低权重（w_other，默认 1.0）：其它用户在所选 UP主 内容/评论 下的评论
                               + 被转发内容的原文（非本人所写）
  股票排序分 = Σ 提及权重；同分按最近提及时间降序。
  展开明细排序：① 是否 UP主本人 ② 时间降序（严格按需求）。
======================================================================
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

ProgressCb = Optional[Callable[[float, float, str], None]]

_log = None


def _log_():
    """惰性取 logger（与项目其它模块一致，避免 import 期副作用）。"""
    global _log
    if _log is None:
        import logging
        _log = logging.getLogger("bili")
    return _log


# ============ 配置 ============
DEFAULTS: dict = {
    "days": 30,                 # 时间范围（自然日）
    "use_local": True,          # 优先使用本地存档：只抓「未覆盖」的尾部（增量），命中则跳过
    "min_interval": 4.5,        # 全局最小请求间隔（秒）——风控安全阀，不建议低于 3
    "jitter": 1.5,              # 间隔抖动上限（秒）
    "max_tries": 5,             # 单请求最大尝试次数
    "backoff": 4.0,             # 退避基数（秒）：第 n 次失败等 min(backoff*n, 15)
    "max_requests": 260,        # 单次扫描请求数硬上限
    "video_pages": 2,           # 投稿列表页数（每页 20 条）
    "max_videos": 40,           # 最多分析的视频数
    "dyn_pages": 3,             # 动态翻页数（每页约 12~20 条）
    "max_dyns": 60,             # 最多分析的动态数
    "comment_pages_hot": 2,     # 每视频/动态：热度排序评论页数
    "comment_pages_new": 1,     # 每视频/动态：最新排序评论页数
    "sub_reply_pages": 1,       # 楼中楼页数（仅对 UP主自身评论 + 最热评论）
    "with_subtitle": True,      # 是否取视频字幕（**字幕现已需登录**，见 sessdata）
    "with_dynamic": True,       # 是否抓动态（此接口风控最严，失败会自动降级）
    "video_local": False,       # 是否「本地下载视频 + 语音转文字」补全文稿（默认关）
    "video_local_max": 5,       # 单轮最多转写几个视频（转写很慢，务必从严）
    "video_local_dir": "",      # 本地视频/文稿目录；留空 = data/bili_video
    "ctx_min_len": 3,           # 简称长度 ≤ 该值时必须搭配股票语境词（挡「太平洋战争」）
    "w_up": 3.0,                # 权重：UP主自己（视频/动态/评论）
    "w_other": 1.0,             # 权重：其它用户的评论 / 被转发的原文
    "ttl_info": 7 * 86400,      # 缓存 TTL（秒）
    "ttl_videos": 6 * 3600,
    "ttl_dyns": 6 * 3600,
    "ttl_view": 7 * 86400,
    "ttl_subtitle": 7 * 86400,
    "ttl_comments": 24 * 3600,
}

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA = os.path.join(_ROOT, "data")
_BILI_DIR = os.path.join(_DATA, "bili_cache")
_UPS_FILE = os.path.join(_DATA, "bili_ups.json")
_SESSION_FILE = os.path.join(_DATA, "bili_session.json")
_CRED_FILE = os.path.join(_DATA, "bili_cred.json")
_VIDEO_DIR = os.path.join(_DATA, "bili_video")

_IMPERSONATE = "chrome124"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# WBI 混淆表（公开常量）
_MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
              27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
              37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
              22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]
_APPKEY = "1d8b6e7d45233436"
_APPSEC = "560c52ccd288fed045859ed18bffd973"


# ============ 限速器 ============
class _Limiter:
    """全局串行限速：任何网络请求都必须先过这里。绝不并发（风控安全的核心）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._last = 0.0
        self._min = DEFAULTS["min_interval"]
        self._jitter = DEFAULTS["jitter"]
        self.count = 0

    def configure(self, min_interval: float, jitter: float) -> None:
        with self._lock:
            self._min = max(1.0, float(min_interval))
            self._jitter = max(0.0, float(jitter))

    def wait(self) -> None:
        with self._lock:
            gap = self._min + (time.time() % 1) * self._jitter  # 伪随机抖动
            d = gap - (time.time() - self._last)
            if d > 0:
                time.sleep(d)
            self._last = time.time()
            self.count += 1


LIMITER = _Limiter()


class BiliBlocked(RuntimeError):
    """风控拦截（-352/-412）且已达重试上限。"""


class BudgetExceeded(RuntimeError):
    """超出单次扫描的请求数硬闸。"""


class _Budget:
    def __init__(self, cap: int):
        self.cap = int(cap)
        self.used = 0

    def spend(self) -> None:
        self.used += 1
        if self.used > self.cap:
            raise BudgetExceeded(
                f"已达单次扫描请求上限 {self.cap}（风控安全闸）；"
                f"请缩小时间范围后重试")


BUDGET: Optional[_Budget] = None
CM_FAIL = [0]   # 本轮评论接口失败次数（供 scan 提示用户）


# ============ 磁盘缓存 ============
def _cache_path(key: str) -> str:
    safe = re.sub(r"[^\w\-.]", "_", key)[:120]
    return os.path.join(_BILI_DIR, safe + ".json")


def _cache_get(key: str, ttl: int):
    p = _cache_path(key)
    try:
        if time.time() - os.path.getmtime(p) <= ttl:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except Exception:  # noqa: BLE001
        pass
    return None


def _cache_put(key: str, data) -> None:
    try:
        os.makedirs(_BILI_DIR, exist_ok=True)
        tmp = _cache_path(key) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _cache_path(key))
    except Exception as e:  # noqa: BLE001
        _log_().warning(f"缓存写入失败 {key}: {e}")


def cache_stats() -> dict:
    n, size = 0, 0
    try:
        for fn in os.listdir(_BILI_DIR):
            if fn.endswith(".json"):
                n += 1
                size += os.path.getsize(os.path.join(_BILI_DIR, fn))
    except Exception:  # noqa: BLE001
        pass
    return {"files": n, "mb": round(size / 1048576, 2)}


def clear_cache() -> dict:
    n = 0
    try:
        for fn in os.listdir(_BILI_DIR):
            if fn.endswith(".json"):
                os.remove(os.path.join(_BILI_DIR, fn))
                n += 1
    except Exception:  # noqa: BLE001
        pass
    return {"removed": n}


# ============ 本地存档（跨扫描持久复用 · 增量抓取） ============
# 目的（用户需求）：把过去抓过的 视频/动态/评论 提及以**简略文本**长期存本地，
# 下次用同一 UP主 时先看本地已覆盖的时间段 —— 已覆盖的部分直接复用、不再打网络，
# 只抓「本地没有的尾部」；边界那天也重拉一遍（用 id 去重，重复内容直接跳过）。
# 每个 UP主 独立一份存档（视频/动态两条流各有覆盖区间），因此多 UP主 可各用各的策略。
#
# 覆盖区间语义：[min, max] 表示该流在这段时间内已被「完整枚举并处理」。
#   · B站 列表按时间倒序 → 取到最老一条即知「比它新的都拿到了」→ [最老, now] 覆盖。
#   · 请求窗口比 [min,max] 更早 → 需补历史（全量重抓该窗口）；
#     落在区间内或之后 → 只增量抓 (max 那天 00:00, now]，其余用存档补齐。
_ARCHIVE_DIR = os.path.join(_DATA, "bili_archive")
_ARCHIVE_LOCK = threading.RLock()


def _archive_path(uid) -> str:
    digits = re.sub(r"[^0-9]", "", str(uid)) or "x"
    return os.path.join(_ARCHIVE_DIR, f"{digits[:18]}.json")


def _empty_archive(uid) -> dict:
    return {"uid": str(uid), "name": "", "updated_at": "",
            "cov": {"video": {"min": 0, "max": 0}, "dynamic": {"min": 0, "max": 0}},
            "done": {"video": {}, "dynamic": {}},
            "mentions": {}, "unit_count": 0, "mention_count": 0}


def load_archive(uid) -> dict:
    """读取某 UP主 的本地存档（不存在则返回空结构）。"""
    with _ARCHIVE_LOCK:
        base = _empty_archive(uid)
        try:
            with open(_archive_path(uid), encoding="utf-8") as f:
                a = json.load(f)
        except Exception:  # noqa: BLE001
            return base
        for k in base:
            if k in a:
                base[k] = a[k]
        for s in ("video", "dynamic"):
            base["cov"].setdefault(s, {"min": 0, "max": 0})
            base["done"].setdefault(s, {})
        base["mentions"] = base.get("mentions") or {}
        return base


def save_archive(arc: dict) -> None:
    """原子写回存档。"""
    try:
        os.makedirs(_ARCHIVE_DIR, exist_ok=True)
        arc["unit_count"] = sum(len(arc["done"].get(s) or {})
                               for s in ("video", "dynamic"))
        arc["mention_count"] = len(arc.get("mentions") or {})
        arc["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        p = _archive_path(arc["uid"])
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(arc, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, p)
    except Exception as e:  # noqa: BLE001
        _log_().warning(f"存档写入失败 {arc.get('uid')}: {e}")


def _mkey(m: dict) -> str:
    """提及去重键：评论用 rpid（同一条评论跨视频/动态只留一次），其余用 url。"""
    idp = m.get("rpid") or m.get("url") or ""
    return f"{m.get('source')}|{idp}|{m.get('code')}"


def _floor_day(ts) -> float:
    """取该时间戳当天的 00:00（边界日重拉用）。"""
    try:
        return datetime.fromtimestamp(float(ts)).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
    except Exception:  # noqa: BLE001
        return float(ts or 0)


def archive_plan(arc: dict, win_start, now) -> dict:
    """按本地覆盖情况给出每个流的抓取计划。

    返回 {"video": {...}, "dynamic": {...}}，每项：
      {"fetch": bool, "from": ts|None, "reason": str}
    规则（对应用户示例）：
      · 本地无数据            → 全量抓 [win_start, now]
      · 窗口早于本地覆盖区间  → 需补更早历史，全量抓窗口
      · 本地已覆盖到当下      → 不抓，纯本地复用
      · 其余                  → 增量：从「已覆盖最大时间那天 00:00」抓到当下
    """
    W0 = win_start.timestamp()
    N = now.timestamp()
    out = {}
    for s in ("video", "dynamic"):
        mn = float((arc["cov"].get(s) or {}).get("min") or 0)
        mx = float((arc["cov"].get(s) or {}).get("max") or 0)
        if mx <= 0:
            out[s] = {"fetch": True, "from": W0, "reason": "本地无数据"}
        elif W0 < mn - 86400:
            out[s] = {"fetch": True, "from": W0, "reason": "补更早历史"}
        elif mx >= N - 3600:
            out[s] = {"fetch": False, "from": None, "reason": "已覆盖至当下"}
        else:
            out[s] = {"fetch": True, "from": max(W0, _floor_day(mx)),
                      "reason": "增量补齐"}
    return out


def archive_stats() -> dict:
    """所有 UP主 的存档概览（供前端展示）。"""
    ups = {str(u.get("uid")): u for u in load_ups()}
    items = []
    total_mentions = 0
    total_bytes = 0
    try:
        names = os.listdir(_ARCHIVE_DIR)
    except Exception:  # noqa: BLE001
        names = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        uid = fn[:-5]
        arc = load_archive(uid)
        total_mentions += len(arc.get("mentions") or {})
        try:
            total_bytes += os.path.getsize(os.path.join(_ARCHIVE_DIR, fn))
        except Exception:  # noqa: BLE001
            pass
        cov = {}
        for s in ("video", "dynamic"):
            mx = float((arc["cov"].get(s) or {}).get("max") or 0)
            mn = float((arc["cov"].get(s) or {}).get("min") or 0)
            cov[s] = {"from": _fmt_ts(mn)[:10], "to": _fmt_ts(mx)[:10],
                      "covered": bool(mx)}
        items.append({"uid": uid,
                      "name": (ups.get(uid) or {}).get("alias")
                              or (ups.get(uid) or {}).get("name")
                              or arc.get("name") or uid,
                      "mentions": len(arc.get("mentions") or {}),
                      "units": arc.get("unit_count", 0),
                      "updated_at": arc.get("updated_at") or "",
                      "video": cov["video"], "dynamic": cov["dynamic"]})
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"ups": items, "total_mentions": total_mentions,
            "mb": round(total_bytes / 1048576, 2), "dir": _ARCHIVE_DIR}


def clear_archive(uid=None) -> dict:
    """清空存档：指定 uid 则只清该 UP主，否则全清。"""
    n = 0
    try:
        if uid:
            try:
                os.remove(_archive_path(uid))
                n = 1
            except Exception:  # noqa: BLE001
                n = 0
        else:
            for fn in os.listdir(_ARCHIVE_DIR):
                if fn.endswith(".json"):
                    os.remove(os.path.join(_ARCHIVE_DIR, fn))
                    n += 1
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "removed": n, **archive_stats()}


# ============ 会话（curl_cffi + 预热 + WBI） ============
_SESS = None
_SESS_LOCK = threading.RLock()
_WBI = {"mk": None, "ts": 0.0, "ttl": 3600.0}
_SESS_TTL = 6 * 3600
_consecutive_banned = [0]


# ============ 可选凭据：用户自己的 SESSDATA ============
# 为什么需要：B站 自 2025 起 `x/player/wbi/v2` 返回 need_login_subtitle=True，
# **视频字幕（含免费 AI 字幕）对匿名访问已关闭**。字幕是「视频正文里的股票提及」
# 的主要来源，因此提供**可选**的自己账号 Cookie：
#   · 默认关闭，不填也能用（标题/简介/动态/评论 照常抓取）；
#   · 只在本地 data/bili_cred.json 明文保存（本机自用），可一键清除；
#   · 不做任何自动登录（无扫码/密码），只是把你浏览器里已有的会话带进来；
#   · 仍是串行 + 限速 + 只读，等同于你自己用浏览器低频浏览，风险最低。
def _load_cred() -> dict:
    try:
        with open(_CRED_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return {"sessdata": str(d.get("sessdata") or "").strip(),
                "saved_at": d.get("saved_at") or ""}
    except Exception:  # noqa: BLE001
        return {"sessdata": "", "saved_at": ""}


def set_sessdata(value: str) -> dict:
    v = str(value or "").strip()
    # 允许直接粘贴整段 Cookie：自动从中提取 SESSDATA=xxx
    if "SESSDATA=" in v:
        m = re.search(r"SESSDATA=([^;\s]+)", v)
        v = m.group(1) if m else ""
    if not v:
        return {"ok": False, "msg": "未识别到 SESSDATA"}
    os.makedirs(_DATA, exist_ok=True)
    with open(_CRED_FILE, "w", encoding="utf-8") as f:
        json.dump({"sessdata": v,
                   "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f,
                  ensure_ascii=False, indent=1)
    try:
        os.chmod(_CRED_FILE, 0o600)
    except Exception:  # noqa: BLE001
        pass
    _sess(force_new=True)          # 立即带凭据重建会话
    return {"ok": True, "msg": "已保存并在本机生效（仅存于本地，可随时清除）",
            **cred_state()}


def clear_sessdata() -> dict:
    try:
        os.remove(_CRED_FILE)
    except Exception:  # noqa: BLE001
        pass
    _sess(force_new=True)
    return {"ok": True, "msg": "已清除", **cred_state()}


def cred_state() -> dict:
    c = _load_cred()
    v = c["sessdata"]
    masked = (v[:4] + "…" + v[-4:]) if len(v) > 10 else ("已设置" if v else "")
    return {"has_sessdata": bool(v), "sessdata_masked": masked,
            "saved_at": c["saved_at"]}


def _apply_cred(s) -> bool:
    """把本地凭据注入会话；返回是否有凭据。"""
    c = _load_cred()
    if not c["sessdata"]:
        return False
    s.cookies.set("SESSDATA", c["sessdata"], domain=".bilibili.com")
    return True


def _new_session(cfg: Optional[dict] = None):
    """建立 curl_cffi 会话并完成预热：buvid3/4 + b_nut + bili_ticket + WBI keys。"""
    from curl_cffi import requests as cq
    s = cq.Session(impersonate=_IMPERSONATE, headers={
        "User-Agent": _UA,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    has_cred = _apply_cred(s)      # 可选：用户自己的 SESSDATA（解锁字幕）
    # 1) 主站 HTML：拿 buvid3 / b_nut
    _raw_get(s, "https://www.bilibili.com/", None, 12)
    # 2) finger/spi：拿 buvid3 / buvid4（显式注入，比依赖 Set-Cookie 稳）
    j = _raw_json(s, "https://api.bilibili.com/x/frontend/finger/spi", None)
    d = (j or {}).get("data") or {}
    if d.get("b_3"):
        for k, v in (("buvid3", d["b_3"]), ("buvid4", d.get("b_4") or ""),
                     ("b_nut", str(int(time.time())))):
            if v:
                s.cookies.set(k, v, domain=".bilibili.com")
    # 3) GenWebTicket（POST + HMAC-SHA256）：拿 bili_ticket
    ts = int(time.time())
    hexsign = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode(), hashlib.sha256).hexdigest()
    t = _raw_json(s, "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket",
                  {"key_id": "ec02", "hexsign": hexsign, "context[ts]": ts, "csrf": ""},
                  method="POST")
    td = (t or {}).get("data") or {}
    if td.get("ticket"):
        s.cookies.set("bili_ticket", td["ticket"], domain=".bilibili.com")
    # 4) nav：拿 WBI keys
    nav = _raw_json(s, "https://api.bilibili.com/x/web-interface/nav", None)
    wi = ((nav or {}).get("data") or {}).get("wbi_img") or {}
    img = (wi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub = (wi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    if img and sub:
        _WBI["mk"] = "".join((img + sub)[i] for i in _MIXIN_TAB)[:32]
        _WBI["ts"] = time.time()
    if cfg:
        LIMITER.configure(cfg.get("min_interval", DEFAULTS["min_interval"]),
                          cfg.get("jitter", DEFAULTS["jitter"]))
    _save_session(s)
    _log_().info(f"B站会话就绪：cookies={list(s.cookies.get_dict().keys())} "
                 f"wbi={'OK' if _WBI['mk'] else 'FAIL'}")
    return s


def _raw_get(s, url: str, params, timeout: int = 12):
    LIMITER.wait()
    if BUDGET is not None:
        BUDGET.spend()
    return s.get(url, params=params, timeout=timeout)


def _raw_json(s, url: str, params, method: str = "GET", timeout: int = 12):
    try:
        # 预热阶段的 4 个请求不计入预算（它们固定在会话建立时发生）
        LIMITER.wait()
        if method == "POST":
            r = s.post(url, params=params, timeout=timeout)
        else:
            r = s.get(url, params=params, timeout=timeout)
        return r.json()
    except Exception as e:  # noqa: BLE001
        _log_().warning(f"预热请求失败 {url.split('/')[-1]}: {type(e).__name__}")
        return None


def _save_session(s) -> None:
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(),
                       "cookies": s.cookies.get_dict(),
                       "mk": _WBI["mk"], "mk_ts": _WBI["ts"]}, f,
                      ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        pass


def _load_session(cfg: Optional[dict]) -> bool:
    """复用未过期的落盘会话（省掉 4 次预热请求）。"""
    global _SESS
    try:
        if time.time() - os.path.getmtime(_SESSION_FILE) > _SESS_TTL:
            return False
        with open(_SESSION_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:  # noqa: BLE001
        return False
    if not d.get("cookies") or not d.get("mk"):
        return False
    from curl_cffi import requests as cq
    s = cq.Session(impersonate=_IMPERSONATE, headers={
        "User-Agent": _UA, "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9"})
    for k, v in d["cookies"].items():
        s.cookies.set(k, v, domain=".bilibili.com")
    _apply_cred(s)
    _WBI["mk"] = d["mk"]
    _WBI["ts"] = float(d.get("mk_ts") or time.time())
    if cfg:
        LIMITER.configure(cfg.get("min_interval", DEFAULTS["min_interval"]),
                          cfg.get("jitter", DEFAULTS["jitter"]))
    _SESS = s
    return True


def _sess(cfg: Optional[dict] = None, force_new: bool = False):
    global _SESS
    with _SESS_LOCK:
        if force_new:
            _SESS = None
        if _SESS is None:
            if not force_new and _load_session(cfg):
                _log_().info("B站会话复用本地缓存（跳过预热）")
            else:
                _SESS = _new_session(cfg)
        return _SESS


def session_state() -> dict:
    """供前端展示会话状态（是否可用/是否需要重建）。"""
    try:
        st = os.path.getmtime(_SESSION_FILE)
    except Exception:  # noqa: BLE001
        st = None
    return {"has_session": st is not None,
            "age_min": None if st is None else round((time.time() - st) / 60, 1),
            "requests_this_process": LIMITER.count,
            "consecutive_banned": _consecutive_banned[0],
            "cache": cache_stats()}


def reset_session() -> dict:
    """手动重建会话（换 buvid/ticket）。"""
    _sess(force_new=True)
    return {"ok": True, **session_state()}


# ============ WBI 签名 ============
def _wbi_sign(params: dict) -> dict:
    import hashlib
    import urllib.parse
    p = dict(params)
    p["wts"] = int(time.time())
    q = urllib.parse.urlencode(sorted(
        (k, str(v)) for k, v in p.items()
        if str(v) not in ("", "None")
        and not any(c in str(v) for c in "'!()*")))
    p["w_rid"] = hashlib.md5((q + (_WBI["mk"] or "")).encode()).hexdigest()
    return p


def _app_sign(params: dict) -> str:
    import hashlib
    import urllib.parse
    p = dict(params)
    p["appkey"] = _APPKEY
    p["ts"] = int(time.time())
    q = urllib.parse.urlencode(sorted((k, str(v)) for k, v in p.items()))
    return q + "&sign=" + hashlib.md5((q + _APPSEC).encode()).hexdigest()


# ============ 请求主入口（限速 + 退避 + 会话重建） ============
def _api(url: str, params: dict, referer: str = "https://www.bilibili.com/",
         wbi: bool = False, tries: Optional[int] = None) -> Optional[dict]:
    """一次 API 调用。返回 JSON dict；风控耗尽重试后抛 BiliBlocked。"""
    cfg = _CFG_REF[0]
    n_tries = int(tries or cfg.get("max_tries", DEFAULTS["max_tries"]))
    backoff = float(cfg.get("backoff", DEFAULTS["backoff"]))
    last_code = None
    for i in range(n_tries):
        s = _sess(cfg)
        p = _wbi_sign(params) if wbi else params
        if BUDGET is not None:
            BUDGET.spend()
        LIMITER.wait()
        try:
            r = s.get(url, params=p, headers={"Referer": referer}, timeout=15)
            j = r.json()
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            last_code = f"exc:{type(e).__name__}"
            _log_().warning(f"请求异常({i + 1}/{n_tries}) {url.split('/')[-1]}: {e}")
            time.sleep(backoff * (i + 1) * 0.5)
            continue
        code = j.get("code")
        if code == 0:
            _consecutive_banned[0] = 0
            return j
        last_code = code
        if code in (-352, -412):
            _consecutive_banned[0] += 1
            _log_().warning(f"风控命中 code={code}({i + 1}/{n_tries}) "
                            f"{url.split('/')[-1]}")
            if _consecutive_banned[0] >= 3:
                # 连续被拦 → 重建会话（换 buvid/ticket）再试
                _log_().warning("连续风控拦截，重建会话（换 buvid/ticket）")
                _sess(cfg, force_new=True)
                _consecutive_banned[0] = 0
            # 退避封顶 15s：线性增长不加封顶时，5 次重试会空转 60s
            time.sleep(min(backoff * (i + 1), 15.0))
            continue
        # 其它业务错误（如 -101 未登录 / 404）不重试，直接返回
        return j
    raise BiliBlocked(f"B站接口持续被风控拦截（code={last_code}）：{url.split('/')[-1]}")


# 当前生效配置（模块内引用，供 _api 读取限速/重试参数）
_CFG_REF: list = [dict(DEFAULTS)]


def _merge_cfg(cfg: Optional[dict]) -> dict:
    c = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if v is not None and k in c:
            c[k] = v
    _CFG_REF[0] = c
    LIMITER.configure(c["min_interval"], c["jitter"])
    return c


# ============ UP主存档（本地复用） ============
_UPS_LOCK = threading.Lock()


def load_ups() -> list:
    try:
        with open(_UPS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        ups = d.get("ups") or []
    except Exception:  # noqa: BLE001
        return []
    for u in ups:
        if "alias" not in u:
            u["alias"] = ""
    return ups


def up_display_name(u: dict) -> str:
    """展示名：用户自定义 alias 优先，其次自动读取的 name，最后 UIDxxxx。"""
    u = u or {}
    return u.get("alias") or u.get("name") or f"UID{u.get('uid', '')}"


def _write_ups(ups: list) -> None:
    os.makedirs(_DATA, exist_ok=True)
    tmp = _UPS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ups": ups}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _UPS_FILE)


def add_up(uid, cfg: Optional[dict] = None, alias=None) -> dict:
    """保存 UP主（uid → 名称），已存在则刷新名称与时间。

    alias 可选：保存/刷新时顺带给它一个**用户自定义名称**（优先于自动读取的昵称）。
    """
    uid = str(uid or "").strip()
    if not re.fullmatch(r"\d{1,12}", uid):
        return {"ok": False, "msg": "uid 必须是纯数字（在 UP主 主页 URL 的 /space/ 之后）"}
    alias = (str(alias).strip()[:40] if alias is not None else None)
    info = fetch_up_info(uid, cfg)
    with _UPS_LOCK:
        ups = load_ups()
        old = next((u for u in ups if str(u.get("uid")) == uid), None)
        # 读取昵称失败时**保留已有名称**（否则一次风控就把好名字覆盖成 UIDxxxx）
        name = (info or {}).get("name") or (old or {}).get("name") or f"UID{uid}"
        if old is not None:
            old["name"] = name
            if alias:                     # 只有显式传了才改自定义名，避免刷新时被清掉
                old["alias"] = alias
            if info:
                old["face"] = info.get("face") or old.get("face") or ""
                old["sign"] = info.get("sign") or old.get("sign") or ""
                old["follower"] = info.get("follower")
            old["refreshed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _write_ups(ups)
            msg = "已更新" if info else "已更新（昵称读取失败，沿用原名）"
            return {"ok": True, "up": old, "msg": msg, "ups": ups}
        u = {"uid": uid, "name": name, "alias": alias or "",
             "face": (info or {}).get("face") or "",
             "sign": (info or {}).get("sign") or "",
             "follower": (info or {}).get("follower"),
             "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        ups.append(u)
        _write_ups(ups)
    return {"ok": True, "up": u,
            "msg": "已保存" if info else "已保存（昵称读取失败，可用 ✎ 自定义名称）",
            "ups": ups}


def rename_up(uid, name) -> dict:
    """给已保存的 UP主 设置/清除**自定义名称**（alias）。

    name 为空/空白 → 清除自定义名，回退到自动读取的昵称（或 UIDxxxx）。
    自定义名优先于自动读取（解决「昵称有时读不出来」的问题）。
    """
    uid = str(uid or "").strip()
    alias = (str(name).strip()[:40] if name is not None else "")
    with _UPS_LOCK:
        ups = load_ups()
        hit = next((u for u in ups if str(u.get("uid")) == uid), None)
        if hit is None:
            return {"ok": False, "msg": "该 UP主 不在已保存列表中", "ups": ups}
        hit["alias"] = alias
        hit["renamed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _write_ups(ups)
    return {"ok": True, "up": hit,
            "msg": ("已命名为：" + alias) if alias else "已清除自定义名称（回退为自动昵称）",
            "ups": ups}


def remove_up(uid) -> dict:
    uid = str(uid or "").strip()
    with _UPS_LOCK:
        ups = [u for u in load_ups() if str(u.get("uid")) != uid]
        _write_ups(ups)
    return {"ok": True, "ups": ups}


# ============ 抓取：UP主信息 ============
def fetch_up_info(uid, cfg: Optional[dict] = None) -> Optional[dict]:
    c = _merge_cfg(cfg)
    key = f"info_{uid}"
    hit = _cache_get(key, int(c.get("ttl_info", DEFAULTS["ttl_info"])))
    if hit:
        return hit
    try:
        j = _api("https://api.bilibili.com/x/space/wbi/acc/info",
                 {"mid": uid}, f"https://space.bilibili.com/{uid}",
                 wbi=True, tries=2)   # 展示信息，失败不阻塞主流程
    except BiliBlocked:
        return None
    d = (j or {}).get("data") or {}
    if not d:
        return None
    stat = d.get("stat") or {}
    out = {"uid": str(uid), "name": d.get("name"), "sign": d.get("sign"),
           "face": d.get("face"), "follower": stat.get("follower"),
           "archive": stat.get("archive") or (d.get("archive") or {}).get("count")}
    _cache_put(key, out)
    return out


# ============ 抓取：投稿视频列表（APP 签名，最稳） ============
def fetch_videos(uid, cfg: Optional[dict] = None) -> list:
    """返回 [{bvid, aid, title, ctime, ...}]，按发布时间降序，已按时间窗裁剪。"""
    c = _merge_cfg(cfg)
    win = datetime.now() - timedelta(days=int(c["days"]))
    key = f"videos_{uid}_{int(c['days'])}"
    hit = _cache_get(key, int(c.get("ttl_videos", DEFAULTS["ttl_videos"])))
    if hit is not None:
        return hit
    out: list = []
    pages = int(c.get("video_pages", 2))
    ok = False
    for pn in range(1, pages + 1):
        try:
            qs = _app_sign({"vmid": str(uid), "ps": 20, "pn": pn,
                            "order": "pubdate"})
            j = _api(f"https://app.bilibili.com/x/v2/space/archive/cursor?{qs}",
                     None, f"https://space.bilibili.com/{uid}/video")
        except BiliBlocked:
            break
        if not j or j.get("code") != 0:
            continue
        ok = True
        items = ((j or {}).get("data") or {}).get("item") or []
        if not items:
            break
        for it in items:
            bvid = it.get("bvid")
            if not bvid:
                # 无 bvid 的多为「课程/付费课程」条目 —— 按需求不强行读取，直接跳过
                continue
            ts = it.get("ctime") or 0
            try:
                ts = int(ts)
            except Exception:  # noqa: BLE001
                ts = 0
            if ts and datetime.fromtimestamp(ts) < win:
                continue
            out.append({"bvid": bvid, "aid": it.get("param") or it.get("aid"),
                        "title": (it.get("title") or "").strip(),
                        "ts": ts, "duration": it.get("duration"),
                        "play": it.get("play")})
        if all((int(x.get("ctime") or 0) or 0) and
               datetime.fromtimestamp(int(x["ctime"])) < win for x in items):
            break      # 本页已全部早于窗口 → 不必再翻
        if len(out) >= int(c.get("max_videos", 40)):
            break
    out = out[:int(c.get("max_videos", 40))]
    if ok:
        _cache_put(key, out)
    else:
        _log_().warning(f"投稿列表未取到（可能被风控），不写缓存: uid={uid}")
    return out


# ============ 抓取：动态列表 ============
def fetch_dynamics(uid, cfg: Optional[dict] = None) -> list:
    """返回 [{id, ts, text, bvid, title, type, comment_count}]（含转发原文文本）。"""
    c = _merge_cfg(cfg)
    win = datetime.now() - timedelta(days=int(c["days"]))
    key = f"dyns_{uid}_{int(c['days'])}"
    hit = _cache_get(key, int(c.get("ttl_dyns", DEFAULTS["ttl_dyns"])))
    if hit is not None:
        return hit
    out: list = []
    offset = ""
    pages = int(c.get("dyn_pages", 3))
    ok = False
    for _ in range(pages):
        try:
            j = _api("https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                     {"host_mid": str(uid), "offset": offset,
                      "timezone_offset": -480, "platform": "web",
                      "features": "itemOpusStyle,listOnlyfans",
                      "web_location": 333.1387},
                     f"https://space.bilibili.com/{uid}/dynamic", wbi=True)
        except BiliBlocked:
            break
        if not j or j.get("code") != 0:
            continue
        ok = True
        d = (j or {}).get("data") or {}
        items = d.get("items") or []
        if not items:
            break
        stop = False
        for it in items:
            a = (it.get("modules") or {}).get("module_author") or {}
            md = (it.get("modules") or {}).get("module_dynamic") or {}
            try:
                ts = int(a.get("pub_ts") or 0)
            except Exception:  # noqa: BLE001
                ts = 0
            if ts and datetime.fromtimestamp(ts) < win:
                stop = True
                continue
            own = ((md.get("desc") or {}).get("text") or "").strip()
            major = md.get("major") or {}
            arch = (major.get("archive") or {})
            opus = (major.get("opus") or {})
            opus_txt = " ".join(
                str((t or {}).get("text") or "")
                for t in ((opus.get("summary") or {}).get("rich_text_nodes") or []))
            orig = ((it.get("orig") or {}).get("modules") or {}).get("module_dynamic") or {}
            orig_own = ((orig.get("desc") or {}).get("text") or "").strip()
            orig_arch = ((orig.get("major") or {}).get("archive") or {})
            origin = ((it.get("basic") or {}).get("orig") or {})
            st = ((it.get("modules") or {}).get("module_stat") or {}).get("comment") or {}
            out.append({
                "id": str(it.get("id_str") or ""),
                "type": it.get("type"),
                "ts": ts,
                "own": own,
                "opus": opus_txt,
                "title": (arch.get("title") or orig_arch.get("title") or "").strip(),
                "bvid": arch.get("bvid") or orig_arch.get("bvid") or "",
                "orig": orig_own,
                "orig_author": ((origin.get("modules") or {}).get("module_author") or {}).get("name") or "",
                "comment_count": st.get("count") or 0,
            })
        if stop:
            break
        offset = str(d.get("offset") or "")
        if not d.get("has_more") or not offset:
            break
        if len(out) >= int(c.get("max_dyns", 60)):
            break
    out = out[:int(c.get("max_dyns", 60))]
    if ok:
        _cache_put(key, out)
    else:
        _log_().warning(f"动态未取到（可能被风控），不写缓存: uid={uid}")
    return out


# ============ 抓取：视频详情 + 字幕 ============
def fetch_video_text(bvid: str, cfg: Optional[dict] = None) -> dict:
    """视频的 标题/简介/字幕全文（字幕常为免费 AI 字幕；取不到就跳过，不做 ASR）。"""
    c = _merge_cfg(cfg)
    key = f"view_{bvid}"
    hit = _cache_get(key, int(c.get("ttl_view", DEFAULTS["ttl_view"])))
    if hit:
        return hit
    out = {"bvid": bvid, "title": "", "desc": "", "aid": None, "cid": None,
           "subtitle": "", "has_subtitle": False, "sub_need_login": False,
           "view_ok": False}
    try:
        j = _api("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid},
                 f"https://www.bilibili.com/video/{bvid}/")
    except BiliBlocked:
        return out
    d = (j or {}).get("data") or {}
    if not d:
        return out
    out["view_ok"] = True
    out.update({"title": (d.get("title") or "").strip(),
                "desc": (d.get("desc") or "").strip(),
                "aid": d.get("aid"), "cid": d.get("cid")})
    if c.get("with_subtitle", True) and out["aid"] and out["cid"]:
        try:
            p = _api("https://api.bilibili.com/x/player/wbi/v2",
                     {"aid": out["aid"], "cid": out["cid"]},
                     f"https://www.bilibili.com/video/{bvid}/", wbi=True)
        except BiliBlocked:
            p = None
        subs = ((((p or {}).get("data") or {}).get("subtitle") or {})
                .get("subtitles")) or []
        if not subs:
            out["sub_need_login"] = bool(
                ((p or {}).get("data") or {}).get("need_login_subtitle"))
        if subs:
            u = subs[0].get("subtitle_url") or ""
            if u.startswith("//"):
                u = "https:" + u
            if u:
                sj = None
                try:
                    s = _sess(c)
                    if BUDGET is not None:
                        BUDGET.spend()
                    LIMITER.wait()
                    r = s.get(u, headers={"Referer": f"https://www.bilibili.com/video/{bvid}/"},
                              timeout=15)
                    sj = r.json()
                except BudgetExceeded:
                    raise
                except Exception as e:  # noqa: BLE001
                    _log_().warning(f"字幕下载失败 {bvid}: {e}")
                body = ((sj or {}).get("body")) or []
                if body:
                    out["subtitle"] = " ".join(
                        str(x.get("content") or "") for x in body)[:20000]
                    out["has_subtitle"] = True
    # 字幕因未登录拿不到时**不缓存** —— 否则用户之后补上 SESSDATA 也读不到字幕
    if out["sub_need_login"] and not cred_state()["has_sessdata"]:
        return out
    _cache_put(key, out)
    return out


# ============ 本地视频文稿（可选：下载音频 → 本地语音识别） ============
# 设计取舍（用户需求：视频文案作为「视频」数据源，安全 + 免费 + 稳定）：
#   · 音频流 URL 由 playurl 接口匿名取得（实测 code=0，dash.audio 有 3 档），
#     **无需登录、无需 cookie**，所以拿流这一步不增加任何风控暴露面。
#   · 下载走磁盘缓存，同一视频只下一次；转写结果也落盘，重跑秒出。
#   · 转写引擎二选一（都是纯 pip、离线、免费）：
#       funasr  → SenseVoiceSmall：中文最强、非自回归、比 whisper-small 快 5×，
#                 CPU 也能跑；GPU 可选。**推荐**。
#       whisper → faster-whisper：多语言、生态成熟；GPU 需 CUDA12.8+cuDNN9。
#   · 引擎缺席时**只提示不报错**，不影响主流程（视频仍按标题/简介参与）。
#   · 转写很慢，用 video_local_max 从严限制单轮数量，且结果缓存 30 天。
_ASR_CACHE_DIR = os.path.join(_DATA, "bili_asr")
_LOCAL_VIDEO_STATE: dict = {"engine": None, "checked": False, "note": ""}


def _which(name: str) -> str:
    from shutil import which
    return which(name) or ""


def _probe_asr() -> dict:
    """探测本机可用的转写引擎（只探一次）。返回 {engine, note}。"""
    global _LOCAL_VIDEO_STATE
    if _LOCAL_VIDEO_STATE["checked"]:
        return _LOCAL_VIDEO_STATE
    _LOCAL_VIDEO_STATE["checked"] = True
    # 优先级：faster-whisper（快、准、多语言） > funasr SenseVoice（中文强、纯CPU）
    try:
        import faster_whisper  # noqa: F401
        _LOCAL_VIDEO_STATE.update(engine="whisper", note="faster-whisper 可用")
        return _LOCAL_VIDEO_STATE
    except Exception:  # noqa: BLE001
        pass
    try:
        import funasr  # noqa: F401
        _LOCAL_VIDEO_STATE.update(engine="funasr", note="funasr(SenseVoice) 可用")
        return _LOCAL_VIDEO_STATE
    except Exception:  # noqa: BLE001
        pass
    _LOCAL_VIDEO_STATE.update(
        engine="", note="未检测到本地转写引擎（faster-whisper / funasr）。"
                        "可执行 `pip install faster-whisper` 或 "
                        "`pip install funasr modelscope` 后重试；"
                        "在此之前「本地视频文稿」不可用（不影响其它功能）。")
    return _LOCAL_VIDEO_STATE


def local_video_state() -> dict:
    s = _probe_asr()
    return {"engine": s["engine"], "note": s["note"],
            "dir": _VIDEO_DIR, "dir_exists": os.path.isdir(_VIDEO_DIR),
            "asr_cache": _ASR_CACHE_DIR}


def asr_help() -> dict:
    """给出「本地视频文稿」可用的安装路径（全部免费、离线、无需登录）。"""
    st = _probe_asr()
    return {
        "engine": st["engine"], "ready": bool(st["engine"]), "note": st["note"],
        "options": [
            {
                "name": "faster-whisper（推荐：多语言、GPU 快）",
                "install": "pip install faster-whisper",
                "extra": "Windows + RTX 50 系需 CUDA 12.8 运行库与 cuDNN 9；"
                         "CPU 亦可（用 int8，较慢）。首次运行会自动下载模型"
                         "（medium≈1.5GB），全程离线识别。",
                "why": "零 torch 依赖（CTranslate2 + PyAV 自带 ffmpeg），"
                       "中文与多语言兼顾，社区最成熟。",
            },
            {
                "name": "funasr + SenseVoice（中文更准、纯 CPU 也快）",
                "install": "pip install funasr modelscope",
                "extra": "首次会自动下载 SenseVoiceSmall（约 900MB）；"
                         "非自回归结构，比 whisper-small 快 5×。",
                "why": "阿里达摩院开源，中文/粤语原生优化，自带情感与音频事件"
                       "标签，纯 pip 无需 CUDA 工具链。",
            },
        ],
        "privacy": "音频与文稿只存本机（data/bili_video、data/bili_asr）；"
                   "识别在本机完成，不上传任何音频。",
        "note_extra": "拿音频流不需要登录（playurl 匿名可用），因此本功能"
                      "不会增加任何账号风控风险；下载与转写均受同一套限速/硬闸约束。",
    }


def _audio_url(bvid: str, cid, c: dict) -> str:
    """取该视频最佳音频流 URL（匿名，playurl WBI）。"""
    if not cid:
        return ""
    try:
        j = _api("https://api.bilibili.com/x/player/wbi/playurl",
                 {"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0,
                  "fourk": 1, "qn": 64, "platform": "pc"},
                 f"https://www.bilibili.com/video/{bvid}/", wbi=True, tries=2)
    except BiliBlocked:
        return ""
    d = (j or {}).get("data") or {}
    audios = ((d.get("dash") or {}).get("audio")) or []
    if audios:
        # 取码率最高的一档
        audios = sorted(audios, key=lambda a: int(a.get("bandwidth") or 0),
                        reverse=True)
        a = audios[0]
        return a.get("baseUrl") or a.get("base_url") or ""
    durl = d.get("durl") or []
    return (durl[0].get("url") if durl else "") or ""


def _download_audio(bvid: str, url: str, c: dict) -> str:
    """下载音频到本地（走磁盘缓存，同一视频只下一次）。返回本地路径。"""
    vdir = c.get("video_local_dir") or _VIDEO_DIR
    os.makedirs(vdir, exist_ok=True)
    dst = os.path.join(vdir, f"{bvid}.m4a")
    if os.path.isfile(dst) and os.path.getsize(dst) > 4096:
        return dst
    try:
        s = _sess(c)
        if BUDGET is not None:
            BUDGET.spend()
        LIMITER.wait()
        r = s.get(url, headers={
            "Referer": f"https://www.bilibili.com/video/{bvid}/",
            "Origin": "https://www.bilibili.com",
            "Range": "bytes=0-",
        }, timeout=120)
        if r.status_code not in (200, 206) or not r.content:
            _log_().warning(f"音频下载异常 {bvid}: HTTP {r.status_code}")
            return ""
        tmp = dst + ".part"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, dst)
        return dst
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001
        _log_().warning(f"音频下载失败 {bvid}: {e}")
        return ""


def _transcribe(path: str, engine: str) -> str:
    """本地语音识别（离线）。返回纯文本；失败返回空串。"""
    try:
        if engine == "whisper":
            from faster_whisper import WhisperModel
            # 首次会自动下载模型（medium≈1.5GB）；GPU 失败则回落 CPU
            try:
                model = WhisperModel("medium", device="cuda",
                                     compute_type="int8_float16")
            except Exception:  # noqa: BLE001
                model = WhisperModel("medium", device="cpu", compute_type="int8")
            segs, _info = model.transcribe(path, language="zh", beam_size=5,
                                           vad_filter=True)
            return " ".join(s.text.strip() for s in segs).strip()
        if engine == "funasr":
            from funasr import AutoModel
            from funasr.utils.postprocess_utils import rich_transcription_postprocess
            try:
                model = AutoModel(model="iic/SenseVoiceSmall", device="cuda:0",
                                  vad_model="fsmn-vad",
                                  vad_kwargs={"max_single_segment_time": 30000})
            except Exception:  # noqa: BLE001
                model = AutoModel(model="iic/SenseVoiceSmall", device="cpu",
                                  vad_model="fsmn-vad",
                                  vad_kwargs={"max_single_segment_time": 30000})
            res = model.generate(input=path, language="zh", use_itn=True,
                                 batch_size_s=60)
            return rich_transcription_postprocess(res[0]["text"]).strip()
    except Exception as e:  # noqa: BLE001
        _log_().warning(f"本地转写失败 {os.path.basename(path)}: "
                        f"{type(e).__name__}: {str(e)[:120]}")
    return ""


def local_transcript(bvid: str, cid, cfg: Optional[dict] = None) -> dict:
    """视频 → 本地音频 → 本地语音识别 → 文稿全文。

    返回 {"ok": bool, "text": str, "reason": str, "audio": str}。
    全程离线识别（不上传任何音频）；结果按 30 天缓存。
    """
    c = _merge_cfg(cfg)
    key = f"asr_{bvid}"
    hit = _cache_get(key, 30 * 86400)
    if hit is not None:
        return hit
    st = _probe_asr()
    out = {"ok": False, "text": "", "reason": "", "audio": ""}
    if not st["engine"]:
        out["reason"] = st["note"]
        return out
    if not cid:
        out["reason"] = "缺少 cid，无法取流"
        return out
    url = _audio_url(bvid, cid, c)
    if not url:
        out["reason"] = "未取到音频流（可能需登录或接口变动）"
        return out
    path = _download_audio(bvid, url, c)
    if not path:
        out["reason"] = "音频下载失败"
        return out
    out["audio"] = path
    txt = _transcribe(path, st["engine"])
    if txt:
        out.update(ok=True, text=txt[:40000], reason=f"本地转写({st['engine']})")
        _cache_put(key, out)
    else:
        out["reason"] = "转写返回空"
    return out


# ============ 抓取：评论 + 楼中楼 ============
def _replies_to_rows(reps: list, is_up_uid: str, floor: str) -> list:
    out = []
    for r in reps or []:
        mem = r.get("member") or {}
        mid = str(mem.get("mid") or "")
        msg = ((r.get("content") or {}).get("message") or "").strip()
        if not msg:
            continue
        out.append({
            "rpid": str(r.get("rpid") or ""),
            "mid": mid,
            "uname": mem.get("uname") or "",
            "is_up": mid == str(is_up_uid),
            "ts": int(r.get("ctime") or 0),
            "msg": msg,
            "like": r.get("like") or 0,
            "rcount": r.get("rcount") or 0,
            "floor": floor,
        })
    return out


def fetch_comments(oid, otype: int, up_uid, cfg: Optional[dict] = None,
                   label: str = "") -> list:
    """评论：置顶 + 热度页 + 最新页；并对「UP主自己的评论」与「最热评论」取楼中楼。"""
    c = _merge_cfg(cfg)
    key = f"cm_{otype}_{oid}"
    hit = _cache_get(key, int(c.get("ttl_comments", DEFAULTS["ttl_comments"])))
    if hit is not None:
        return hit
    rows: list = []
    seen = set()
    ok = False        # 是否有任一页真正拿到数据（决定要不要写缓存）

    def _add(new):
        for r in new:
            if r["rpid"] and r["rpid"] in seen:
                continue
            seen.add(r["rpid"])
            rows.append(r)

    plans = ([(2, int(c.get("comment_pages_hot", 2)), "热门"),
              (0, int(c.get("comment_pages_new", 1)), "最新")]
             if otype == 1 else
             [(2, int(c.get("comment_pages_hot", 2)), "热门")])
    for sort, pages, tag in plans:
        for pn in range(1, pages + 1):
            try:
                j = _api("https://api.bilibili.com/x/v2/reply",
                         {"type": otype, "oid": oid, "pn": pn, "ps": 20,
                          "sort": sort}, label)
            except BiliBlocked:
                break
            if not j or j.get("code") != 0:
                continue
            ok = True
            d = (j or {}).get("data") or {}
            _add(_replies_to_rows(d.get("top_replies") or [], up_uid, "置顶"))
            _add(_replies_to_rows([d["top"]] if isinstance(d.get("top"), dict) else [],
                                  up_uid, "置顶"))
            raw_main = d.get("replies") or []
            # 内联的前若干条子回复先用上（省一次请求）。
            # 注意：必须遍历 raw_main 而不是解析后的 rows —— _replies_to_rows 会跳过
            # 空消息，用 zip(rows) 会与原文错位，把子回复挂到错误的父评论上。
            for raw in raw_main:
                _add(_replies_to_rows(raw.get("replies") or [], up_uid,
                                      f"{tag}评论的回复"))
            _add(_replies_to_rows(raw_main, up_uid, f"{tag}评论"))
            if not raw_main:
                break
    # 楼中楼：UP主自己的评论 优先，其次最热的一条
    want = [r for r in rows if r["is_up"] and int(r["rcount"] or 0) > 0]
    hot = sorted([r for r in rows if not r["is_up"]],
                 key=lambda x: -(int(x["like"] or 0)))[:1]
    for r in (want + hot)[:4]:
        for pn in range(1, int(c.get("sub_reply_pages", 1)) + 1):
            try:
                j = _api("https://api.bilibili.com/x/v2/reply/reply",
                         {"type": otype, "oid": oid, "root": r["rpid"],
                          "pn": pn, "ps": 20}, label)
            except BiliBlocked:
                break
            if not j or j.get("code") != 0:
                continue
            ok = True
            d = (j or {}).get("data") or {}
            sub = _replies_to_rows(d.get("replies") or [], up_uid,
                                   f"回复 {r['uname']}")
            if not sub:
                break
            _add(sub)
    # 只有真正取到过数据才写缓存：否则一次风控拦截会把「空结果」缓存住，
    # 让后续 24 小时内的重试全都拿到空列表（实测踩过）。
    if ok:
        _cache_put(key, rows)
    else:
        _log_().warning(f"评论未取到（可能被风控），不写缓存: type={otype} oid={oid}")
        CM_FAIL[0] += 1
    return rows


# ============ 股票提取 ============
_MAP_LOCK = threading.Lock()
_MAPS = {"ts": 0.0, "code2name": {}, "names": []}
_NAME2CODE: dict = {}

_CN_NUM = "零一二三四五六七八九"
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_SENT_SPLIT = re.compile(r"[。！？!?\n；;]")

# 2 字简称必须搭配股票语境词，否则「美的/大众/人民/东方」之类日常词会大面积误命中。
# 与 futures._hit_sym 的「单字关键词需搭配期货上下文」是同一套思路。
_CTX_WORDS = (
    "股票", "个股", "A股", "港股", "美股", "涨停", "跌停", "涨了", "跌了", "涨到", "跌到",
    "市值", "股价", "收盘", "开盘", "持仓", "仓位", "加仓", "减仓", "建仓", "清仓", "满仓",
    "买入", "卖出", "标的", "龙头", "板块", "题材", "赛道", "估值", "业绩", "财报", "报表",
    "分红", "股息", "增持", "减持", "回购", "定增", "调研", "机构", "北向", "主力", "散户",
    "基本面", "技术面", "低估", "高估", "PE", "PB", "ROE", "毛利率", "净利率", "营收",
    "净利润", "增速", "订单", "产能", "扩产", "重组", "并购", "上市", "新股", "退市",
    "限售", "解禁", "公告", "业绩预告", "中报", "年报", "一季报", "三季报",
)


def _stock_maps():
    """从本地 meta 表取 {code: name} 与按长度降序的简称表（缓存 10 分钟）。"""
    global _NAME2CODE
    with _MAP_LOCK:
        if _MAPS["code2name"] and time.time() - _MAPS["ts"] < 600:
            return _MAPS["code2name"], _MAPS["names"]
        code2name, names, name2code = {}, [], {}
        try:
            from ..core import db
            conn = db.reader()
            for code, name in conn.execute("SELECT code, name FROM meta"):
                name = str(name or "").strip()
                if not name:
                    continue
                code2name[str(code)] = name
                if 2 <= len(name) <= 6:
                    names.append(name)
                    # 同名多代码（罕见）：保留先出现的，避免不确定映射
                    name2code.setdefault(name, str(code))
        except Exception as e:  # noqa: BLE001
            _log_().warning(f"读取股票名表失败: {e}")
        # 长名优先（避免「中国平安」被「中国」截胡）
        names = sorted(set(names), key=len, reverse=True)
        _NAME2CODE = name2code
        _MAPS.update(code2name=code2name, names=names, ts=time.time())
        return code2name, names


def extract_mentions(text: str, limit: int = 12):
    """从文本中提取股票提及 → [(code, name, matched, kind)]。

    **同一段文本内同一只股票只保留首次出现**（否则「贵州茅台…600519」会被算成
    两次提及，虚增权重）；结果按出现位置排序，便于原文截取阅读。
    """
    t = str(text or "")
    if not t:
        return []
    code2name, names = _stock_maps()
    if not code2name:
        return []
    found: list = []          # (pos, code, name, matched, kind)
    used_spans: list = []
    ctx_min_len = int(_CFG_REF[0].get("ctx_min_len", DEFAULTS["ctx_min_len"]))

    def _overlap(s, e):
        return any(not (e <= a or s >= b) for a, b in used_spans)

    # ① 6 位代码（对齐本地股票表 → 精度极高，不会命中随机数字）
    for m in _CODE_RE.finditer(t):
        code = m.group(1)
        if code not in code2name:
            continue
        if _overlap(m.start(), m.end()):
            continue
        used_spans.append((m.start(), m.end()))
        found.append((m.start(), code, code2name[code], code, "code"))

    # ② 简称（长名优先；2 字名需同句有股票语境词）
    for name in names:
        start = 0
        while True:
            i = t.find(name, start)
            if i < 0:
                break
            start = i + len(name)
            s, e = i, i + len(name)
            if _overlap(s, e):
                continue
            code = _NAME2CODE.get(name)
            if not code:
                continue
            if len(name) <= ctx_min_len:
                # 短简称（默认 ≤3 字）必须搭配股票语境词，否则「太平洋战争」
                # 「大众点评」「美的空调」这类日常短语会大面积误命中。
                seg = _SENT_SPLIT.split(t[max(0, s - 60):e + 60])
                near = ""
                for part in seg:
                    if name in part:
                        near = part
                        break
                if not any(w in near for w in _CTX_WORDS):
                    continue
            used_spans.append((s, e))
            found.append((s, code, name, name, "name"))

    found.sort(key=lambda x: x[0])
    out: list = []
    seen: set = set()
    for _pos, code, name, matched, kind in found:
        if code in seen:
            continue
        seen.add(code)
        out.append((code, name, matched, kind))
        if len(out) >= limit:
            break
    return out


def _snippet(text: str, matched: str, width: int = 46) -> str:
    """原文截取：命中词左右各 width 字符，折叠空白。"""
    t = re.sub(r"\s+", " ", str(text or ""))
    i = t.find(matched)
    if i < 0:
        return t[:width * 2]
    s = max(0, i - width)
    e = min(len(t), i + len(matched) + width)
    pre = "…" if s > 0 else ""
    post = "…" if e < len(t) else ""
    return f"{pre}{t[s:e]}{post}"


# ---- 主流程 ----
# 最近一次扫描结果（供「一键总结」复用，避免重新抓取）
LAST: dict = {"result": None, "ts": 0.0}


def _fmt_ts(ts) -> str:
    try:
        ts = int(ts)
    except Exception:  # noqa: BLE001
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def scan(uids: list, cfg: Optional[dict] = None,
         progress_cb: ProgressCb = None) -> dict:
    """抓取指定 UP主 群在时间窗内的内容并聚合出股票榜单。

    返回 {"stocks": [...], "ups": [...], "stats": {...}, "notes": [...]}。
    """
    global BUDGET
    c = _merge_cfg(cfg)
    BUDGET = _Budget(c["max_requests"])
    CM_FAIL[0] = 0
    LIMITER.configure(c["min_interval"], c["jitter"])
    t0 = time.time()
    notes: list[str] = []
    uids = [str(u).strip() for u in (uids or []) if str(u).strip()]
    if not uids:
        return {"stocks": [], "ups": [], "notes": ["请先选择至少一个 UP主"], "stats": {}}
    days = int(c["days"])
    now = datetime.now()
    win_start = now - timedelta(days=days)
    W0 = win_start.timestamp()
    N = now.timestamp()
    use_local = bool(c.get("use_local", True))

    def cb(frac, msg):
        if progress_cb:
            progress_cb(frac, 1.0, msg)

    # ---- 1. UP主信息 ----
    ups = []
    dyn_blocked = False
    up_name: dict = {}
    saved_ups = {str(u.get("uid")): u for u in load_ups()}
    for i, uid in enumerate(uids):
        cb(0.02 + 0.04 * i / max(1, len(uids)), f"读取 UP主 {uid} 信息…")
        info = fetch_up_info(uid, c) or {"uid": uid, "name": f"UID{uid}"}
        ups.append(info)
        sv = saved_ups.get(uid) or {}
        # 展示名优先序：用户自定义 alias > 本次读到的昵称 > 存档里的昵称 > UIDxxxx
        up_name[uid] = (sv.get("alias") or info.get("name")
                        or sv.get("name") or f"UID{uid}")
        info["name"] = up_name[uid]

    # ---- 1b. 本地存档：逐 UP主 决定抓取策略（全量 / 增量 / 纯本地复用） ----
    archives: dict = {}
    plans: dict = {}
    reuse_mentions: list = []
    for uid in uids:
        arc = load_archive(uid)
        arc["name"] = up_name.get(uid) or arc.get("name") or uid
        archives[uid] = arc
        plans[uid] = (archive_plan(arc, win_start, now) if use_local else
                      {s: {"fetch": True, "from": W0, "reason": "全量"}
                       for s in ("video", "dynamic")})
        if use_local:
            for m in (arc.get("mentions") or {}).values():
                if W0 <= float(m.get("ts") or 0) <= N:
                    reuse_mentions.append(m)
    n_local = len(reuse_mentions)

    def _fetch_days(from_ts) -> int:
        """把「从 from_ts 抓到当下」折算成 fetch_* 需要的自然日数（不超过请求范围）。"""
        dd = int(math.ceil((N - float(from_ts)) / 86400.0))
        return max(1, min(int(c["days"]), dd))

    # 计算总任务量用于进度（视频 + 动态）
    cb(0.08, "拉取投稿视频列表…")
    vids_map: dict = {}
    raw_vids: dict = {}
    used_days: dict = {}     # (uid, stream) → 本轮实际使用的抓取天数（用于推算覆盖下界）
    for uid in uids:
        arc, pl = archives[uid], plans[uid]
        if not pl["video"]["fetch"]:
            raw_vids[uid] = []
            vids_map[uid] = []
            continue
        c2 = dict(c)
        c2["days"] = _fetch_days(pl["video"]["from"])
        used_days[(uid, "video")] = c2["days"]
        raw = fetch_videos(uid, c2) or []
        raw_vids[uid] = raw
        # 只处理「窗口内」的单元。边界日会被重拉一次（以捕捉新增评论等）——
        # 与本地存档重复的提及在合并阶段按 _mkey 去重，因此不会重复显示。
        vids_map[uid] = [u for u in raw if int(u.get("ts") or 0) >= W0]
    tot_v = sum(len(v) for v in vids_map.values())

    dyn_map: dict = {}
    raw_dyns: dict = {}
    for uid in uids:
        raw_dyns[uid] = []
        dyn_map[uid] = []
    if c.get("with_dynamic", True):
        cb(0.14, "拉取动态列表…")
        for uid in uids:
            arc, pl = archives[uid], plans[uid]
            if not pl["dynamic"]["fetch"]:
                continue
            try:
                c2 = dict(c)
                c2["days"] = _fetch_days(pl["dynamic"]["from"])
                used_days[(uid, "dynamic")] = c2["days"]
                raw = fetch_dynamics(uid, c2) or []
                raw_dyns[uid] = raw
                # 同上：边界日重拉，重复提及在合并阶段按 _mkey 去重
                dyn_map[uid] = [d for d in raw if int(d.get("ts") or 0) >= W0]
            except BiliBlocked as e:
                dyn_blocked = True
                notes.append(f"动态接口被风控拦截，已跳过该 UP主 的动态：{str(e)[:60]}")
    tot_d = sum(len(v) for v in dyn_map.values())
    tot_units = max(1, tot_v + tot_d)

    # 逐 UP主 的策略说明（用户要求：区分各 UP主 在本地的数据，按实际情况取策略）
    skip_ups: list = []
    for uid in uids:
        pl = plans[uid]
        f_v = pl["video"]["fetch"]
        f_d = bool(c.get("with_dynamic", True)) and pl["dynamic"]["fetch"]
        nm = up_name.get(uid, uid)
        if not f_v and not f_d:
            skip_ups.append(nm)
            continue
        if not use_local:
            continue
        rs = ([f"视频·{pl['video']['reason']}"] if f_v else []) + \
             ([f"动态·{pl['dynamic']['reason']}"] if f_d else [])
        frm = min([x for x in (pl["video"]["from"], pl["dynamic"]["from"]) if x]
                  or [W0])
        notes.append(
            f"UP主 {nm}：本地{'增量' if frm > W0 + 86400 else '全量'}抓取"
            f"（{'、'.join(rs)}）—— 自 {_fmt_ts(frm)[:10]} 起，"
            f"视频 {len(vids_map[uid])} 条 / 动态 {len(dyn_map[uid])} 条待处理。")
    local_ups = len(skip_ups) if use_local else 0
    if use_local and skip_ups:
        notes.append(
            "以下 UP主 本地存档已覆盖当前范围，本轮 **0 网络请求**、直接复用本地："
            + "、".join(skip_ups[:8])
            + (f" 等共 {local_ups} 个" if local_ups > 8 else ""))

    # ---- 2. 逐条采集文本 ----
    units = [("video", uid, v) for uid in uids for v in vids_map[uid]]
    units += [("dyn", uid, d) for uid in uids for d in dyn_map[uid]]
    done = 0
    mentions: list[dict] = []
    seen_crpids: set = set()   # 跨 oid 去重：同一视频既作为「视频」又作为「动态」被抓，
    #                         其评论（同 rpid）会被 fetch_comments 抓两遍 → 全局按 rpid 去重，
    #                         否则同一评论在结果里重复出现（见下方视频/动态两条评论采集分支）。
    trunc = False
    n_comments = 0            # 实际检视过的评论条数（含楼中楼）
    n_up_comments = 0         # 其中 UP主 自己发的
    n_sub_login = 0           # 因未登录而拿不到字幕的视频数
    n_empty_dyn = 0           # 无正文且非转发的空动态（纯图/表情等）
    n_asr = 0                 # 实际完成「本地转写」的视频数
    n_asr_skip = 0            # 因引擎缺席/超限而跳过转写的视频数
    asr_note = ""
    want_asr = bool(c.get("video_local"))
    asr_cap = int(c.get("video_local_max") or 5)
    if want_asr:
        _st = _probe_asr()
        asr_note = _st["note"]
        if not _st["engine"]:
            want_asr = False
            notes.append(asr_note)

    added_mentions: dict = {}     # 本轮新抓的提及，按 uid 归集 → 回写各 UP主 存档

    def _record(uid, m):
        mentions.append(m)
        added_mentions.setdefault(str(uid), []).append(m)

    def _collect(uid, kind, ts, author_is_up, src_label, text, title, url,
                 via=""):
        for code, name, matched, mkind in extract_mentions(text):
            _record(uid, {
                "code": code, "name": name, "kind": mkind,
                "ts": int(ts or 0), "time": _fmt_ts(ts),
                "is_up": bool(author_is_up),
                "author": "UP主" if author_is_up else "",
                "source": kind, "source_label": src_label, "via": via,
                "title": title or "", "url": url or "",
                "snippet": _snippet(text, matched),
                "weight": float(c["w_up"] if author_is_up else c["w_other"]),
            })

    for kind, uid, item in units:
        done += 1
        try:
            if kind == "video":
                bvid = item["bvid"]
                cb(0.16 + 0.72 * done / tot_units,
                   f"分析视频 {done}/{tot_units}：{str(item.get('title'))[:24]}")
                vt = fetch_video_text(bvid, c)
                vurl = f"https://www.bilibili.com/video/{bvid}/"
                vt_title = vt.get("title") or item.get("title") or ""
                _collect(uid, "video", item.get("ts"), True, "视频",
                         f"{vt_title} {vt.get('desc') or ''}", vt_title, vurl)
                if vt.get("subtitle"):
                    _collect(uid, "subtitle", item.get("ts"), True, "视频字幕",
                             vt["subtitle"], vt_title, vurl)
                elif vt.get("sub_need_login"):
                    n_sub_login += 1
                # 字幕拿不到时，可选走「本地下载音频 + 离线转写」补全文稿
                if not vt.get("subtitle") and want_asr:
                    if n_asr >= asr_cap:
                        n_asr_skip += 1
                    else:
                        cb(0.16 + 0.72 * done / tot_units,
                           f"本地转写视频 {n_asr + 1}/{asr_cap}："
                           f"{str(vt_title)[:20]}")
                        lt = local_transcript(bvid, vt.get("cid"), c)
                        if lt.get("ok"):
                            n_asr += 1
                            _collect(uid, "video_asr", item.get("ts"), True,
                                     "视频文稿", lt["text"], vt_title, vurl,
                                     via="本地语音识别")
                        else:
                            n_asr_skip += 1
                            if lt.get("reason"):
                                asr_note = lt["reason"]
                cm = fetch_comments(vt.get("aid") or item.get("aid"), 1, uid, c, vurl)
                for r in cm:
                    in_win = (not win_start) or \
                        datetime.fromtimestamp(r["ts"]) >= win_start
                    if not in_win:
                        continue
                    if not r["msg"]:
                        continue
                    # 跨 oid 去重：同一视频被当作「视频」和「动态」各抓一次，
                    # 其评论 rpid 相同，跳过已在其它 oid 抓过的，避免重复显示。
                    if r["rpid"] in seen_crpids:
                        continue
                    seen_crpids.add(r["rpid"])
                    n_comments += 1
                    if r["is_up"]:
                        n_up_comments += 1
                    for code, name, matched, mkind in extract_mentions(r["msg"]):
                        _record(uid, {
                            "code": code, "name": name, "kind": mkind,
                            "ts": r["ts"], "time": _fmt_ts(r["ts"]),
                            "is_up": r["is_up"],
                            "rpid": r["rpid"],
                            "author": r["uname"], "source": "comment",
                            "source_label": f"{r['floor']}评论", "via": "",
                            "title": vt_title, "url": vurl,
                            "snippet": _snippet(r["msg"], matched),
                            "like": r["like"],
                            "weight": float(c["w_up"] if r["is_up"] else c["w_other"]),
                        })
            else:
                did = item["id"]
                title = item.get("title") or ""
                durl = f"https://t.bilibili.com/{did}"
                cb(0.16 + 0.72 * done / tot_units,
                   f"分析动态 {done}/{tot_units}")
                own = " ".join(x for x in (item.get("own"), item.get("opus")) if x)
                # 关键：UP主 的「本人观点」只来自其自己撰写的正文。
                # 纯转发（自己一句话不说）= 完全不计入（除非带附言，见下）。
                # 转发并附言 = 附言算本人观点（高权重），被转的原文另算（低权重）；
                # 这样既不会把搬运内容当成 UP主观点，也不会漏掉 UP主 在转发时的点评。
                if own:
                    _collect(uid, "dynamic", item.get("ts"), True, "动态", own,
                             title, durl)
                if item.get("orig"):
                    _collect(uid, "repost", item.get("ts"), False, "转发原文",
                             item["orig"], title, durl,
                             via=f"转发自 {item.get('orig_author') or '他人'}")
                if not own and not item.get("orig"):
                    n_empty_dyn += 1
                cm = fetch_comments(did, 17, uid, c, durl)
                for r in cm:
                    if r["ts"] and datetime.fromtimestamp(r["ts"]) < win_start:
                        continue
                    if not r["msg"]:
                        continue
                    # 跨 oid 去重（同一视频既作「视频」又作「动态」被抓，评论 rpid 相同）
                    if r["rpid"] in seen_crpids:
                        continue
                    seen_crpids.add(r["rpid"])
                    n_comments += 1
                    if r["is_up"]:
                        n_up_comments += 1
                    for code, name, matched, mkind in extract_mentions(r["msg"]):
                        _record(uid, {
                            "code": code, "name": name, "kind": mkind,
                            "ts": r["ts"], "time": _fmt_ts(r["ts"]),
                            "is_up": r["is_up"],
                            "rpid": r["rpid"],
                            "author": r["uname"], "source": "comment",
                            "source_label": f"动态{r['floor']}评论", "via": "",
                            "title": title or ("动态 " + did), "url": durl,
                            "snippet": _snippet(r["msg"], matched),
                            "like": r["like"],
                            "weight": float(c["w_up"] if r["is_up"] else c["w_other"]),
                        })
        except BudgetExceeded as e:
            trunc = True
            notes.append(str(e))
            break
        except BiliBlocked as e:
            notes.append(f"风控拦截，提前结束：{str(e)[:60]}")
            break
        except Exception as e:  # noqa: BLE001
            notes.append(f"{kind} 处理失败：{type(e).__name__}: {str(e)[:60]}")

    # ---- 2b. 合并本地复用 + 回写存档 ----
    # 合并「本地存档复用」与「本轮新抓」，按 _mkey 去重（边界日重拉时同一条不会算两次）
    if reuse_mentions:
        merged: dict = {}
        for m in reuse_mentions + mentions:
            merged.setdefault(_mkey(m), m)
        mentions = list(merged.values())
    for uid in uids:
        arc = archives[uid]
        for stream, raw in (("video", raw_vids.get(uid) or []),
                            ("dynamic", raw_dyns.get(uid) or [])):
            if not plans[uid][stream]["fetch"]:
                continue
            done = arc["done"].setdefault(stream, {})
            cand = []
            for u in raw:
                ts = int(u.get("ts") or 0)
                if ts >= W0:
                    key = str(u.get("bvid") if stream == "video" else u.get("id"))
                    done[key] = ts
                    cand.append(ts)
            covc = arc["cov"].setdefault(stream, {"min": 0, "max": 0})
            old_min = float(covc.get("min") or 0)
            # 覆盖下界：默认取本轮「枚举到的窗口底部」（列表按时间倒序，取到窗口底即知
            # 比它新的都拿到了）；只有触达条数上限、可能没枚举到窗口底时，才退回最老单元。
            fd = used_days.get((uid, stream))
            win_bottom = (N - fd * 86400.0) if fd else float(
                plans[uid][stream]["from"] or W0)
            cap = int(c.get("max_videos" if stream == "video" else "max_dyns") or 0)
            capped = bool(cap and cand and len(raw) >= cap)
            lo = min(cand) if (capped and cand) else win_bottom
            covc["min"] = min(old_min, lo) if old_min > 0 else lo
            covc["max"] = max(float(covc.get("max") or 0), N)
        for m in added_mentions.get(uid, []):
            arc["mentions"][_mkey(m)] = m
        save_archive(arc)

    # ---- 3. 聚合 ----
    cb(0.92, "聚合与排序…")
    stocks = _aggregate(mentions, c)
    stats = {
        "ups": len(ups), "days": days,
        "videos": tot_v, "dynamics": tot_d,
        "comments_scanned": n_comments,
        "up_comments": n_up_comments,
        "reposts": sum(1 for m in mentions if m["source"] == "repost"),
        "empty_dynamics": n_empty_dyn,
        "asr_videos": n_asr,
        "asr_skipped": n_asr_skip,
        "asr_note": asr_note,
        "asr_enabled": bool(c.get("video_local")),
        "subtitle_videos": sum(1 for m in mentions if m["source"] == "subtitle"),
        "mentions": len(mentions), "stocks": len(stocks),
        "requests": BUDGET.used, "request_cap": BUDGET.cap,
        "elapsed": round(time.time() - t0, 1),
        "dynamic_blocked": dyn_blocked,
        "comment_failed": CM_FAIL[0],
        "subtitle_login_blocked": n_sub_login,
        "has_sessdata": cred_state()["has_sessdata"],
        "win_start": win_start.strftime("%Y-%m-%d"),
        "win_end": datetime.now().strftime("%Y-%m-%d"),
        "use_local": use_local,
        "local_mentions": n_local,
        "local_ups": local_ups,
        "fetched_units": tot_v + tot_d,
        "archive_ups": sum(1 for u in uids if archives[u].get("mentions")),
    }
    if n_sub_login and not cred_state()["has_sessdata"]:
        notes.append(
            f"{n_sub_login} 个视频的字幕需登录才能读取（B站 已对匿名关闭字幕接口）；"
            "如需视频正文，可在「高级设置」里填入自己的 SESSDATA（仅存本地），"
            "或开启「本地视频文稿」（下载音频后离线转写，无需登录）。")
    if want_asr and n_asr:
        notes.append(f"已完成 {n_asr} 个视频的本地文稿转写（音频仅存本机，"
                     "识别全程离线）；文稿参与股票提及统计，权重同 UP主 本人内容。")
    if want_asr and n_asr_skip and not n_asr:
        notes.append(f"本地文稿转写未产出结果（{n_asr_skip} 个视频被跳过）："
                     f"{asr_note or '转写引擎异常'}")
    if not mentions:
        notes.append("未在所选范围内发现股票提及（可放宽时间范围或确认内容相关性）")
    if trunc:
        notes.append("达到请求上限，结果不完整；建议缩短时间范围或减少 UP主")
    if CM_FAIL[0]:
        notes.append(f"有 {CM_FAIL[0]} 处评论接口被风控拦截（评论为抽样，"
                     "这部分评论未纳入统计）；可稍后重试或用更低的请求间隔。")
    cb(1.0, "完成")
    out = {"stocks": stocks, "ups": ups, "stats": stats, "notes": notes}
    LAST.update(result=out, ts=time.time())
    return out


def summarize_last(cfg: Optional[dict] = None) -> dict:
    """对最近一次 scan 结果做总结（无结果时明确提示）。"""
    r = LAST.get("result")
    if not r:
        return {"lines": ["还没有可总结的抓取结果，请先执行一次抓取。"],
                "stocks": [], "factors": {}, "notes": []}
    out = summarize(r, cfg)
    out["scanned_at"] = datetime.fromtimestamp(LAST["ts"]).strftime(
        "%Y-%m-%d %H:%M:%S")
    return out


def _aggregate(mentions: list, c: dict) -> list:
    """按股票聚合 → 排序分降序（同分按最近时间降序）。"""
    by: dict = {}
    for m in mentions:
        g = by.setdefault(m["code"], {
            "code": m["code"], "name": m["name"], "score": 0.0,
            "up_mentions": 0, "other_mentions": 0, "total": 0,
            "last_ts": 0, "sources": set(), "mentions": [],
        })
        g["score"] += float(m["weight"])
        g["total"] += 1
        if m["is_up"]:
            g["up_mentions"] += 1
        else:
            g["other_mentions"] += 1
        g["last_ts"] = max(g["last_ts"], int(m["ts"] or 0))
        g["sources"].add(m["source_label"])
        g["mentions"].append(m)
    out = []
    for g in by.values():
        # 展开明细：① UP主本人优先 ② 时间降序（严格按需求）
        g["mentions"].sort(key=lambda x: (0 if x["is_up"] else 1,
                                          -int(x["ts"] or 0)))
        g["last_time"] = _fmt_ts(g["last_ts"])
        g["source_count"] = len(g["sources"])
        g["sources"] = sorted(g["sources"])
        g["is_up_only"] = g["other_mentions"] == 0
        g["score"] = round(g["score"], 2)
        out.append(g)
    out.sort(key=lambda g: (-g["score"], -g["last_ts"]))
    return out


# ============ 一键总结 ============
_POS = ("利好", "受益", "看好", "超预期", "增长", "放量", "涨价", "提价", "中标", "订单",
        "扩产", "投产", "扭亏", "翻倍", "新高", "突破", "回购", "增持", "上调", "推荐",
        "买入", "低估", "龙头", "核心", "壁垒", "景气", "拐点", "反转")
_NEG = ("利空", "承压", "下调", "减持", "亏损", "下滑", "不及预期", "低于预期", "商誉",
        "减值", "退市", "问询", "处罚", "违规", "诉讼", "破位", "高位", "风险", "泡沫",
        "高估", "卖出", "警惕", "回调", "分化", "内卷", "价格战")


def _polarity(text: str) -> int:
    t = str(text or "")
    p = sum(1 for w in _POS if w in t)
    n = sum(1 for w in _NEG if w in t)
    return 1 if p > n else (-1 if n > p else 0)


def _quote_ctx(codes: list, days: int = 20) -> dict:
    """结合本地行情（若有）：近 N 日涨跌幅 → 用于「是否已被市场反应」的印证。"""
    out = {}
    try:
        from ..core import db
        conn = db.reader()
        for code in codes[:60]:
            rows = conn.execute(
                "SELECT date, close, volume FROM daily WHERE code=? "
                "ORDER BY date DESC LIMIT ?", (code, days + 1)).fetchall()
            if len(rows) < 6:
                continue
            rows = list(reversed(rows))
            c0, c1 = float(rows[0][1] or 0), float(rows[-1][1] or 0)
            if c0 <= 0:
                continue
            chg = (c1 / c0 - 1) * 100
            vols = [float(r[2] or 0) for r in rows]
            vr = (sum(vols[-5:]) / 5) / (sum(vols) / len(vols)) if sum(vols) else 1.0
            out[code] = {"chg": round(chg, 2), "vol_ratio": round(vr, 2),
                         "last_date": rows[-1][0]}
    except Exception as e:  # noqa: BLE001
        _log_().warning(f"行情对照失败: {e}")
    return out


def summarize(result: dict, cfg: Optional[dict] = None) -> dict:
    """一键总结：多因子确定性汇总（热度/来源/时效/情绪/共现/行情印证）。"""
    c = _merge_cfg(cfg or {})
    stocks = list(result.get("stocks") or [])
    stats = result.get("stats") or {}
    if not stocks:
        return {"lines": ["范围内没有可总结的股票提及。"], "stocks": [],
                "factors": {}, "notes": result.get("notes") or []}

    now = time.time()
    all_m = [m for g in stocks for m in g["mentions"]]
    # 「UP主本体」= 本人撰写的内容 + 本人评论；「外围」= 其它用户评论 + 被转发的原文
    n_up = sum(1 for m in all_m if m["is_up"])
    n_other = len(all_m) - n_up
    n_repost = sum(1 for m in all_m if m.get("source") == "repost")
    n_inner = n_up + n_repost
    # ① 来源结构（分母只算「本人内容」，外围不计入 UP主 观点占比）
    up_share = n_up / max(1, n_up + n_repost + sum(
        1 for m in all_m
        if (not m["is_up"]) and m.get("source") != "repost"))
    # ② 时效分布
    recent7 = sum(1 for m in all_m if now - (m["ts"] or 0) <= 7 * 86400)
    recent30 = sum(1 for m in all_m if now - (m["ts"] or 0) <= 30 * 86400)
    # ③ 情绪（只由「本人内容 + UP主评论」定调，外围噪音不参与定调）
    sent = {}
    for g in stocks:
        v = sum(_polarity((m["snippet"] or "") + " " + (m.get("title") or ""))
                for m in g["mentions"] if m["is_up"])
        sent[g["code"]] = v
    # ④ 共现（同一条来源文本内同时出现的股票对）
    pair: dict = {}
    for m in all_m:
        key = (m["source_label"], m["title"], m["ts"])
        pair.setdefault(key, set()).add(m["code"])
    co: dict = {}
    for codes in pair.values():
        cs = sorted(codes)
        for i in range(len(cs)):
            for k in range(i + 1, len(cs)):
                co[(cs[i], cs[k])] = co.get((cs[i], cs[k]), 0) + 1
    top_co = sorted(co.items(), key=lambda x: -x[1])[:6]
    # ⑤ 行情印证
    q = _quote_ctx([g["code"] for g in stocks])

    name_of = {g["code"]: g["name"] for g in stocks}
    lines: list[str] = []
    lines.append(
        f"【样本】{stats.get('ups', 0)} 个 UP主 · 近 {stats.get('days', '?')} 天 · "
        f"视频 {stats.get('videos', 0)} 条 / 动态 {stats.get('dynamics', 0)} 条 · "
        f"共 {len(all_m)} 处股票提及，覆盖 {len(stocks)} 只个股"
        f"（其中 **UP主本人内容 {n_up} 处**"
        f"{'、被转发原文 ' + str(n_repost) + ' 处（非本人观点）' if n_repost else ''}"
        f"、其它用户评论 {n_other - n_repost} 处）。")
    if stats.get("dynamic_blocked"):
        lines.append("⚠ 动态接口当次被风控拦截，结论仅基于视频与评论。")

    lines.append(
        f"【来源结构】在「UP主本人内容」中，本人撰写/评论一字一句构成观点 "
        f"（{n_up} 处）；其余 {n_inner - n_up} 处为转发原文，仅作背景参考。"
        + ("观点高度集中于 UP主 本人，属单一信源，需自行交叉验证。"
           if up_share >= 0.6 else
           "评论参与度较高，可视为小范围共识（但评论区同样易受情绪放大）。"))

    lines.append(
        f"【时效】近 7 天 {recent7} 处、近 30 天 {recent30} 处。"
        + ("讨论集中在近期，话题仍在发酵。"
           if recent7 >= max(1, len(all_m) * 0.3) else
           "多数提及已过热度高点，属回溯性内容。"))

    top = stocks[:min(8, len(stocks))]
    lines.append("【热度前列】" + "；".join(
        f"{g['name']}({g['code']}) 权重分 {g['score']}"
        f"[UP主{g['up_mentions']}/评论{g['other_mentions']}"
        f"{'，情绪+' + str(sent[g['code']]) if sent.get(g['code'], 0) > 0 else ('，情绪' + str(sent[g['code']]) if sent.get(g['code'], 0) < 0 else '')}"
        f"{'，近20日' + ('%+.1f%%' % q[g['code']]['chg']) if g['code'] in q else ''}]"
        for g in top) + "。")

    strong = [g for g in stocks if sent.get(g["code"], 0) >= 2]
    weak = [g for g in stocks if sent.get(g["code"], 0) <= -2]
    if strong:
        lines.append("【偏多提及】" + "、".join(
            f"{g['name']}({g['code']})" for g in strong[:6])
            + " —— 语境以利好/受益/订单/扩产为主，注意区分「已兑现」与「预期」。")
    if weak:
        lines.append("【偏空提及】" + "、".join(
            f"{g['name']}({g['code']})" for g in weak[:6])
            + " —— 语境含承压/下调/减持等，勿只看多头叙事。")

    if top_co:
        lines.append("【共现线索】" + "；".join(
            f"{name_of.get(a, a)}+{name_of.get(b, b)} ×{n}"
            for (a, b), n in top_co))

    reacted = [g for g in stocks
               if g["code"] in q and q[g["code"]]["chg"] >= 15]
    quiet = [g for g in stocks
             if g["code"] in q and abs(q[g["code"]]["chg"]) <= 5]
    if reacted:
        lines.append("【行情印证】" + "、".join(
            f"{g['name']}{q[g['code']]['chg']:+.0f}%" for g in reacted[:6])
            + " 近 20 日已明显上行 —— 提及可能已被市场部分定价，追高需谨慎。")
    if quiet:
        lines.append("【尚未反应】" + "、".join(
            f"{g['name']}{q[g['code']]['chg']:+.1f}%" for g in quiet[:6])
            + " 近 20 日基本走平 —— 若逻辑成立，属预期尚未兑现的一类。")

    lines.append(
        "【风险提示】以上为对公开内容（视频/动态/评论）的机械汇总，"
        "不代表任何投资建议；单一 UP主 或小样本评论存在明显偏向与滞后，"
        f"本模块全程限速抓取（本次 {stats.get('requests', '?')} 次请求），"
        "评论为抽样而非全量。")
    factors = {
        "up_share": round(up_share, 3), "recent7": recent7, "recent30": recent30,
        "n_up": n_up, "n_other": n_other, "n_repost": n_repost,
        "co_occurrence": [{"a": name_of.get(a, a), "b": name_of.get(b, b), "n": n}
                          for (a, b), n in top_co],
        "sentiment": {name_of.get(k, k): v for k, v in sent.items() if v},
    }
    return {"lines": lines, "stocks": top, "factors": factors,
            "notes": result.get("notes") or []}


# ============ 供前端读取的静态信息 ============
def meta_info() -> dict:
    return {
        "defaults": DEFAULTS,
        "safety": {
            "min_interval": DEFAULTS["min_interval"],
            "max_requests": DEFAULTS["max_requests"],
            "concurrency": 1,
            "tls_fingerprint": _IMPERSONATE,
            "note": "全程串行 + 最小间隔限速 + 单轮请求硬闸；不读会员/充电专属内容、"
                    "不自动登录。默认只读接口不抓音视频；如开启「本地视频转写」"
                    "则仅在本机落盘后离线转文字（走 yt-dlp/ffmpeg，仍受同样的限速与硬闸）。",
        },
        "local_video": local_video_state(),
        "cred": cred_state(),
        "archive": archive_stats(),
        **session_state(),
    }
