"""编排层的两种中断：阻塞（要本人处理）和步骤失败（不重试）。"""

from __future__ import annotations


class Blocked(Exception):
    """需要本人处理才能继续，对应 blocking-report 的 reason。"""

    def __init__(self, reason: str, needed: list[str], detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.needed = needed
        self.detail = detail


class StepError(Exception):
    """步骤没法执行，例如缺少必需的输入、上游产物被改过；重试也没用。"""
