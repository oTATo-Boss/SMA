"""时间：一律记北京时间，带时区。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")


def now() -> datetime:
    return datetime.now(ZONE)


def stamp() -> str:
    """ISO 格式，精确到秒。"""
    return now().isoformat(timespec="seconds")


def compact() -> str:
    """用于 id：20261015-093012。"""
    return now().strftime("%Y%m%d-%H%M%S")
