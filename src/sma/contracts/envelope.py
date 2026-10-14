"""产物信封：元数据由编排层填写，模型只填 content（设计 02 第 2 节）。"""

from __future__ import annotations

from dataclasses import dataclass

from sma.contracts.validate import collect_refs
from sma.timeutil import stamp

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Producer:
    agent: str
    version: str
    engine: str
    model: str | None


def artifact_id(run_id: str, step_id: str, artifact_type: str, index: int | None, version: int) -> str:
    suffix = f"-{index}" if index is not None else ""
    return f"{run_id}/{step_id}/{artifact_type}{suffix}.v{version}"


def make_envelope(artifact_type: str, content: dict, *, run_id: str, step_id: str, index: int | None, version: int,
                  producer: Producer, inputs: list[dict], slug: str | None = None) -> dict:
    """slug 是步骤在文件系统和 id 里的写法（research[viral] → research-viral）。"""
    return {
        "artifact": artifact_type,
        "schema_version": SCHEMA_VERSION,
        "id": artifact_id(run_id, slug or step_id, artifact_type, index, version),
        "run_id": run_id,
        "step_id": step_id,
        "producer": {"agent": producer.agent, "version": producer.version, "engine": producer.engine, "model": producer.model},
        "created_at": stamp(),
        "inputs": inputs,
        "evidence_refs": sorted(set(collect_refs(content))),
        "content": content,
    }
