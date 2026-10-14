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
from sma.urls import unseen_urls  # noqa: F401  对外沿用 validate.unseen_urls

URL = re.compile(r"^https?://\S+$")


class EvidenceResolver(Protocol):
    def has_library(self, entry_id: str) -> bool: ...
    def has_data(self, relative_path: str) -> bool: ...


@dataclass
class Context:
    """语义规则需要的上下文：步骤参数、同一次输出里的爆款代号等。"""

    params: dict = field(default_factory=dict)
    local_refs: set[str] = field(default_factory=set)
    resolver: EvidenceResolver | None = None


SemanticRule = Callable[[dict, Context], list[str]]
_RULES: dict[str, list[SemanticRule]] = {}


def rule(artifact_type: str):
    def register(fn: SemanticRule) -> SemanticRule:
        _RULES.setdefault(artifact_type, []).append(fn)
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
            problems += [f"[证据] {p}" for p in evidence_errors(collect_refs(content), ctx, where)]
    return problems


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
