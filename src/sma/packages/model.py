"""agent.yaml 的模型（设计 01 第 3 节）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 不是产物、由编排层从账号工作区取的输入类型
WORKSPACE_INPUTS = {"strategy", "benchmarks", "data-files"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IOSpec(_Strict):
    type: str
    required: bool = True
    many: bool = False


class EngineSpec(_Strict):
    prefer: list[Literal["codex", "claude", "api"]] = Field(default_factory=lambda: ["codex"])


class ModelSpec(_Strict):
    family: Literal["openai", "anthropic", "any"] = "any"
    tier: Literal["strong", "standard", "fast"] = "standard"


class SkillRef(_Strict):
    name: str
    required: bool = True


class ToolRef(_Strict):
    server: str
    tools: list[str] = Field(default_factory=list)


class MemorySpec(_Strict):
    read: bool = False


class Independence(_Strict):
    differ_from: list[str] = Field(default_factory=list)


class Limits(_Strict):
    timeout_s: int = Field(900, ge=30, le=7200)
    max_attempts: int = Field(3, ge=1, le=6)
    max_output_kb: int = Field(256, ge=8, le=4096)


class AgentPackage(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str
    kind: Literal["worker", "critic", "planner"]
    inputs: list[IOSpec] = Field(default_factory=list)
    outputs: list[IOSpec]
    engine: EngineSpec = Field(default_factory=EngineSpec)
    model: ModelSpec = Field(default_factory=ModelSpec)
    reasoning: Literal["low", "medium", "high", "xhigh"] = "medium"
    sandbox: Literal["read-only", "step-write"]
    network: Literal["none", "tools-only", "web"]
    tools: list[ToolRef] = Field(default_factory=list)
    skills: list[SkillRef] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    memory: MemorySpec = Field(default_factory=MemorySpec)
    independence: Independence = Field(default_factory=Independence)
    blind: list[str] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
