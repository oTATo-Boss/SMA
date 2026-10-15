"""四层校验：Schema → 语义规则 → 证据存在 → 哈希绑定（设计 02 第 4 节）。

规则和写法沿用 AI-SMA 的产物校验脚本；这里按产物类型注册语义规则，校验结果只返回错误文字，
由编排层决定是否带着错误修复重试。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from jsonschema import Draft202012Validator

from sma.contracts.schemas import OutputSpec, content_schema, field_name
from sma.hashing import sha256_file
from sma.urls import normalize_url, unseen_urls

URL = re.compile(r"^https?://\S+$")


class EvidenceResolver(Protocol):
    def has_library(self, entry_id: str) -> bool: ...
    def has_data(self, relative_path: str) -> bool: ...
    def has_asset(self, relative_path: str) -> bool: ...


@dataclass
class Context:
    """语义规则需要的上下文：步骤参数、同一次输出里的爆款代号、本步骤拿到的上游产物、研究工具的证据台账。"""

    params: dict = field(default_factory=dict)
    local_refs: set[str] = field(default_factory=set)
    resolver: EvidenceResolver | None = None
    inputs: dict[str, list[dict]] = field(default_factory=dict)     # 类型 → 内容
    ledger: dict[str, dict] | None = None                           # 台账编号 → 记录；没用研究工具时为 None


SemanticRule = Callable[[dict, Context], list[str]]
CrossRule = Callable[[dict[str, list[dict]], Context], list[str]]
_RULES: dict[str, list[SemanticRule]] = {}
_CROSS_RULES: list[tuple[tuple[str, ...], CrossRule]] = []


def rule(artifact_type: str):
    def register(fn: SemanticRule) -> SemanticRule:
        _RULES.setdefault(artifact_type, []).append(fn)
        return fn

    return register


def cross_rule(*artifact_types: str):
    """同一步骤交出的几种产物之间的规则，例如镜头表引用的资产要在制作方案里。"""

    def register(fn: CrossRule) -> CrossRule:
        _CROSS_RULES.append((artifact_types, fn))
        return fn

    return register


def schema_errors(schema: dict, data) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '$'}：{e.message}" for e in errors]


def evidence_errors(refs: list[str], ctx: Context, where: str) -> list[str]:
    problems = []
    for ref in refs:
        if URL.match(ref):
            continue
        kind, _, value = ref.partition(":")
        if kind == "ref":
            if value not in ctx.local_refs:
                problems.append(f"{where}：引用了本次输出里不存在的条目 {ref}")
        elif kind == "lib":
            if ctx.resolver is None or not ctx.resolver.has_library(value):
                problems.append(f"{where}：爆款库里没有 {ref}")
        elif kind == "data":
            if ctx.resolver is None or not ctx.resolver.has_data(value):
                problems.append(f"{where}：工作区里没有这个数据文件 {ref}")
        else:
            problems.append(f"{where}：认不出的依据写法 {ref!r}（只能用 ref:、lib:、data: 或公开链接）")
    return problems


def validate_content(artifact_type: str, content: dict, ctx: Context | None = None) -> list[str]:
    """第 1、2 层：Schema 和语义规则。"""
    ctx = ctx or Context()
    problems = schema_errors(content_schema(artifact_type), content)
    if problems:
        return [f"[Schema] {p}" for p in problems]
    for check in _RULES.get(artifact_type, []):
        problems += [f"[规则] {p}" for p in check(content, ctx)]
    return problems


def split_output(outputs: list[OutputSpec], output: dict) -> dict[str, list[dict]]:
    """把引擎的总输出拆回各类产物：{类型: [内容, …]}。"""
    parts: dict[str, list[dict]] = {}
    for spec in outputs:
        value = output.get(field_name(spec.type, spec.many))
        parts[spec.type] = list(value or []) if spec.many else ([value] if value is not None else [])
    return parts


def validate_output(outputs: list[OutputSpec], output: dict, ctx: Context) -> list[str]:
    """校验一个步骤的总输出：拆分后逐个做 Schema、语义规则和证据检查。"""
    from sma.contracts.schemas import output_schema

    problems = schema_errors(output_schema(outputs), output)
    if problems:
        return [f"[Schema] {p}" for p in problems]
    parts = split_output(outputs, output)
    keys = [ref["key"] for ref in parts.get("viral-reference", [])]
    ctx.local_refs = set(keys)
    repeated = sorted({k for k in keys if keys.count(k) > 1})
    if repeated:
        problems.append(f"[规则] viral-reference 的 key 重复：{repeated}；每条用不同的代号")
    for artifact_type, items in parts.items():
        for i, content in enumerate(items):
            where = f"{artifact_type}[{i}]"
            problems += [f"{where} {p}" for p in validate_content(artifact_type, content, ctx)]
            for location, ref in located_refs(content, where):
                problems += [f"[证据] {p}" for p in evidence_errors([ref], ctx, location)]
    for types, check in _CROSS_RULES:
        if all(parts.get(t) for t in types):
            problems += [f"[规则] {p}" for p in check(parts, ctx)]
    return list(dict.fromkeys(problems))      # 同一个问题只报一次


def located_refs(content, path: str) -> list[tuple[str, str]]:
    """内容里所有 evidence_refs 的值和它们的位置，例如 ("idea-candidates[0].candidates[2]", "lib:…")。"""
    found: list[tuple[str, str]] = []
    if isinstance(content, dict):
        for key, value in content.items():
            if key == "evidence_refs" and isinstance(value, list):
                found += [(path, v) for v in value if isinstance(v, str)]
            else:
                found += located_refs(value, f"{path}.{key}")
    elif isinstance(content, list):
        for i, item in enumerate(content):
            found += located_refs(item, f"{path}[{i}]")
    return found


def collect_refs(content) -> list[str]:
    """内容里所有 evidence_refs 字段的值（任意深度）。"""
    found: list[str] = []
    if isinstance(content, dict):
        for key, value in content.items():
            if key == "evidence_refs" and isinstance(value, list):
                found += [v for v in value if isinstance(v, str)]
            else:
                found += collect_refs(value)
    elif isinstance(content, list):
        for item in content:
            found += collect_refs(item)
    return found


def collect_urls(content) -> list[str]:
    """内容里引用的公开链接：url 字段和 evidence_refs 里的链接（任意深度）。"""
    found: list[str] = []
    if isinstance(content, dict):
        for key, value in content.items():
            if key == "url" and isinstance(value, str) and URL.match(value):
                found.append(value)
            elif key == "evidence_refs" and isinstance(value, list):
                found += [v for v in value if isinstance(v, str) and URL.match(v)]
            else:
                found += collect_urls(value)
    elif isinstance(content, list):
        for item in content:
            found += collect_urls(item)
    return list(dict.fromkeys(found))


def hash_error(path: Path, expected: str) -> str | None:
    """第 4 层：文件还是不是当初绑定的那个版本。"""
    if not path.is_file():
        return f"文件不存在：{path}"
    if sha256_file(path) != expected:
        return f"文件在绑定之后被改过：{path}"
    return None


# ---- 语义规则（第 2 层） ----


@rule("viral-reference")
def _viral_reference(content: dict, ctx: Context) -> list[str]:
    problems = []
    if not URL.match(content["url"]):
        problems.append(f"url 不是公开链接：{content['url']!r}")
    ratio = content["outlier_ratio"]
    if ratio is not None and ratio < 0:
        problems.append("outlier_ratio 不能是负数")
    if not content["observed"]:
        problems.append("observed 至少写一条看到的事实")
    return problems


@rule("opportunity-brief")
def _opportunity_brief(content: dict, ctx: Context) -> list[str]:
    problems = []
    count = len(content["opportunities"])
    if not 1 <= count <= 5:
        problems.append(f"机会要有 1 到 5 个，现在是 {count} 个")
    if not content["gaps"]:
        for i, item in enumerate(content["opportunities"]):
            if not item["evidence_refs"]:
                problems.append(f"第 {i + 1} 个机会没有依据；证据不够时要写进 gaps")
    return problems


@rule("idea-candidates")
def _idea_candidates(content: dict, ctx: Context) -> list[str]:
    problems = []
    low, high = ctx.params.get("candidates_range", (3, 4))
    count = len(content["candidates"])
    if not low <= count <= high:
        problems.append(f"候选要有 {low} 到 {high} 个，现在是 {count} 个")
    ids = [c["id"] for c in content["candidates"]]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        problems.append(f"候选代号重复：{duplicated}")
    method = ctx.params.get("method")
    if method and content["method"] != method:
        problems.append(f"method 应该是本步骤指定的 {method!r}，现在是 {content['method']!r}")
    for c in content["candidates"]:
        if len(c["beats"]) < 2:
            problems.append(f"候选 {c['id']} 的节拍太少，至少 2 拍")
    return problems


@rule("viral-reference")
def _viral_reference_ledger(content: dict, ctx: Context) -> list[str]:
    """用了研究工具时（有台账），条目的链接、指标、看没看过画面都要和台账对得上。"""
    if ctx.ledger is None:
        return []
    ids = content["evidence_ids"]
    if not ids:
        return ["用了研究工具时，每条爆款都要写台账编号 evidence_ids"]
    problems = [f"台账里没有编号 {i}" for i in ids if i not in ctx.ledger]
    records = [ctx.ledger[i] for i in ids if i in ctx.ledger]
    if not records:
        return problems
    seen = "\n".join(str(r.get("url", "")) + "\n" + str(r.get("data", "")) for r in records)
    if unseen_urls([content["url"]], seen):
        problems.append(f"台账 {ids} 里找不到这条作品的链接 {content['url']}")
    captured: dict[str, float] = {}
    for record in records:
        captured.update({k: v for k, v in (record.get("metrics") or {}).items() if isinstance(v, (int, float))})
    for metric in content["metrics"]:
        value, recorded = metric["value_number"], captured.get(metric["name"])
        if value is not None and recorded is not None and abs(value - recorded) > max(1.0, abs(recorded) * 0.01):
            problems.append(f"指标「{metric['name']}」写的是 {value}，台账里是 {recorded}；只写台账里看到的数")
    if content["media_reviewed"]:
        target = normalize_url(content["url"])
        played = [r for r in ctx.ledger.values() if r.get("tool") == "playback_frames" and r.get("frames")
                  and (normalize_url(str(r.get("url", ""))) == target or not unseen_urls([content["url"]], str(r.get("url", ""))))]
        if not played:
            problems.append("media_reviewed 写了 true，但台账里没有这条作品的播放截图；没看过就写 false")
    return problems


@rule("run-plan")
def _run_plan(content: dict, ctx: Context) -> list[str]:
    if content["intent"] == "direction-change":
        restatement = content["restatement"]
        return [] if restatement and restatement["questions"] else ["intent 是 direction-change 时要写 restatement，questions 至少一条"]
    if content["intent"] != "content":
        return []
    problems = []
    for field_name_, spec in (ctx.params.get("choices") or {}).items():
        allowed = [v["id"] for v in spec["values"]]
        picked = [x["id"] if isinstance(x, dict) else x for x in content.get(field_name_, [])]
        low, high = spec.get("min", 1), spec.get("max", len(allowed))
        if not low <= len(picked) <= high:
            problems.append(f"{field_name_} 要选 {low} 到 {high} 个，现在是 {len(picked)} 个")
        unknown = [x for x in picked if x not in allowed]
        if unknown:
            problems.append(f"{field_name_} 里有不在可选范围的 {unknown}；只能从 {allowed} 里选")
        if len(set(picked)) != len(picked):
            problems.append(f"{field_name_} 有重复")
    return problems


THRESHOLDS = {"pass_total": 25, "min_dimension": 3, "min_consistency": 4}


@rule("idea-review")
def _idea_review(content: dict, ctx: Context) -> list[str]:
    problems = []
    if content["blind"] is not True:
        problems.append("blind 要写 true：评审按盲评做")
    scores = {s["candidate"]: s for s in content["scores"]}
    if len(scores) != len(content["scores"]):
        problems.append("同一个候选打了不止一次分")
    given = [c["id"] for batch in ctx.inputs.get("blinded-candidates", []) for c in batch["candidates"]]
    if given:
        missing, unknown = sorted(set(given) - set(scores)), sorted(set(scores) - set(given))
        if missing:
            problems.append(f"这些候选没有打分：{missing}")
        if unknown:
            problems.append(f"打分里有输入里没有的候选：{unknown}")
    for cid, score in scores.items():
        if sum(score["dimensions"].values()) != score["total"]:
            problems.append(f"{cid} 的 total 应该是 7 项之和 {sum(score['dimensions'].values())}，现在写的是 {score['total']}")
    pick, explore = content["pick"], content["explore_pick"]
    if pick not in scores:
        problems.append(f"pick {pick!r} 不是打过分的候选")
    if explore is not None and (explore not in scores or explore == pick):
        problems.append(f"explore_pick {explore!r} 要是另一个打过分的候选，没有就写 null")
    flagged = {f["candidate"] for f in content["similarity_flags"]}
    unknown_flags = sorted(flagged - set(scores))
    if unknown_flags:
        problems.append(f"similarity_flags 里有输入里没有的候选：{unknown_flags}")
    t = {**THRESHOLDS, **(ctx.params.get("thresholds") or {})}

    def passes(cid: str) -> bool:
        score = scores.get(cid)
        return bool(score) and score["total"] >= t["pass_total"] and min(score["dimensions"].values()) >= t["min_dimension"] \
            and score["dimensions"]["consistency"] >= t["min_consistency"] and cid not in flagged

    rule_text = f"总分不低于 {t['pass_total']}、每项不低于 {t['min_dimension']}、一致不低于 {t['min_consistency']}、没有相似度问题"
    if content["verdict"] == "pass" and pick in scores and not passes(pick):
        problems.append(f"verdict 是 pass，但 {pick} 没过线（{rule_text}）；没过线就写 revise 或 reject")
    if content["verdict"] == "pass" and explore is not None and explore in scores and not passes(explore):
        problems.append(f"explore_pick {explore} 也要过线（{rule_text}），不过线就写 null")
    if content["verdict"] in {"revise", "reject"} and not content["required_changes"]:
        problems.append(f"verdict 是 {content['verdict']} 时要写 required_changes")
    return problems


@rule("shot-list")
def _shot_list(content: dict, ctx: Context) -> list[str]:
    problems = []
    ids = [shot["id"] for shot in content["shots"]]
    if not ids:
        problems.append("镜头表是空的")
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        problems.append(f"镜头代号重复：{repeated}")
    total = sum(shot["seconds"] for shot in content["shots"])
    if abs(total - content["total_seconds"]) > 0.5:
        problems.append(f"各镜头时长加起来是 {total:g} 秒，total_seconds 写的是 {content['total_seconds']:g} 秒")
    for shot in content["shots"]:
        if shot["seconds"] <= 0:
            problems.append(f"{shot['id']} 的时长要大于 0")
        if shot["generation"]["needed"] and not shot["generation"]["prompt"]:
            problems.append(f"{shot['id']} 要生成，但没有写提示词")
        reference = shot["generation"]["reference"]
        if reference and (ctx.resolver is None or not ctx.resolver.has_asset(reference)):
            problems.append(f"{shot['id']} 的参考图在固定资产里不存在：{reference}")
    return problems


@rule("production-plan")
def _production_plan(content: dict, ctx: Context) -> list[str]:
    problems = []
    names = [a["name"] for a in content["assets"]]
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        problems.append(f"资产名重复：{repeated}")
    for asset in content["assets"]:
        if asset["source"] == "fixed":
            if not asset["ref"]:
                problems.append(f"固定资产 {asset['name']} 要写 ref（固定资产/ 下的相对路径）")
            elif ctx.resolver is None or not ctx.resolver.has_asset(asset["ref"]):
                problems.append(f"固定资产里没有 {asset['ref']}（{asset['name']}）")
    return problems


@cross_rule("production-plan", "shot-list")
def _plan_and_shots(parts: dict[str, list[dict]], ctx: Context) -> list[str]:
    names = {a["name"] for a in parts["production-plan"][0]["assets"]}
    used = {name for shot in parts["shot-list"][0]["shots"] for name in shot["assets"]}
    unknown = sorted(used - names)
    return [f"镜头表用到了制作方案里没有的资产：{unknown}"] if unknown else []
