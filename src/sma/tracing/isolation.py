"""隔离检查（设计 05 第 2、5 节）。

- 写隔离：运行前后给工作区拍快照（不含 SMA 自己写的 runs/、.sma/、library/viral/），有变化就记下来；
- 串号：产物里出现本机登记的其他账号的名称或路径就报错，本人在 isolation.allowed_terms 里允许的除外。
"""

from __future__ import annotations

from pathlib import Path

SMA_WRITES = {"runs", ".sma", "library/viral"}


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    state: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if any(rel == p or rel.startswith(p + "/") for p in SMA_WRITES) or not path.is_file():
            continue
        stat = path.stat()
        state[rel] = (stat.st_size, stat.st_mtime_ns)
    return state


def compare(before: dict, after: dict) -> dict[str, list[str]]:
    return {"新增": sorted(set(after) - set(before)), "删除": sorted(set(before) - set(after)),
            "改动": sorted(k for k in set(before) & set(after) if before[k] != after[k])}


def foreign_mentions(text: str, others: list[dict], allowed: list[str]) -> list[str]:
    """text 里出现的其他账号名称或路径。others 来自账号登记：[{name, path}]。"""
    found = []
    allowed_set = set(allowed)
    for account in others:
        for term in (account.get("name", ""), account.get("path", "")):
            if len(term) >= 2 and term not in allowed_set and term in text:
                found.append(term)
    return sorted(set(found))
