from __future__ import annotations

import pytest
from helpers import POSITIONING

from sma.packages import skills
from sma.workspace import registry
from sma.workspace.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """每个测试都用临时的用户配置和技能目录，不碰本机的真实配置。"""
    monkeypatch.setenv("SMA_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SMA_SKILL_ROOTS", str(tmp_path / "skills"))
    monkeypatch.delenv("SMA_RESOURCE_ROOT", raising=False)
    skills.clear_cache()
    yield
    skills.clear_cache()


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    ws = Workspace.init(tmp_path / "@测试号", "测试号", ["抖音"])
    (ws.root / "strategy" / "positioning.md").write_text(POSITIONING, encoding="utf-8")
    ws.confirm_positioning("可以，就按这个来")
    registry.register(ws.name, ws.root)
    return ws
