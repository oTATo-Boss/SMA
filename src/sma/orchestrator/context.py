"""上下文装配（设计 03 第 7 节、04 第 3 节）：为一次尝试准备目录。

目录里有：AGENTS.md（策略摘要 + 角色指令 + 知识 + 技能 + 账号经验）、CLAUDE.md、inputs/、schema.json、prompt.md；
任务正文只写本人原话、本步骤参数和输入清单。每一步只拿到声明的输入，不带前面的聊天记录。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sma.contracts.schemas import output_schema
from sma.packages.loader import Package
from sma.packages.skills import find_skill
from sma.policy.summary import policy_summary


@dataclass
class InputFile:
    name: str                                       # inputs/ 里的文件名
    description: str                                # 一句话用途
    content: str
    refs: list[dict] = field(default_factory=list)  # 来源和哈希，写进产物信封的 inputs


@dataclass
class Assembled:
    prompt: str
    schema_path: Path


class MissingSkill(Exception):
    pass


def agents_md(package: Package, run_label: str, memory: str | None) -> str:
    spec = package.spec
    parts = [f"# 本步骤：{spec.name}（{spec.id} {spec.version}）", "", f"运行：{run_label}", "", policy_summary(spec), "",
             "## 角色指令", "", package.instructions.strip()]
    for rel, text in package.knowledge_texts():
        parts += ["", f"## 参考：{rel}", "", text.strip()]
    skills = []
    for skill in spec.skills:
        path = find_skill(skill.name)
        if path is None:
            if skill.required:
                raise MissingSkill(f"{spec.id} 需要的技能 {skill.name} 本机没有")
            continue
        skills.append((skill.name, path))
    if skills:
        parts += ["", "## 技能", "", "下面的技能是方法参考：和角色指令冲突时以角色指令为准；不向本人提问，信息不够时写明假设；"
                  "技能目录里的参考资料可以只读查阅。"]
        for name, path in skills:
            parts += ["", f"### 技能：{name}", "", f"（技能目录：{path.parent}）", "", path.read_text(encoding="utf-8").strip()]
    if memory:
        parts += ["", "## 本账号的经验", "", memory.strip()]
    return "\n".join(parts) + "\n"


def write_prompt(task: str, params: dict, inputs: list[InputFile], extra_paths: list[tuple[str, str]], repair: list[str] | None) -> str:
    lines = ["任务（本人原话）：", task.strip(), ""]
    if params:
        lines += ["本步骤参数：", json.dumps(params, ensure_ascii=False, indent=1), ""]
    if inputs:
        lines.append("输入文件（在 inputs/ 目录里）：")
        lines += [f"- inputs/{i.name}：{i.description}" for i in inputs]
        lines.append("")
    if extra_paths:
        lines.append("本人提供的资料（只读）：")
        lines += [f"- {path}：{desc}" for path, desc in extra_paths]
        lines.append("")
    lines.append("按 AGENTS.md 的角色指令完成本步骤，最终回复一个符合 Schema 的 JSON。")
    if repair:
        lines += ["", "上一次的输出没有通过校验，问题如下。请修正后重新输出完整的 JSON（内容里不要提到这次修正）：",
                  *[f"- {p}" for p in repair[:30]]]
    return "\n".join(lines)


def assemble(attempt_dir: Path, package: Package, *, run_label: str, task: str, params: dict, inputs: list[InputFile],
             extra_paths: list[tuple[str, str]], memory: str | None, repair: list[str] | None) -> Assembled:
    """写好尝试目录，返回任务正文和 Schema 路径。extra_paths 是只读给出的绝对路径（例如 data/ 里的资料）。"""
    (attempt_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (attempt_dir / "AGENTS.md").write_text(agents_md(package, run_label, memory), encoding="utf-8")
    (attempt_dir / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    schema_path = attempt_dir / "schema.json"
    schema_path.write_text(json.dumps(output_schema(package.output_specs), ensure_ascii=False, indent=1), encoding="utf-8")
    for item in inputs:
        (attempt_dir / "inputs" / item.name).write_text(item.content, encoding="utf-8")
    prompt = write_prompt(task, params, inputs, extra_paths, repair)
    (attempt_dir / "prompt.md").write_text(prompt + "\n", encoding="utf-8")
    return Assembled(prompt=prompt, schema_path=schema_path)
