"""资源和配置的位置。

M1 在仓库里直接运行（uv 的可编辑安装），资源目录就是仓库根目录；以后打包发行时改为包内资源。
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """schemas/、agents/、workflows/ 所在的目录。可用环境变量 SMA_RESOURCE_ROOT 覆盖。"""
    return Path(os.environ.get("SMA_RESOURCE_ROOT", REPO_ROOT))


def schemas_dir() -> Path:
    return resource_root() / "schemas"


def agents_dir() -> Path:
    return resource_root() / "agents"


def workflows_dir() -> Path:
    return resource_root() / "workflows"


def user_config_dir() -> Path:
    """用户级配置（账号登记、引擎路径等）。可用环境变量 SMA_CONFIG_DIR 覆盖，测试会用到。"""
    return Path(os.environ.get("SMA_CONFIG_DIR", Path.home() / ".config" / "sma"))
