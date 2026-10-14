"""模型路由（设计 04 第 8 节）：先找到的为准——账号配置里对这个包的指定 → 账号的档位映射 → 用户级的档位映射 → 引擎默认。"""

from __future__ import annotations

from dataclasses import dataclass

from sma.packages.loader import Package
from sma.workspace.config import AccountConfig, Models


@dataclass(frozen=True)
class Route:
    engine: str
    model: str | None
    reasoning: str


def resolve(package: Package, config: AccountConfig | None, available: list[str], user_models: Models | None = None) -> Route:
    spec = package.spec
    choice = config.models.agents.get(spec.id) if config else None
    engines = [choice.engine] if choice and choice.engine else list(spec.engine.prefer)
    engine = next((e for e in engines if e in available), None)
    if engine is None:
        raise RuntimeError(f"{spec.id} 要用的引擎 {engines} 都不可用（可用：{available}）")
    model = choice.model if choice and choice.model else None
    for models in (config.models if config else None, user_models):
        if model is None and models is not None:
            model = models.tiers.get(spec.model.tier, {}).get(engine)
    reasoning = choice.reasoning if choice and choice.reasoning else spec.reasoning
    return Route(engine=engine, model=model, reasoning=reasoning)
