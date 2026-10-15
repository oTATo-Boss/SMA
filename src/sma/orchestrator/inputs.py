"""按流程定义收集一个步骤的输入（设计 03 第 7 节）。

上游产物读取前先核对哈希（第 4 层校验）；工作区文件复制一份快照进 inputs/，data/ 里的资料只给只读路径。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sma.contracts.validate import hash_error
from sma.hashing import sha256_file
from sma.orchestrator.context import InputFile
from sma.orchestrator.errors import StepError
from sma.orchestrator.workflow import InputSpec, Instance, Workflow
from sma.workspace.workspace import Workspace

STRATEGY_FILES = [("strategy/positioning.md", "定位与策略"), ("strategy/content-rules.md", "内容与交付规则"), ("strategy/facts.md", "账号事实")]
TYPE_LABELS = {"opportunity-brief": "机会简报", "viral-reference": "爆款条目", "idea-candidates": "创意候选"}
DATA_LIMIT = 100
HASH_LIMIT = 50 << 20


@dataclass
class Loaded:
    id: str
    step_id: str
    type: str
    content: dict
    sha256: str


@dataclass
class StepInputs:
    files: list[InputFile] = field(default_factory=list)
    paths: list[tuple[str, str]] = field(default_factory=list)
    refs: list[dict] = field(default_factory=list)                 # 写进信封的 inputs
    artifacts: dict[str, list[Loaded]] = field(default_factory=dict)


def load_artifacts(ws: Workspace, run_id: str, step_ids: list[str], artifact_type: str) -> list[Loaded]:
    found = []
    for step_id in step_ids:
        for record in ws.db.artifacts(run_id, step_id, artifact_type):
            path = ws.root / record["path"]
            problem = hash_error(path, record["sha256"])
            if problem:
                raise StepError(f"上游产物 {record['id']} 校验没过：{problem}")
            envelope = json.loads(path.read_text(encoding="utf-8"))
            found.append(Loaded(record["id"], step_id, artifact_type, envelope["content"], record["sha256"]))
    return found


def _select(items: list[Loaded], spec: InputSpec) -> list[Loaded]:
    if spec.select == "top_by_outlier":
        items = sorted(items, key=lambda a: (a.content.get("outlier_ratio") is None, -(a.content.get("outlier_ratio") or 0)))
    return items[: spec.limit] if spec.limit else items


def describe_input(artifact_type: str, source: str, items: list[dict], ordered: bool = False) -> str:
    """inputs/ 里一个文件的一句话说明。"""
    label = TYPE_LABELS.get(artifact_type, artifact_type)
    text = f"{label}（来自 {source}，" + ("1 份）" if len(items) == 1 else f"共 {len(items)} 份，是一个列表）")
    if artifact_type == "viral-reference" and items and all(Workspace.is_library_id(i.get("key", "")) for i in items):
        text += "；key 就是爆款库 id，引用时写 lib:<key>"
    if ordered:
        text += "；按 outlier_ratio 从高到低"
    return text


def _json_file(name: str, description: str, data) -> InputFile:
    return InputFile(name, description, json.dumps(data, ensure_ascii=False, indent=1))


def recent_runs(ws: Workspace, current: str, limit: int = 6) -> list[dict]:
    """最近几次运行的任务和选中的方案（规划用来避免重复）。"""
    out = []
    for run in ws.db.list_runs(limit + 1):
        if run["id"] == current:
            continue
        pick = None
        for record in ws.db.artifacts(run["id"], artifact_type="selected-idea"):
            content = json.loads((ws.root / record["path"]).read_text(encoding="utf-8"))["content"]
            pick = {"one_liner": content["candidate"]["one_liner"], "method": content["method"]}
        out.append({"run": run["id"], "at": run["created_at"], "task": run["task"], "status": run["status"], "pick": pick})
    return out[:limit]


def _workspace(ws: Workspace, spec: InputSpec, collected: StepInputs, run_id: str = "") -> None:
    if spec.type == "strategy":
        found = [(rel, label) for rel, label in STRATEGY_FILES if (ws.root / rel).is_file()]
        if spec.required and not (ws.root / STRATEGY_FILES[0][0]).is_file():
            raise StepError(f"缺少定位文件 {STRATEGY_FILES[0][0]}")
        for rel, label in found:
            path = ws.root / rel
            collected.files.append(InputFile("strategy-" + path.name, label, path.read_text(encoding="utf-8"),
                                             [{"ref": f"workspace:{rel}", "sha256": sha256_file(path)}]))
    elif spec.type == "benchmarks":
        path = ws.root / "library" / "benchmarks.json"
        if path.is_file():
            collected.files.append(InputFile("benchmarks.json", "对标账号清单", path.read_text(encoding="utf-8"),
                                             [{"ref": "workspace:library/benchmarks.json", "sha256": sha256_file(path)}]))
        elif spec.required:
            raise StepError("缺少对标库 library/benchmarks.json")
    elif spec.type == "recent-runs":
        collected.files.append(_json_file("recent-runs.json", "最近几次运行的任务和选中的方案", recent_runs(ws, run_id)))
    elif spec.type == "account":
        config = ws.config
        collected.files.append(_json_file("account.json", "账号的平台、启用的制作路线、交付规格",
                                          {"name": config.account.name, "platforms": config.account.platforms,
                                           "routes": config.routes, "deliverables": config.deliverables}))
    elif spec.type == "assets":
        base = ws.root / "固定资产"
        files = sorted(p for p in base.rglob("*") if p.is_file() and not p.name.startswith(".")) if base.is_dir() else []
        listing = [{"path": p.relative_to(base).as_posix(), "bytes": p.stat().st_size} for p in files[:200]]
        collected.files.append(_json_file("assets.json", "固定资产清单（固定资产/ 下的相对路径）", listing))
    elif spec.type == "data-files":
        data = ws.root / "data"
        files = sorted(p for p in data.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.relative_to(data).parts))
        if spec.required and not files:
            raise StepError("data/ 里没有本人提供的资料")
        for path in files[:DATA_LIMIT]:
            rel = path.relative_to(data).as_posix()
            collected.paths.append((str(path), f"引用时写 data:{rel}"))
            if path.stat().st_size <= HASH_LIMIT:
                collected.refs.append({"ref": f"workspace:data/{rel}", "sha256": sha256_file(path)})


def gather(ws: Workspace, run_id: str, workflow: Workflow, instance: Instance, sources: dict[str, list[str]],
           all_ids: dict[str, list[str]] | None = None) -> StepInputs:
    """sources：每个上游基础步骤已成功的实例 id；all_ids：每个步骤当前的全部实例 id（feedback 输入用）。"""
    collected = StepInputs()
    for spec in workflow.steps[instance.step].inputs:
        if spec.source == "workspace":
            _workspace(ws, spec, collected, run_id)
            continue
        ids = (all_ids or {}).get(spec.source, []) if spec.feedback else sources.get(spec.source, [])
        items = _select(load_artifacts(ws, run_id, ids, spec.type), spec)
        if not items:
            if spec.required:
                raise StepError(f"缺少必需的输入：{spec.source} 的 {spec.type}")
            continue
        collected.artifacts.setdefault(spec.type, []).extend(items)
        body = items[0].content if len(items) == 1 else [a.content for a in items]
        description = describe_input(spec.type, spec.source, [a.content for a in items], spec.select == "top_by_outlier")
        collected.files.append(InputFile(f"{spec.source}.{spec.type}.json", description, json.dumps(body, ensure_ascii=False, indent=1)))
        collected.refs += [{"ref": a.id, "sha256": a.sha256} for a in items]
    collected.refs = [r for f in collected.files for r in f.refs] + collected.refs
    return collected
