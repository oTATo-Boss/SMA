"""本机登记的账号工作区（用户级配置），隔离检查要用这份清单。"""

from __future__ import annotations

from pathlib import Path

import yaml

from sma.paths import user_config_dir


def _path() -> Path:
    return user_config_dir() / "accounts.yaml"


def list_accounts() -> list[dict]:
    path = _path()
    if not path.is_file():
        return []
    return list((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("accounts", []))


def register(name: str, root: Path) -> None:
    accounts = [a for a in list_accounts() if Path(a["path"]).resolve() != root.resolve()]
    accounts.append({"name": name, "path": str(root.resolve())})
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"accounts": accounts}, allow_unicode=True, sort_keys=False), encoding="utf-8")


def other_accounts(root: Path) -> list[dict]:
    """除了 root 以外登记过的账号。"""
    return [a for a in list_accounts() if Path(a["path"]).resolve() != root.resolve()]
