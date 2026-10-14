"""产物 Schema：加载内容 Schema，按包的输出声明拼出交给引擎的总 Schema，检查是否满足严格模式。

引擎（Codex 的 --output-schema）使用严格模式：每个对象必须写 additionalProperties: false，
required 必须列出全部属性，可选字段用"或 null"表示。不满足时 API 直接拒绝，所以这里提前静态检查。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache

from sma.paths import schemas_dir


@dataclass(frozen=True)
class OutputSpec:
    type: str
    many: bool = False


@cache
def content_schema(artifact_type: str) -> dict:
    path = schemas_dir() / "content" / f"{artifact_type}.schema.json"
    if not path.exists():
        raise KeyError(f"没有这种产物的 Schema：{artifact_type}")
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def envelope_schema() -> dict:
    return json.loads((schemas_dir() / "envelope.schema.json").read_text(encoding="utf-8"))


def field_name(artifact_type: str, many: bool) -> str:
    """总 Schema 里每种产物的字段名：opportunity_brief、viral_references。"""
    base = artifact_type.replace("-", "_")
    return base + "s" if many else base


def _strip_meta(schema: dict) -> dict:
    return {k: v for k, v in schema.items() if k not in {"$schema", "$id"}}


def output_schema(outputs: list[OutputSpec]) -> dict:
    """把一个步骤要交的几种产物拼成一个严格模式的对象，引擎只需输出这一个 JSON。"""
    properties = {}
    for spec in outputs:
        schema = _strip_meta(content_schema(spec.type))
        properties[field_name(spec.type, spec.many)] = {"type": "array", "items": schema} if spec.many else schema
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def strict_problems(schema: dict, where: str = "$") -> list[str]:
    """不满足严格模式的地方。"""
    problems: list[str] = []
    types = schema.get("type")
    types = types if isinstance(types, list) else [types]
    if "object" in types:
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False:
            problems.append(f"{where}：对象要写 additionalProperties: false")
        missing = sorted(set(props) - set(schema.get("required", [])))
        if missing:
            problems.append(f"{where}：严格模式下这些字段也要列进 required：{missing}")
        for name, sub in props.items():
            problems += strict_problems(sub, f"{where}.{name}")
    if "array" in types and isinstance(schema.get("items"), dict):
        problems += strict_problems(schema["items"], f"{where}[]")
    for key in ("anyOf", "oneOf", "allOf"):
        for i, sub in enumerate(schema.get(key, [])):
            problems += strict_problems(sub, f"{where}.{key}[{i}]")
    return problems
