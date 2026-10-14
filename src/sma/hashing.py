"""内容哈希：产物、确认、检查都靠它绑定版本。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(data) -> str:
    """稳定的 JSON 文本：键排序、中文不转义，写盘和算哈希都用它。"""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
