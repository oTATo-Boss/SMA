"""加载和检查 Agent 包（设计 01）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from sma.contracts.schemas import OutputSpec, content_schema
from sma.packages.model import WORKSPACE_INPUTS, AgentPackage
from sma.packages.skills import find_skill
from sma.paths import agents_dir

SECTIONS = ["你是谁", "你会收到什么", "你要交什么", "怎么做", "质量标准", "不做什么"]


class PackageError(Exception):
    pass


@dataclass
class Package:
    spec: AgentPackage
    root: Path
    instructions: str

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def version(self) -> str:
        return self.spec.version

    @property
    def output_specs(self) -> list[OutputSpec]:
        return [OutputSpec(o.type, o.many) for o in self.spec.outputs]

    def knowledge_texts(self) -> list[tuple[str, str]]:
        return [(rel, (self.root / rel).read_text(encoding="utf-8")) for rel in self.spec.knowledge]


def package_dir(agent_id: str) -> Path:
    return agents_dir() / agent_id


def list_packages() -> list[str]:
    return sorted(p.name for p in agents_dir().iterdir() if (p / "agent.yaml").is_file()) if agents_dir().exists() else []


def load_package(agent_id: str) -> Package:
    root = package_dir(agent_id)
    config = root / "agent.yaml"
    if not config.is_file():
        raise PackageError(f"没有这个 Agent 包：{agent_id}")
    try:
        spec = AgentPackage.model_validate(yaml.safe_load(config.read_text(encoding="utf-8")) or {})
    except ValidationError as exc:
        raise PackageError(f"{agent_id}/agent.yaml 不合格：{exc}") from exc
    if spec.id != agent_id:
        raise PackageError(f"{agent_id}/agent.yaml 的 id 是 {spec.id}，和目录名不一致")
    instructions = root / "instructions.md"
    if not instructions.is_file():
        raise PackageError(f"{agent_id} 缺少 instructions.md")
    return Package(spec=spec, root=root, instructions=instructions.read_text(encoding="utf-8"))


def lint_package(agent_id: str) -> tuple[list[str], list[str]]:
    """返回 (错误, 提醒)。"""
    try:
        package = load_package(agent_id)
    except PackageError as exc:
        return [str(exc)], []
    errors: list[str] = []
    warnings: list[str] = []
    spec = package.spec
    for section in SECTIONS:
        if f"## {section}" not in package.instructions:
            errors.append(f"instructions.md 缺少「{section}」一节")
    for io in [*spec.inputs, *spec.outputs]:
        if io.type in WORKSPACE_INPUTS:
            continue
        try:
            content_schema(io.type)
        except KeyError:
            errors.append(f"没有产物类型 {io.type} 的 Schema")
    for rel in spec.knowledge:
        if not (package.root / rel).is_file():
            errors.append(f"knowledge 里的文件不存在：{rel}")
    for skill in spec.skills:
        if find_skill(skill.name) is None:
            (errors if skill.required else warnings).append(f"本机没有技能 {skill.name}" + ("" if skill.required else "（可选）"))
    if spec.kind == "critic" and not spec.independence.differ_from:
        errors.append("评审类包要写 independence.differ_from")
    cases = package.root / "evals" / "cases.jsonl"
    if not cases.is_file():
        warnings.append("没有评测集 evals/cases.jsonl")
    else:
        for number, line in enumerate(cases.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"evals/cases.jsonl 第 {number} 行不是 JSON：{exc}")
                continue
            for rel in case.get("inputs", {}).values():
                for item in rel if isinstance(rel, list) else [rel]:
                    if not (package.root / "evals" / item).is_file():
                        errors.append(f"评测用例 {case.get('id')} 的输入不存在：{item}")
    for name in ("CHANGELOG.md", "README.md"):
        if not (package.root / name).is_file():
            warnings.append(f"缺少 {name}")
    return errors, warnings
