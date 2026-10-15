"""流程定义（设计 03 第 2 节）：加载 YAML，把 foreach 步骤展开成并行实例。

- foreach 可以写死取值（values），也可以取上游步骤的结果（from: <步骤>.<字段>）：可选范围和默认值写在那个步骤的
  params.choices.<字段>，结果不合格或那个步骤失败时用默认值；
- gate：步骤成功后由代码判断往哪走（继续、阻塞、返工、重出），判断逻辑在 gates.py 里按名字注册；
- routed：平时跳过，只在被 gate 路由时运行（例如返工）；
- 输入的 feedback：读一个不在 needs 里的步骤的最新产物（例如下一轮创意读上一轮评审意见），读不到就算了。
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
    feedback: bool = False                # 读不在 needs 里的步骤的最新产物，总是可选
    select: Literal["top_by_outlier"] | None = None
    limit: int | None = Field(None, ge=1, le=100)

    @model_validator(mode="after")
    def _check(self):
        if self.source == "workspace" and self.type not in WORKSPACE_TYPES:
            raise ValueError(f"工作区输入只能是 {sorted(WORKSPACE_TYPES)}，不能是 {self.type}")
        if self.select == "top_by_outlier" and self.type != "viral-reference":
            raise ValueError("top_by_outlier 只能用在 viral-reference 上")
        if self.feedback:
            self.required = False
        return self


class ForeachSpec(_Strict):
    param: str
    values: list[dict] = Field(default_factory=list)
    source: str | None = Field(None, alias="from")       # <步骤>.<字段>

    @model_validator(mode="after")
    def _ids(self):
        if (self.source is None) == (not self.values):
            raise ValueError("foreach 要写 values 或 from 中的一个")
        if self.source and "." not in self.source:
            raise ValueError("foreach.from 的写法是 <步骤>.<字段>，例如 plan.research_lanes")
        ids = [v.get("id") for v in self.values]
        if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("foreach 的每个值都要有唯一的 id")
        return self


class OnFail(_Strict):
    action: Literal["fail", "block", "continue"] = "fail"     # continue：失败了也继续，下游用默认值
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
    gate: str | None = None
    gate_params: dict = Field(default_factory=dict)
    routed: bool = False

    @model_validator(mode="after")
    def _check(self):
        if (self.agent is None) == (self.code is None):
            raise ValueError("步骤要写 agent 或 code 中的一个")
        if self.code and not self.outputs:
            raise ValueError("代码步骤要写 outputs")
        count = None if self.foreach and self.foreach.source else (len(self.foreach.values) if self.foreach else 1)
        if count is not None and self.min_success and self.min_success > count:
            raise ValueError(f"min_success 是 {self.min_success}，但只有 {count} 个实例")
        if (self.routed or self.gate) and self.foreach:
            raise ValueError("routed 步骤和带 gate 的步骤不能用 foreach")
        return self

    def required_successes(self, count: int) -> int:
        return min(self.min_success or count, count)


class Workflow(_Strict):
    id: str
    version: int
    description: str = ""
    preconditions: list[Literal["positioning_confirmed"]] = Field(default_factory=list)
    steps: dict[str, StepDef]

    @model_validator(mode="after")
    def _needs(self):
        order = list(self.steps)
        routed = {name for name, step in self.steps.items() if step.routed}
        for name, step in self.steps.items():
            if not name.replace("-", "").replace("_", "").isalnum():
                raise ValueError(f"步骤名只能用字母、数字、- 和 _：{name}")
            for need in step.needs:
                if need not in self.steps:
                    raise ValueError(f"步骤 {name} 依赖了不存在的步骤 {need}")
                if order.index(need) >= order.index(name):
                    raise ValueError(f"步骤 {name} 依赖的 {need} 要写在它前面")
                if need in routed:
                    raise ValueError(f"步骤 {name} 不能依赖 routed 步骤 {need}；要用它的产物就写成 feedback 输入")
            for spec in step.inputs:
                if spec.source == "workspace" or spec.feedback:
                    if spec.feedback and spec.source not in self.steps:
                        raise ValueError(f"步骤 {name} 的 feedback 输入来自不存在的步骤 {spec.source}")
                    continue
                if spec.source not in step.needs:
                    raise ValueError(f"步骤 {name} 的输入来自 {spec.source}，要先写进 needs，或者写成 feedback 输入")
            if step.foreach and step.foreach.source:
                source, _, field_ = step.foreach.source.partition(".")
                if source not in step.needs or field_ not in self.steps[source].params.get("choices", {}):
                    raise ValueError(f"步骤 {name} 的 foreach.from 要指向 needs 里步骤的 params.choices.{field_ or '<字段>'}")
            for field_, spec in step.params.get("choices", {}).items():
                ids = [v.get("id") for v in spec.get("values", [])]
                if not ids or len(set(ids)) != len(ids) or not set(spec.get("default", [])) <= set(ids):
                    raise ValueError(f"步骤 {name} 的 choices.{field_}：values 要有唯一的 id，default 要从 values 里选")
                if not spec.get("min", 1) <= len(spec["default"]) <= spec.get("max", len(ids)):
                    raise ValueError(f"步骤 {name} 的 choices.{field_}：default 的数量要在 min 和 max 之间")
        return self

    def downstream(self, step: str) -> set[str]:
        """直接或间接依赖 step 的步骤。"""
        found: set[str] = set()
        for name, definition in self.steps.items():
            if step in definition.needs or found & set(definition.needs):
                found.add(name)
        return found

    def choices(self, source: str) -> dict:
        """foreach.from 指向的可选范围：{values, default, min, max}。"""
        step, _, field_ = source.partition(".")
        return self.steps[step].params["choices"][field_]


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


def expand(workflow: Workflow, dynamic: dict[str, list[dict] | None] | None = None) -> list[Instance]:
    """展开成实例。dynamic：foreach.from 的取值；某个来源还没有结果（None）时，对应的步骤暂时没有实例。"""
    dynamic = dynamic or {}
    instances: list[Instance] = []
    for name, step in workflow.steps.items():
        if step.foreach is None:
            instances.append(Instance(name, name, dict(step.params)))
            continue
        values = step.foreach.values if step.foreach.source is None else dynamic.get(step.foreach.source)
        for value in values or []:
            instances.append(Instance(name, f"{name}[{value['id']}]", {**step.params, step.foreach.param: value}))
    return instances


def resolve_choice(workflow: Workflow, source: str, picked: list | None) -> list[dict]:
    """把上游选的 id（或带 focus 的对象）换成可选范围里的完整取值；没有选或选得不合法时用默认值。"""
    spec = workflow.choices(source)
    by_id = {v["id"]: v for v in spec["values"]}
    chosen = []
    for item in picked or []:
        key = item.get("id") if isinstance(item, dict) else item
        if key in by_id and all(c["id"] != key for c in chosen):
            extra = {k: v for k, v in item.items() if k != "id"} if isinstance(item, dict) else {}
            chosen.append({**by_id[key], **extra})
    low, high = spec.get("min", 1), spec.get("max", len(by_id))
    if not low <= len(chosen) <= high:
        chosen = [by_id[i] for i in spec["default"]]
    return chosen
