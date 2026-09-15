# -*- coding: utf-8 -*-
"""常驻进程池管理器（pattern/ant 共用）。

Windows spawn 模式下每次 scan 新建 ProcessPoolExecutor，每个 worker 都要
重启 Python + import pandas/numpy（约 2-4s 固定开销）。本模块让池惰性创建、
常驻复用，多次扫描免去重复建池成本。

- 统一 initializer：只做 db.set_db_path（各模块私有状态在 worker 内惰性加载）
- 池大小取首次请求的 max_workers（之后固定；前端调参仅首次生效）
- 取消/异常时 discard_pool() 丢弃（cancel_futures），下次扫描惰性重建，行为与旧版一致
- 副作用：常驻 worker 内的模块级缓存（如蚂蚁指数）在 DB 更新后可能滞后，
  取消或进程重启后自动刷新；对扫描结果影响可忽略
"""
import os
import threading

_LOCK = threading.Lock()
_POOL: dict = {"ex": None, "workers": 0}


def _pool_init(db_path: str) -> None:
    """子进程统一初始化（可 pickle 的模块级函数）。"""
    from . import db
    db.set_db_path(db_path)


def get_pool(max_workers: int, db_path: str):
    """取常驻池：无则惰性创建（首次调用者决定池大小）。"""
    with _LOCK:
        ex = _POOL["ex"]
        if ex is None:
            from concurrent.futures import ProcessPoolExecutor
            workers = max(1, min(int(max_workers), os.cpu_count() or 4))
            ex = ProcessPoolExecutor(max_workers=workers,
                                     initializer=_pool_init,
                                     initargs=(db_path,))
            _POOL["ex"], _POOL["workers"] = ex, workers
        return ex


def discard_pool() -> None:
    """取消/异常时丢弃常驻池（不等跑完、取消未开始任务），下次扫描重建。"""
    with _LOCK:
        ex = _POOL["ex"]
        _POOL["ex"] = None
    if ex is not None:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass
