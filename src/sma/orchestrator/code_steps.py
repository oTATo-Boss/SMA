"""代码步骤：确定性的处理由代码完成，不交给模型（设计 03 第 9 节）。

每个函数收到 CodeContext，返回 {产物类型: [内容, …]}；编排层负责校验、存档和登记。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import zip_longest

from sma.orchestrator.inputs import Loaded
from sma.orchestrator.workflow import Instance
from sma.workspace.workspace import Workspace

MAX_OPPORTUNITIES = 5


@dataclass
class CodeContext:
    workspace: Workspace
    run_id: str
    instance: Instance
    artifacts: dict[str, list[Loaded]]

    def by_step(self, artifact_type: str) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for item in self.artifacts.get(artifact_type, []):
            grouped.setdefault(item.step_id, []).append(item.content)
        return grouped


CodeStep = Callable[[CodeContext], dict[str, list[dict]]]
CODE_STEPS: dict[str, CodeStep] = {}


def code_step(name: str):
    def register(fn: CodeStep) -> CodeStep:
        CODE_STEPS[name] = fn
        return fn

    return register


def _rewrite_refs(refs: list[str], mapping: dict[str, str]) -> list[str]:
    out = []
    for ref in refs:
        kind, _, value = ref.partition(":")
        new = f"lib:{mapping[value]}" if kind == "ref" and value in mapping else ref
        if new not in out:
            out.append(new)
    return out


@code_step("merge_research")
def merge_research(ctx: CodeContext) -> dict[str, list[dict]]:
    """合并各路调研：爆款条目按链接去重收进爆款库，简报里的 ref: 换成 lib:，机会轮流取、按主题去重，最多 5 个。"""
    references = ctx.by_step("viral-reference")
    briefs = ctx.by_step("opportunity-brief")
    merged_refs: dict[str, dict] = {}
    lanes: list[list[dict]] = []
    gaps: list[str] = []
    for step_id in sorted(set(references) | set(briefs)):
        refs = references.get(step_id, [])
        mapping = ctx.workspace.add_library_entries(refs, source=f"{ctx.run_id}/{step_id}") if refs else {}
        for ref in refs:
            merged_refs.setdefault(mapping[ref["key"]], {**ref, "key": mapping[ref["key"]]})
        for brief in briefs.get(step_id, []):
            lanes.append([{**o, "evidence_refs": _rewrite_refs(o["evidence_refs"], mapping)} for o in brief["opportunities"]])
            gaps += [g for g in brief["gaps"] if g not in gaps]
    opportunities, topics = [], set()
    for row in zip_longest(*lanes):
        for item in row:
            if item is None or item["topic"].strip().lower() in topics:
                continue
            topics.add(item["topic"].strip().lower())
            opportunities.append(item)
    return {
        "opportunity-brief": [{"opportunities": opportunities[:MAX_OPPORTUNITIES], "gaps": gaps}],
        "viral-reference": list(merged_refs.values()),
    }
