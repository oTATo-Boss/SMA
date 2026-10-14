"""包级评测的确定性检查（设计 01 第 7 节、07 第 3 节）。每个检查返回 (是否通过, 说明)。

模型按评分标准打分放到 M2（需要和被测包不同家族的模型）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from sma.urls import normalize_url

URL = re.compile(r"https?://[^\s\"'，。）)]+")
GAP_WORDS = ("假设", "依据不足", "证据不足", "待验证", "缺少数据", "没有数据", "数据不足")


@dataclass
class CaseContext:
    expect: dict
    lib_ids: set[str] = field(default_factory=set)
    input_urls: set[str] = field(default_factory=set)


Check = Callable[[dict, CaseContext], tuple[bool, str]]
CHECKS: dict[str, Check] = {}


def check(name: str):
    def register(fn: Check) -> Check:
        CHECKS[name] = fn
        return fn

    return register


def _candidates(output: dict) -> list[dict]:
    return (output.get("idea_candidates") or {}).get("candidates", [])


def creative_text(candidate: dict) -> str:
    """候选里属于创意内容本身的文字：一句话、钩子、节拍、回报（不含说明来源和差异的字段）。"""
    hook = candidate.get("hook") or {}
    return " ".join([candidate.get("one_liner", ""), hook.get("visual", ""), hook.get("line", ""),
                     *candidate.get("beats", []), candidate.get("payoff", "")])


def _result(failed: list[str], ok: str, bad: str) -> tuple[bool, str]:
    return (False, f"{bad}：{'、'.join(failed)}") if failed else (True, ok)


@check("cites_evidence")
def cites_evidence(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    return _result([c["id"] for c in _candidates(output) if not c["evidence_refs"]], "每个候选都有依据", "没有依据的候选")


@check("distinct_mechanisms")
def distinct_mechanisms(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    seen: dict[str, str] = {}
    repeated = []
    for c in _candidates(output):
        key = re.sub(r"\s+", "", c["borrowed_mechanism"]).lower()
        if key in seen:
            repeated.append(f"{seen[key]}={c['id']}")
        seen.setdefault(key, c["id"])
    return _result(repeated, "候选的机制各不相同", "机制重复")


@check("keeps_core_asset")
def keeps_core_asset(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    terms = ctx.expect.get("core_terms", [])
    missing = [c["id"] for c in _candidates(output) if not all(t in creative_text(c) for t in terms)]
    return _result(missing, f"每个候选都保留了核心资产（{'、'.join(terms)}）", "创意内容里没有核心资产的候选")


@check("no_signature_copy")
def no_signature_copy(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    terms = ctx.expect.get("banned_terms", [])
    hits = [f"{c['id']}:{t}" for c in _candidates(output) for t in terms if t in creative_text(c)]
    return _result(hits, "创意内容里没有照搬对方的标志性设定", "照搬了标志性设定")


@check("refs_within_inputs")
def refs_within_inputs(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    known_urls = {normalize_url(u) for u in ctx.input_urls}
    bad = []
    for c in _candidates(output):
        for ref in c["evidence_refs"]:
            kind, _, value = ref.partition(":")
            if kind == "lib" and value in ctx.lib_ids:
                continue
            if URL.fullmatch(ref) and normalize_url(ref) in known_urls:
                continue
            bad.append(f"{c['id']}:{ref}")
    return _result(bad, "依据都来自输入", "引用了输入里没有的依据")


@check("acknowledges_gap")
def acknowledges_gap(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    missing = [c["id"] for c in _candidates(output) if not any(w in c["why"] for w in GAP_WORDS)]
    return _result(missing, "证据不足时都说明了是假设", "没有说明证据不足的候选")


@check("flags_positioning_change")
def flags_positioning_change(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    flagged = [c["id"] for c in _candidates(output) if "定位" in c["feasibility"] or "定位" in c["why"]]
    return (True, f"注明了换核心资产要先改定位（{'、'.join(flagged)}）") if flagged else (False, "没有任何候选注明换核心资产要先改定位")


@check("media_honest")
def media_honest(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    """M1 的研究只有网页搜索，看不了视频画面和声音：声称看过就是不诚实。"""
    claimed = [r["key"] for r in output.get("viral_references", []) if r["media_reviewed"]]
    return _result(claimed, "没有声称看过画面", "只有网页搜索却声称看过画面的条目")


@check("urls_unique")
def urls_unique(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    urls = [normalize_url(r["url"]) for r in output.get("viral_references", [])]
    repeated = sorted({u for u in urls if urls.count(u) > 1})
    return _result(repeated, f"{len(urls)} 条链接没有重复", "重复的链接")


@check("gaps_reported")
def gaps_reported(output: dict, ctx: CaseContext) -> tuple[bool, str]:
    gaps = (output.get("opportunity_brief") or {}).get("gaps", [])
    return (True, f"写了 {len(gaps)} 个缺口") if gaps else (False, "数据拿不全却没有写缺口")
