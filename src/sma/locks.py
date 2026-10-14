"""文件锁：同一个账号工作区可能有多个 sma 进程（例如一边运行、一边续跑别的运行）。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:          # Windows 以后再支持；没有 fcntl 时不加锁
    fcntl = None


class LockBusy(Exception):
    pass


@contextmanager
def file_lock(path: Path, *, wait: bool = True) -> Iterator[None]:
    """独占锁。wait=False 时拿不到就抛 LockBusy。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise LockBusy(str(path)) from exc
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
