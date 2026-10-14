"""密钥脱敏（设计 05 第 4 节）：写盘的追踪记录里，已知密钥的值一律替换掉。

已知密钥 = 进程环境里名字像密钥的变量的值；另外按格式识别 Bearer 令牌。思路沿用 AI-SMA 的项目检查。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._\-]{24,}")
TEXT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".csv", ".log", ".py", ".sh", ".env", ".html"}


def secret_values(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """{变量名: 值}；值太短或像路径的不算。"""
    env = os.environ if env is None else env
    return {name: value for name, value in env.items()
            if SECRET_NAME.search(name) and len(value) >= 8 and not value.startswith(("/", "~"))}


def redact_text(text: str, secrets: Mapping[str, str]) -> str:
    for name, value in sorted(secrets.items(), key=lambda kv: -len(kv[1])):
        text = text.replace(value, f"[已脱敏:{name}]")
    return BEARER.sub("Bearer [已脱敏]", text)


def redact_file(path: Path, secrets: Mapping[str, str]) -> bool:
    """有改动返回 True。"""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    clean = redact_text(text, secrets)
    if clean != text:
        path.write_text(clean, encoding="utf-8")
        return True
    return False


def scan_secrets(root: Path, secrets: Mapping[str, str], skip: set[str] | None = None) -> list[str]:
    """工作区里疑似含密钥的文本文件（只报告相对路径，不输出内容）。"""
    skip = skip or set()
    hits = []
    values = list(secrets.values())
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES or set(rel.parts) & skip
                or path.stat().st_size > 5_000_000):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        if any(v in text for v in values) or BEARER.search(text):
            hits.append(rel.as_posix())
    return hits
