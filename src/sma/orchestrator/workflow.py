"""流程定义（设计 03 第 2 节）：加载 YAML，把 foreach 步骤展开成并行实例。

M1 支持 needs、foreach、params、inputs、outputs、min_success、on_fail；loop、transform、checkpoint 留到 M2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sma.packages.model import WORKSPACE_INPUTS as WORKSPACE_TYPES
from sma.packages.model import IOSpec
from sma.paths import workflows_dir


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InputSpec(_Strict):
    source: str = Field(alias="from")     # 上游步骤 id，或 workspace（账号工作区里的文件）
    type: str
    required: bool = True
    select: Literal["top_by_outlier"] | None = None
    limit: int | None = Field(None, ge=1, le=100)

    @model_validator(mode="after")
    def _check(self):
        if self.source == "workspace" and self.type not in WORKSPACE_TYPES:
            raise ValueError(f"工作区输入只能是 {sorted(WORKSPACE_TYPES)}，不能是 {self.type}")
        if self.select == "top_by_outlier" and self.type != "viral-reference":
            raise ValueError("top_by_outlier 只能用在 viral-reference 上")
        return self


class ForeachSpec(_Strict):
    param: str
    values: list[dict]

    @model_validator(mode="after")
    def _ids(self):
        ids = [v.get("id") for v in self.values]
        if not ids or not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("foreach 至少一个值，每个值都要有唯一的 id")
        return self


class OnFail(_Strict):
    action: Literal["fail", "block"] = "fail"
    reason: str = "step-failed"


class StepDef(_Strict):
    agent: str | None = None
    code: str | None = None
    needs: list[str] = Field(default_factory=list)
    foreach: ForeachSpec | None = None
    params: dict = Field(default_factory=dict)
    inputs: list[InputSpec] = Field(default_factory=list)
    outputs: list[IOSpec] = Field(default_factory=list)      # 代码步骤必填；Agent 步骤以包的声明为准
    min_success: int | None = Field(None, ge=1)
    on_fail: OnFail = Field(default_factory=OnFail)

    @model_validator(mode="after")
    def _check(self):
        if (self.agent is None) == (self.code is None):
            raise ValueError("步骤要写 agent 或 code 中的一个")
        if self.code and not self.outputs:
            raise ValueError("代码步骤要写 outputs")
        count = len(self.foreach.values) if self.foreach else 1
        if self.min_success and self.min_success > count:
            raise ValueError(f"min_success 是 {self.min_success}，但只有 {count} 个实例")
        return self

    @property
    def required_successes(self) -> int:
        return self.min_success or (len(self.foreach.values) if self.foreach else 1)


class Workflow(_Strict):
    id: str
    version: int
    description: str = ""
    preconditions: list[Literal["positioning_confirmed"]] = Field(default_factory=list)
    steps: dict[str, StepDef]

    @model_validator(mode="after")
    def _needs(self):
        order = list(self.steps)
        for name, step in self.steps.items():
            if not name.replace("-", "").replace("_", "").isalnum():
                raise ValueError(f"步骤名只能用字母、数字、- 和 _：{name}")
            for need in step.needs:
                if need not in self.steps:
                    raise ValueError(f"步骤 {name} 依赖了不存在的步骤 {need}")
                if order.index(need) >= order.index(name):
                    raise ValueError(f"步骤 {name} 依赖的 {need} 要写在它前面")
            for spec in step.inputs:
                if spec.source != "workspace" and spec.source not in step.needs:
                    raise ValueError(f"步骤 {name} 的输入来自 {spec.source}，要先写进 needs")
        return self

    def downstream(self, step: str) -> set[str]:
        """直接或间接依赖 step 的步骤。"""
        found: set[str] = set()
        for name, definition in self.steps.items():
            if step in definition.needs or found & set(definition.needs):
                found.add(name)
        return found


@dataclass(frozen=True)
class Instance:
    step: str                      # 基础步骤 id
    id: str                        # 实例 id，例如 research[viral]
    params: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def slug(self) -> str:
        """文件系统和产物 id 里用的名字：research[viral] → research-viral。"""
        return self.id.replace("[", "-").replace("]", "")


def workflow_path(workflow_id: str) -> Path:
    return workflows_dir() / f"{workflow_id}.yaml"


def parse_workflow(text: str) -> Workflow:
    return Workflow.model_validate(yaml.safe_load(text))


def load_workflow(workflow_id: str) -> Workflow:
    path = workflow_path(workflow_id)
    if not path.is_file():
        raise FileNotFoundError(f"没有这个流程：{workflow_id}")
    return parse_workflow(path.read_text(encoding="utf-8"))


def expand(workflow: Workflow) -> list[Instance]:
    instances: list[Instance] = []
    for name, step in workflow.steps.items():
        if step.foreach is None:
            instances.append(Instance(name, name, dict(step.params)))
        else:
            for value in step.foreach.values:
                instances.append(Instance(name, f"{name}[{value['id']}]", {**step.params, step.foreach.param: value}))
    return instances
