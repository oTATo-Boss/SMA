"""测试用的产物构造和假引擎应答。所有数据都是虚构的。"""

from __future__ import annotations

import json
import re

from sma.engines.base import StepRequest

POSITIONING = """# 定位与策略

测试用的虚构账号。

- 角色：橘猫店长阿福，深夜便利店，每集一位带着小烦恼的顾客。
- 受众：晚上刷手机放松的上班族。
- 核心资产：阿福（不能换）。
"""


def viral_ref(key: str, url: str, ratio: float | None = 5.0, platform: str = "抖音") -> dict:
    return {
        "key": key, "platform": platform, "url": url, "author": "示例作者", "title": "示例作品", "published_at": None,
        "viewed_at": "2026-10-15 09:30", "metrics": [{"name": "点赞", "value_text": "1.2万", "value_number": 12000}],
        "baseline": {"description": "近 12 条中位数", "median": 2400, "sample_size": 12}, "outlier_ratio": ratio,
        "media_reviewed": False, "observed": ["标题和点赞数"], "inferred": ["钩子强"], "transferable": ["预期建立 → 反转"],
        "evidence_ids": [],
    }


def research_output(lane: str, shared_url: str = "https://example.com/v/shared") -> dict:
    return {
        "viral_references": [viral_ref("r1", f"https://example.com/v/{lane}-1", 8.0), viral_ref("r2", shared_url + "?from=search", 3.0)],
        "opportunity_brief": {
            "opportunities": [
                {"topic": f"{lane} 机会", "why_now": "近一周 8 倍", "evidence_refs": ["ref:r1"], "audience_signal": "评论要续集",
                 "mechanism_hints": ["预期建立 → 反转"]},
                {"topic": "共同机会", "why_now": "多路都看到", "evidence_refs": ["ref:r2"], "audience_signal": "收藏多", "mechanism_hints": ["升级失控"]},
            ],
            "gaps": [f"{lane} 看不到播放量"],
        },
    }


def candidates_output(method: str, refs: list[str], count: int = 3, core: str = "阿福") -> dict:
    return {"idea_candidates": {"method": method, "candidates": [
        {"id": f"C{i}", "one_liner": f"{core}第 {i} 个点子", "hook": {"visual": f"{core}推开店门", "line": "今晚来了位怪客人"},
         "beats": ["客人进门", f"{core}递上便签", "反转"], "payoff": "温柔的结尾", "why": "近期同类 8 倍",
         "evidence_refs": refs[:1], "borrowed_mechanism": f"机制 {i}", "difference_from_source": "换成本账号的角色",
         "feasibility": "5 个镜头，1 个要生成"}
        for i in range(1, count + 1)]}}


def method_of(label: str) -> str:
    match = re.match(r"ideation\[(.+)\]", label)
    return match.group(1) if match else "original"


def lib_keys(request: StepRequest) -> list[str]:
    path = request.attempt_dir / "inputs" / "merge.viral-reference.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    return [f"lib:{item['key']}" for item in items]


def read_input(request: StepRequest, name: str):
    path = request.attempt_dir / "inputs" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def plan_output(intent: str = "content", lanes=("viral", "adjacent"), methods=("mechanism-borrow", "cross-domain", "original")) -> dict:
    restatement = None
    if intent == "direction-change":
        restatement = {"keep": ["深夜便利店"], "drop": ["猫咪日常"], "add": ["职场题材"], "questions": ["阿福这个角色还保留吗？"]}
    content = intent == "content"
    return {"run_plan": {"intent": intent, "intent_reason": "按原话判断",
                         "research_lanes": [{"id": lane, "focus": f"{lane} 的重点"} for lane in lanes] if content else [],
                         "ideation_methods": list(methods) if content else [], "restatement": restatement, "notes": "说明"}}


DIMS = ["evidence", "hook", "clarity", "payoff", "novelty", "consistency", "feasibility"]


def review_output(request: StepRequest, verdict: str = "pass", pick_index: int = 0, explore: bool = False) -> dict:
    ids = [c["id"] for c in read_input(request, "blind.blinded-candidates.json")["candidates"]]
    scores = []
    for i, cid in enumerate(ids):
        dims = dict.fromkeys(DIMS, 5 if i == pick_index else 3)
        if explore and i == pick_index + 1:
            dims = dict.fromkeys(DIMS, 4)
        scores.append({"candidate": cid, "dimensions": dims, "reasons": dict.fromkeys(DIMS, "理由"), "total": sum(dims.values()),
                       "risks": ["可能没人看"]})
    return {"idea_review": {"blind": True, "scores": scores, "pick": ids[pick_index],
                            "explore_pick": ids[pick_index + 1] if explore else None, "verdict": verdict,
                            "required_changes": [] if verdict == "pass" else ["把钩子改得更具体"], "similarity_flags": [],
                            "summary": f"结论 {verdict}"}}


def director_output() -> dict:
    return {"production_plan": {"idea": "阿福的点子", "experience": {"first_second": "推门", "third_second": "怪客人", "turn": "反转",
                                                                   "payoff": "温柔结尾"},
                                "style": "暖黄低饱和", "assets": [{"name": "阿福", "source": "generate", "ref": None, "notes": "橘猫、圆眼镜"}],
                                "sound": "环境声", "risks": ["手部动作"]},
            "shot_list": {"total_seconds": 12, "shots": [
                {"id": "S1", "seconds": 5, "visual": "阿福推门", "action": "推门", "camera": "固定", "audio": "门铃", "text": "",
                 "assets": ["阿福"], "generation": {"needed": True, "prompt": "橘猫店长推开店门", "reference": None}},
                {"id": "S2", "seconds": 7, "visual": "阿福递便签", "action": "递", "camera": "特写", "audio": "雨声", "text": "今晚来了位怪客人",
                 "assets": ["阿福"], "generation": {"needed": False, "prompt": None, "reference": None}}]}}


def selected_input(request: StepRequest) -> dict | None:
    return read_input(request, "select.selected-idea.json")


def default_responder(request: StepRequest, n: int) -> dict:
    label = request.label
    if label == "plan":
        return plan_output()
    if label.startswith("research["):
        return research_output(label[len("research["):-1])
    if label.startswith("ideation["):
        return candidates_output(method_of(label), lib_keys(request))
    if label == "review":
        return review_output(request)
    if label == "rework":
        selected = selected_input(request)
        revised = candidates_output(selected["method"], selected["candidate"]["evidence_refs"], count=1)
        revised["idea_candidates"]["candidates"][0].update(id=selected["candidate_id"], one_liner="阿福返工后的点子")
        return revised
    if label == "direct":
        return director_output()
    raise AssertionError(f"没想到的步骤：{label}")
