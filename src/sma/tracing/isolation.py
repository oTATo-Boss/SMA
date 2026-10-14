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


def _spans(text: str, term: str) -> list[tuple[int, int]]:
    spans, start = [], text.find(term)
    while start >= 0:
        spans.append((start, start + len(term)))
        start = text.find(term, start + 1)
    return spans


def mask_term(term: str) -> str:
    """提示里只露出首尾字，不把其他账号的名称写进本账号的记录。"""
    if "/" in term or "\\" in term:
        return "某个账号的路径"
    return f"{term[0]}…{term[-1]}" if len(term) >= 3 else f"{term[0]}…"


def foreign_mentions(data, others: list[dict], allowed: list[str], path: str = "$") -> list[tuple[str, str]]:
    """data（产物 JSON）里出现的其他账号名称或路径，返回 [(JSON 路径, 遮住的名称)]。

    others 来自账号登记：[{name, path}]。被允许的词（包括本账号自己的名字）覆盖住的位置不算，
    例如本账号叫"测试号"、另一个账号叫"测试"时，"测试号"不会误报；反过来另一个账号更长时照样报出来。
    """
    if isinstance(data, dict):
        return [hit for key, value in data.items() for hit in foreign_mentions(value, others, allowed, f"{path}.{key}")]
    if isinstance(data, list):
        return [hit for i, value in enumerate(data) for hit in foreign_mentions(value, others, allowed, f"{path}[{i}]")]
    if not isinstance(data, str):
        return []
    covered = [span for term in allowed if term for span in _spans(data, term)]
    hits = []
    for account in others:
        for term in (account.get("name", ""), account.get("path", "")):
            if len(term) < 2 or term in allowed:
                continue
            for start, end in _spans(data, term):
                if not any(a <= start and end <= b for a, b in covered):
                    hits.append((path, mask_term(term)))
                    break
    return hits
