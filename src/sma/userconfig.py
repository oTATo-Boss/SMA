"""用户级配置 config.yaml（设计 04 第 8 节第 4 条）：各档位在各引擎上的默认型号，所有账号共用。

```yaml
models:
  tiers:
    strong: { codex: <型号> }
```
"""

from __future__ import annotations

from pathlib import Path

import yaml

from sma.paths import user_config_dir
from sma.workspace.config import Models


def user_config_path() -> Path:
    return user_config_dir() / "config.yaml"


def load_user_models() -> Models:
    path = user_config_path()
    if not path.is_file():
        return Models()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Models.model_validate(data.get("models") or {})
