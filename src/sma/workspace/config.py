"""账号配置 sma.yaml 的模型（设计 06 第 3 节）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountInfo(_Strict):
    name: str
    platforms: list[str] = Field(default_factory=list)
    timezone: str = "Asia/Shanghai"


class Autonomy(_Strict):
    mode: Literal["full", "checkpoints"] = "full"
    max_parallel_steps: int = Field(3, ge=1, le=8)
    idea_rounds_max: int = Field(2, ge=0, le=5)
    rework_rounds_max: int = Field(2, ge=0, le=5)
    explore_ratio: float = Field(0.2, ge=0, le=1)


class ModelChoice(_Strict):
    engine: str | None = None
    model: str | None = None
    family: str | None = None
    reasoning: str | None = None


class Models(_Strict):
    tiers: dict[str, dict[str, str]] = Field(default_factory=dict)
    agents: dict[str, ModelChoice] = Field(default_factory=dict)


class Authorization(_Strict):
    note: str
    date: str
    scope: str = ""


class Isolation(_Strict):
    allowed_terms: list[str] = Field(default_factory=list)


class AccountConfig(_Strict):
    schema_version: int = 1
    account: AccountInfo
    autonomy: Autonomy = Field(default_factory=Autonomy)
    models: Models = Field(default_factory=Models)
    routes: list[str] = Field(default_factory=list)
    authorizations: dict[str, Authorization] = Field(default_factory=dict)
    isolation: Isolation = Field(default_factory=Isolation)
    deliverables: dict = Field(default_factory=dict)
