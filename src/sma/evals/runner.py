"""包级评测（设计 07 第 3 节）：用包的 evals/cases.jsonl 和 fixtures 调真实引擎，
经过和正式运行相同的尝试循环（四层校验、修复重试），再做确定性检查；结果写进用户级的评测库。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from sma.engines.base import Engine
from sma.engines.routing import resolve
from sma.evals.checks import CHECKS, URL, CaseContext
from sma.hashing import sha256_text
from sma.orchestrator.attempts import StepJob, rule_params, run_attempts
from sma.orchestrator.context import InputFile
from sma.orchestrator.inputs import describe_input
from sma.packages.loader import Package, load_package
from sma.paths import user_config_dir
from sma.state.db import StateDB
from sma.timeutil import compact
from sma.tracing.redact import secret_values
from sma.userconfig import load_user_models


class FixtureResolver:
    """评测里的证据只能来自用例的输入：爆款条目的 key 当作爆款库 id。"""

    def __init__(self, lib_ids: set[str]):
        self.lib_ids = lib_ids

    def has_library(self, entry_id: str) -> bool:
        return entry_id in self.lib_ids

    def has_data(self, relative_path: str) -> bool:
        return False


def eval_db() -> StateDB:
    return StateDB(user_config_dir() / "evals.db")


def load_cases(package: Package) -> list[dict]:
    path = package.root / "evals" / "cases.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def case_inputs(package: Package, case: dict) -> tuple[list[InputFile], set[str], set[str]]:
    """返回 (输入文件, 可引用的爆款库 id, 输入里出现过的链接)。"""
    files: list[InputFile] = []
    lib_ids: set[str] = set()
    urls: set[str] = set()
    for kind, rel in case.get("inputs", {}).items():
        for item in rel if isinstance(rel, list) else [rel]:
            text = (package.root / "evals" / item).read_text(encoding="utf-8")
            refs = [{"ref": f"fixture:{package.id}/{item}", "sha256": sha256_text(text)}]
            if kind == "strategy":
                files.append(InputFile("strategy-positioning.md", "定位与策略", text, refs))
                continue
            data = json.loads(text)
            items = data if isinstance(data, list) else [data]
            if kind == "viral-reference":
                lib_ids |= {i["key"] for i in items}
            urls |= set(URL.findall(text))
            files.append(InputFile(f"fixture.{kind}.json", describe_input(kind, "评测用例", items), text, refs))
    return files, lib_ids, urls


def run_case(package: Package, case: dict, *, engine: Engine, route, batch: str, cancel: threading.Event, secrets: dict,
             retry_delay_s: float, on_event: Callable[[dict], None]) -> dict | None:
    if cancel.is_set():                 # 已经停止：没开始的用例不跑、不记录
        return None
    files, lib_ids, urls = case_inputs(package, case)
    params = case.get("params", {})
    job = StepJob(label=case["id"], package=package, route=route, engine=engine, step_dir=user_config_dir() / "evals" / batch / case["id"],
                  first_n=1, task=case.get("task", "今天做一条视频"), params=params, rule_params=rule_params(params), inputs=files,
                  extra_paths=[], memory=None, resolver=FixtureResolver(lib_ids), run_label=f"评测 {batch} · {case['id']}")
    on_event({"kind": "case-start", "case": case["id"], "note": case.get("note", "")})
    result = run_attempts(job, cancel=cancel, secrets=secrets, retry_delay_s=retry_delay_s,
                          on_end=lambda log: on_event({"kind": "attempt-end", "case": case["id"], "attempt": log.n, "status": log.status,
                                                       "problems": log.problems, "detail": log.detail}))
    checks: dict[str, list] = {}
    if result.status == "succeeded":
        ctx = CaseContext(case.get("expect", {}), lib_ids, urls)
        for name in case.get("expect", {}).get("checks", []):
            fn = CHECKS.get(name)
            checks[name] = list(fn(result.output, ctx)) if fn else [False, f"没有这个检查：{name}"]
    if result.status == "cancelled":
        on_event({"kind": "case-end", "case": case["id"], "passed": False, "status": "cancelled", "attempts": len(result.attempts),
                  "duration_s": 0.0, "checks": {}, "error": "已取消"})
        return None
    passed = result.status == "succeeded" and all(ok for ok, _ in checks.values())
    details = {"note": case.get("note", ""), "status": result.status, "error": result.error, "attempts": len(result.attempts),
               "first_pass": bool(result.attempts) and result.attempts[0].status == "ok", "checks": checks, "usage": result.usage,
               "duration_s": round(sum(a.duration_s for a in result.attempts), 1), "dir": str(job.step_dir)}
    eval_db().add_eval(batch, package.id, case["id"], passed, details, agent=package.id, agent_version=package.version,
                       engine=route.engine, model=result.model)
    on_event({"kind": "case-end", "case": case["id"], "passed": passed, **details})
    return {"case": case["id"], "passed": passed, **details}


def _new_batch(agent_id: str) -> str:
    base = f"e-{compact()}-{agent_id}"
    batch, n = base, 1
    while (user_config_dir() / "evals" / batch).exists() or eval_db().evals(batch):
        n += 1
        batch = f"{base}-{n}"
    (user_config_dir() / "evals" / batch).mkdir(parents=True)
    return batch


def run_agent_eval(agent_id: str, engines: dict[str, Engine], *, engine: str | None = None, model: str | None = None,
                   cases: list[str] | None = None, parallel: int = 1, cancel: threading.Event | None = None,
                   on_event: Callable[[dict], None] | None = None, retry_delay_s: float = 2.0) -> str:
    package = load_package(agent_id)
    selected = [c for c in load_cases(package) if not cases or c["id"] in cases]
    if not selected:
        raise ValueError(f"{agent_id} 没有要跑的评测用例")
    route = resolve(package, None, [engine] if engine else list(engines), load_user_models())
    if model:
        route = replace(route, model=model)
    batch = _new_batch(agent_id)
    cancel = cancel or threading.Event()
    secrets = secret_values()
    emit = on_event or (lambda e: None)
    emit({"kind": "eval-start", "batch": batch, "agent": agent_id, "version": package.version, "engine": route.engine,
          "model": route.model, "cases": [c["id"] for c in selected]})
    with ThreadPoolExecutor(max_workers=max(1, parallel), thread_name_prefix="sma-eval") as pool:
        futures = [pool.submit(run_case, package, case, engine=engines[route.engine], route=route, batch=batch, cancel=cancel,
                               secrets=secrets, retry_delay_s=retry_delay_s, on_event=emit) for case in selected]
        for future in futures:
            future.result()
    return batch


def compare_batches(batch_a: str, batch_b: str) -> list[dict]:
    """两次评测逐条对比：是否通过、第一次是否就合格、哪些检查的结果变了。"""
    db = eval_db()
    a = {r["case_id"]: r for r in db.evals(batch_a)}
    b = {r["case_id"]: r for r in db.evals(batch_b)}

    def checks(row: dict | None) -> dict[str, bool]:
        return {k: v[0] for k, v in (row["details"].get("checks") or {}).items()} if row else {}

    rows = []
    for case_id in sorted(set(a) | set(b)):
        ra, rb = a.get(case_id), b.get(case_id)
        ca, cb = checks(ra), checks(rb)
        rows.append({"case": case_id,
                     "a": bool(ra["passed"]) if ra else None, "b": bool(rb["passed"]) if rb else None,
                     "a_first_pass": ra["details"].get("first_pass") if ra else None,
                     "b_first_pass": rb["details"].get("first_pass") if rb else None,
                     "changed": sorted(k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k))})
    return rows
