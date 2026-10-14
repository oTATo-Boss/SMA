"""按名字查找本机技能（扫描方式移植自 AI-SMA 的环境检查）。

SMA 不依赖引擎自动发现技能：按包的声明找到 SKILL.md，由编排层把内容写进步骤指令。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_ROOTS = ["~/.agents/skills", "~/.codex/skills", "~/.claude/skills"]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
NAME = re.compile(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.M)


def skill_roots() -> list[Path]:
    """技能根目录：环境变量 SMA_SKILL_ROOTS（用系统路径分隔符分隔）优先，否则用常见位置。"""
    raw = os.environ.get("SMA_SKILL_ROOTS")
    roots = raw.split(os.pathsep) if raw else DEFAULT_ROOTS
    return [Path(r).expanduser() for r in roots if r]


def frontmatter_name(skill_md: Path) -> str | None:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    match = re.match(r"---\n(.*?)\n---", text, re.S)
    found = NAME.search(match.group(1)) if match else None
    return found.group(1).strip() if found else None


@lru_cache(maxsize=4)
def _index(roots: tuple[Path, ...], depth: int = 6) -> dict[str, Path]:
    index: dict[str, Path] = {}
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root, followlinks=True):
            real = os.path.realpath(current)
            level = len(Path(current).relative_to(root).parts)
            if real in seen or level > depth:
                dirs[:] = []
                continue
            seen.add(real)
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            if "SKILL.md" in files:
                skill_md = Path(current) / "SKILL.md"
                for name in {skill_md.parent.name, frontmatter_name(skill_md) or ""} - {""}:
                    index.setdefault(name.lower(), skill_md)
    return index


def find_skill(name: str) -> Path | None:
    """按名字（目录名或 frontmatter 的 name，不分大小写）找 SKILL.md。"""
    return _index(tuple(skill_roots())).get(name.lower())


def clear_cache() -> None:
    _index.cache_clear()
