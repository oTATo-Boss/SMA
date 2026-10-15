"""代码步骤：确定性的处理由代码完成，不交给模型（设计 03 第 9 节）。

每个函数收到 CodeContext，返回 {产物类型: [内容, …]}；编排层负责校验、存档和登记。
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from dataclasses import dataclass, field
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
    run_state: dict = field(default_factory=dict)

    def one(self, artifact_type: str) -> dict:
        items = self.artifacts.get(artifact_type, [])
        if not items:
            raise ValueError(f"缺少输入 {artifact_type}")
        return items[-1].content

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


@code_step("blind_candidates")
def blind_candidates(ctx: CodeContext) -> dict[str, list[dict]]:
    """合并各路候选，代号打乱重编成 X1、X2……（用运行 id 做种子，重跑时编号不变）；有返工结果时替换被返工的那一个。"""
    rework_step = ctx.instance.params.get("rework_step", "rework")
    items = ctx.artifacts.get("idea-candidates", [])
    lanes = sorted((a for a in items if a.step_id != rework_step), key=lambda a: a.step_id)
    entries = [{"step": a.step_id, "method": a.content["method"], "candidate": c, "artifact": a.id}
               for a in lanes for c in a.content["candidates"]]
    random.Random(f"{ctx.run_id}:blind").shuffle(entries)
    for i, entry in enumerate(entries, 1):
        entry["blind_id"] = f"X{i}"
    target = ctx.run_state.get("rework_target")
    reworked = [a for a in items if a.step_id == rework_step]
    if target and reworked:
        for entry in entries:
            if entry["blind_id"] == target:
                entry.update(candidate=reworked[-1].content["candidates"][0], step=rework_step, artifact=reworked[-1].id)
    return {
        "blinded-candidates": [{"candidates": [{**e["candidate"], "id": e["blind_id"]} for e in entries]}],
        "blind-key": [{"entries": [{"blind_id": e["blind_id"], "step": e["step"], "method": e["method"],
                                    "candidate": e["candidate"]["id"], "artifact": e["artifact"]} for e in entries]}],
    }


@code_step("select_idea")
def select_idea(ctx: CodeContext) -> dict[str, list[dict]]:
    """按评审结论选出要做的方案并解开盲化；评审给了探索名额时，按 autonomy.explore_ratio 的比例改做它。"""
    review, blinded, key = ctx.one("idea-review"), ctx.one("blinded-candidates"), ctx.one("blind-key")
    pick, explore = review["pick"], False
    if review["verdict"] == "pass" and review["explore_pick"]:
        roll = int(hashlib.sha256(f"{ctx.run_id}:explore".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if roll < ctx.workspace.config.autonomy.explore_ratio:
            pick, explore = review["explore_pick"], True
    entry = next(e for e in key["entries"] if e["blind_id"] == pick)
    candidate = next(c for c in blinded["candidates"] if c["id"] == pick)
    score = next(s for s in review["scores"] if s["candidate"] == pick)
    rounds = ctx.run_state.get("rounds", {}).get(ctx.instance.step, 0) + 1
    return {"selected-idea": [{
        "blind_id": pick, "step": entry["step"], "method": entry["method"], "candidate_id": entry["candidate"], "explore": explore,
        "candidate": {**candidate, "id": entry["candidate"]},
        "review": {"verdict": review["verdict"], "total": score["total"], "dimensions": score["dimensions"], "risks": score["risks"],
                   "required_changes": review["required_changes"], "summary": review["summary"]},
        "rounds": rounds,
    }]}


FAMILY = {"codex": "openai", "claude": "anthropic"}


@code_step("write_rationale")
def write_rationale(ctx: CodeContext) -> dict[str, list[dict]]:
    """依据卡：选中的创意、市场证据、评审、制作方案摘要、用到的模型；另外写一份给人读的 依据卡.md。"""
    selected, review = ctx.one("selected-idea"), ctx.one("idea-review")
    plan, shots = ctx.one("production-plan"), ctx.one("shot-list")
    run = ctx.workspace.db.get_run(ctx.run_id)
    cited = list(dict.fromkeys(selected["candidate"]["evidence_refs"]))
    refs = {f"lib:{a.content['key']}": a.content for a in ctx.artifacts.get("viral-reference", [])}
    evidence = []
    for ref in cited:
        known = refs.get(ref)
        evidence.append({"ref": ref, "url": known["url"] if known else (ref if ref.startswith("http") else None),
                         "outlier_ratio": known["outlier_ratio"] if known else None,
                         "evidence_ids": known.get("evidence_ids", []) if known else []})
    rows = [r for r in ctx.workspace.db.get_steps(ctx.run_id) if r["kind"] == "agent" and r["status"] == "succeeded"]
    models = [{"step": r["step_id"], "agent": r["agent"] or "", "engine": r["engine"] or "", "model": r["model"]} for r in rows]
    critic = {FAMILY.get(r["engine"]) for r in rows if r["agent"] == "creative-critic"} - {None}
    makers = {FAMILY.get(r["engine"]) for r in rows if r["agent"] == "creative-director"} - {None}
    independence = "unknown" if not critic or not makers else ("different-family" if not critic & makers else "same-family")
    notes = []
    if independence == "same-family":
        notes.append("评审和创意用了同一家族的模型（同家族评审），独立性打折扣")
    if selected["explore"]:
        notes.append("这次做的是评审给的探索名额，不是最高分方案")
    content = {
        "task": run["task"],
        "pick": {"one_liner": selected["candidate"]["one_liner"], "method": selected["method"], "explore": selected["explore"],
                 "step": selected["step"]},
        "review": {"total": selected["review"]["total"], "rounds": selected["rounds"], "risks": selected["review"]["risks"],
                   "summary": review["summary"]},
        "evidence": evidence, "models": models, "independence": independence, "notes": notes,
    }
    card = render_card(ctx.run_id, content, selected, plan, shots)
    (ctx.workspace.run_dir(ctx.run_id) / "依据卡.md").write_text(card, encoding="utf-8")
    return {"delivery-rationale": [content]}


def render_card(run_id: str, content: dict, selected: dict, plan: dict, shots: dict) -> str:
    c = selected["candidate"]
    generated = sum(1 for s in shots["shots"] if s["generation"]["needed"])
    lines = [f"# 依据卡 · {run_id}", "", f"任务：{content['task']}", "", "## 选中的创意", "",
             f"**{c['one_liner']}**（{selected['method']}{'，探索名额' if selected['explore'] else ''}）", "",
             f"- 前 3 秒：{c['hook']['visual']}｜{c['hook']['line']}",
             f"- 节拍：{' → '.join(c['beats'])}", f"- 结尾：{c['payoff']}", f"- 借的机制：{c['borrowed_mechanism']}",
             f"- 和原作的差别：{c['difference_from_source']}", "", "## 为什么做这个", "", c["why"], "",
             f"评审：{content['review']['total']} 分，第 {content['review']['rounds']} 轮通过。{content['review']['summary']}"]
    if content["review"]["risks"]:
        lines += ["", "风险：", *[f"- {r}" for r in content["review"]["risks"]]]
    lines += ["", "## 市场证据", ""]
    if content["evidence"]:
        lines += ["| 依据 | 链接 | 倍数 | 台账 |", "|---|---|---|---|"]
        for e in content["evidence"]:
            ratio = "—" if e["outlier_ratio"] is None else f"{e['outlier_ratio']:.1f}"
            lines.append(f"| {e['ref']} | {e['url'] or '—'} | {ratio} | {'、'.join(e['evidence_ids']) or '—'} |")
    else:
        lines.append("没有引用市场证据：这条创意属于假设，发布后要看数据验证。")
    lines += ["", "## 制作方案", "", f"{plan['idea']}", "",
              f"总时长 {shots['total_seconds']:g} 秒，{len(shots['shots'])} 个镜头，其中 {generated} 个要生成。",
              f"第 1 秒：{plan['experience']['first_second']}；第 3 秒：{plan['experience']['third_second']}；"
              f"转折：{plan['experience']['turn']}；回报：{plan['experience']['payoff']}"]
    if plan["risks"]:
        lines += ["", "制作风险：", *[f"- {r}" for r in plan["risks"]]]
    lines += ["", "## 用到的模型", "", "| 步骤 | 包 | 引擎 | 型号 |", "|---|---|---|---|"]
    lines += [f"| {m['step']} | {m['agent']} | {m['engine']} | {m['model'] or '默认'} |" for m in content["models"]]
    independence = {"different-family": "评审和创意用了不同家族的模型", "same-family": "评审和创意用了同一家族的模型（同家族评审）",
                    "unknown": "评审和创意的模型家族不明"}[content["independence"]]
    lines += ["", independence + "。", *[f"- {n}" for n in content["notes"]], ""]
    return "\n".join(lines)
