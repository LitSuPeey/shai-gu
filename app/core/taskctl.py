# -*- coding: utf-8 -*-
"""运行中任务的取消机制。

routes 层在每次 _task_start 时重置 flag；
POST /api/cancel 设置 flag；
各筛选模块的 progress_cb（即 routes._task_progress）检测到 flag 后抛 TaskCancelled，
异常向上冒泡终止计算，ProcessPool 场景配合 shutdown(cancel_futures=True) 快速回收。
"""
import threading


class TaskCancelled(Exception):
    """用户主动取消了任务。"""


class CancelFlag:
    def __init__(self):
        self._lock = threading.Lock()
        self._flag = False

    def reset(self) -> None:
        with self._lock:
            self._flag = False

    def set(self) -> None:
        with self._lock:
            self._flag = True

    def is_set(self) -> bool:
        with self._lock:
            return self._flag

    def check(self) -> None:
        """在 progress 回调里调用：若已请求取消则抛 TaskCancelled。"""
        if self.is_set():
            raise TaskCancelled("任务已被用户取消")


CANCEL = CancelFlag()
