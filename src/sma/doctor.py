"""环境检查（sma doctor）：Python、引擎和登录、Agent 包和技能、流程定义、账号工作区、疑似密钥。只读。"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from sma.engines.base import Engine
from sma.engines.codex import CodexEngine
from sma.orchestrator.workflow import parse_workflow
from sma.packages.loader import lint_package, list_packages
from sma.paths import workflows_dir
from sma.tracing.redact import scan_secrets, secret_values
from sma.userconfig import load_user_models, user_config_path
from sma.workspace import registry
from sma.workspace.workspace import Workspace, WorkspaceError


@dataclass
class Item:
    name: str
    ok: bool | None          # True 通过，None 提醒，False 失败
    detail: str = ""


def check_environment(engines: dict[str, Engine]) -> list[Item]:
    items = [Item("Python", sys.version_info >= (3, 11), platform.python_version())]
    for name, engine in engines.items():
        state = engine.check()
        ok = state.available and state.logged_in is not False
        items.append(Item(f"引擎 {name}", ok if state.logged_in is not None else (None if state.available else False),
                          " · ".join(x for x in [state.version or "", state.detail] if x)))
        if isinstance(engine, CodexEngine) and state.available:
            link = engine.home / "auth.json"
            items.append(Item("SMA 的 Codex 主目录", link.is_symlink() or None,
                              f"{engine.home}（登录文件是软链接，不复制凭据）" if link.is_symlink() else f"{engine.home} 里还没有登录文件的软链接"))
    tiers = load_user_models().tiers
    items.append(Item("默认型号", True if tiers else None,
                      ", ".join(f"{t}: {m}" for t, m in sorted(tiers.items())) if tiers else f"没有配置，用引擎默认型号（可写 {user_config_path()}）"))
    for agent_id in list_packages():
        errors, warnings = lint_package(agent_id)
        items.append(Item(f"包 {agent_id}", False if errors else (None if warnings else True), "；".join(errors + warnings)))
    for path in sorted(workflows_dir().glob("*.yaml")):
        try:
            parse_workflow(path.read_text(encoding="utf-8"))
            items.append(Item(f"流程 {path.stem}", True))
        except Exception as exc:  # 校验错误原样报出来
            items.append(Item(f"流程 {path.stem}", False, str(exc).splitlines()[0]))
    return items


def check_account(root: Path) -> list[Item]:
    try:
        ws = Workspace(root)
    except (WorkspaceError, ValueError) as exc:
        return [Item("账号配置", False, str(exc).splitlines()[0])]
    items = [Item("账号配置", True, f"{ws.name}（{ws.root}）")]
    registered = any(Path(a["path"]).resolve() == ws.root for a in registry.list_accounts())
    items.append(Item("账号登记", registered or None, "已登记" if registered else "没有登记，隔离检查认不出它：运行 sma account register 登记"))
    items.append(Item("定位确认", ws.positioning_confirmed() or None,
                      "已确认" if ws.positioning_confirmed() else '还没确认或确认后改过：写好 strategy/positioning.md 后运行 sma strategy confirm --note "原话"'))
    hits = scan_secrets(ws.root, secret_values(), skip={".sma"})
    items.append(Item("疑似密钥", not hits, "；".join(hits[:10]) if hits else "没有发现"))
    return items
