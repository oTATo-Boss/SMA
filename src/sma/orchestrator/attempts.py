"""一个 Agent 步骤的尝试循环：装配目录 → 调引擎 → 脱敏 → 校验 → 不合格带着问题修复重试（设计 02 第 5 节）。

运行器和包级评测共用这一段，状态库的登记通过回调交给调用方。
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sma.contracts.validate import Context, EvidenceResolver, collect_urls, unseen_urls, validate_output
from sma.engines.base import Engine, StepRequest
from sma.engines.routing import Route
from sma.orchestrator.context import InputFile, assemble
from sma.packages.loader import Package
from sma.tracing.redact import redact_file, redact_text

TRACE_FILES = ("events.jsonl", "stderr.log", "output.json", "prompt.md")


@dataclass
class AttemptLog:
    n: int
    dir: Path
    status: str                      # ok / invalid / engine_error / timeout / auth_error / cancelled
    detail: str | None = None
    problems: list[str] = field(default_factory=list)
    usage: dict | None = None
    duration_s: float = 0.0


@dataclass
class StepJob:
    label: str                       # 步骤实例 id，例如 ideation[original]
    package: Package
    route: Route
    engine: Engine
    step_dir: Path                   # 尝试目录的上级：attempt-<n> 建在这里
    first_n: int                     # 本次第一个尝试的序号（续跑时接着往后编）
    task: str
    params: dict                     # 写进任务正文的参数
    rule_params: dict                # 语义规则用的参数
    inputs: list[InputFile]
    extra_paths: list[tuple[str, str]]
    memory: str | None
    resolver: EvidenceResolver | None
    run_label: str
    extra_checks: Callable[[dict], list[str]] | None = None


@dataclass
class AttemptsResult:
    status: str                      # succeeded / failed / cancelled / auth_error
    output: dict | None
    model: str | None
    usage: dict
    attempts: list[AttemptLog]
    error: str | None = None


def rule_params(params: dict) -> dict:
    """语义规则用的参数：foreach 的值（字典）只取 id。"""
    return {k: v["id"] if isinstance(v, dict) and "id" in v else v for k, v in params.items()}


def add_usage(total: dict, usage: dict | None) -> dict:
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
    return total


def check_output(package: Package, output: dict, rule_params: dict, resolver: EvidenceResolver | None) -> list[str]:
    problems = validate_output(package.output_specs, output, Context(params=rule_params, resolver=resolver))
    size = len(json.dumps(output, ensure_ascii=False).encode("utf-8"))
    if size > package.spec.limits.max_output_kb * 1024:
        problems.append(f"[大小] 输出 {size // 1024} KB，超过上限 {package.spec.limits.max_output_kb} KB")
    return problems


def seen_problems(job: StepJob, output: dict, seen_text: str | None) -> list[str]:
    """第 3 层校验的一部分：引用的链接必须出现在输入文件或引擎本次看到的内容里。
    引擎报告不了看到什么、又允许联网时跳过（不能核对）。"""
    if seen_text is None and job.package.spec.network != "none":
        return []
    corpus = "\n".join([*(f.content for f in job.inputs), seen_text or ""])
    return [f"[证据] 链接 {url} 不在输入文件、本次搜索结果或读过的内容里；只引用真的看到的链接，或删掉这条依据"
            for url in unseen_urls(collect_urls(output), corpus)]


def highest_attempt(step_dir: Path) -> int:
    """目录里已有的最大尝试序号（包括状态库里没登记上的）。"""
    numbers = [int(m.group(1)) for p in step_dir.glob("attempt-*") if (m := re.fullmatch(r"attempt-(\d+)", p.name))]
    return max(numbers, default=0)


def run_attempts(job: StepJob, *, cancel: threading.Event, secrets: dict[str, str], retry_delay_s: float = 2.0,
                 on_start: Callable[[int, Path], None] | None = None,
                 on_end: Callable[[AttemptLog], None] | None = None) -> AttemptsResult:
    limits = job.package.spec.limits
    usage: dict = {}
    logs: list[AttemptLog] = []
    repair: list[str] | None = None
    last_error, last_kind = "", None
    n = max(job.first_n - 1, highest_attempt(job.step_dir))

    def clean(text: str | None) -> str | None:
        return redact_text(text, secrets) if text else text

    def finish(log: AttemptLog) -> None:
        log.detail = clean(log.detail)
        log.problems = [clean(p) for p in log.problems]
        logs.append(log)
        if on_end:
            on_end(log)

    for k in range(1, limits.max_attempts + 1):
        if last_kind == "engine":
            cancel.wait(min(retry_delay_s * 2 ** (k - 2), 30))          # 引擎出错后退避再试
        if cancel.is_set():
            return AttemptsResult("cancelled", None, job.route.model, usage, logs, "已取消")
        n += 1
        attempt_dir = job.step_dir / f"attempt-{n}"
        attempt_dir.mkdir(parents=True)
        if on_start:
            on_start(n, attempt_dir)            # 先登记再装配：装配出错也留下记录
        try:
            assembled = assemble(attempt_dir, job.package, run_label=job.run_label, task=job.task, params=job.params,
                                 inputs=job.inputs, extra_paths=job.extra_paths, memory=job.memory, repair=repair)
        except Exception as exc:                # 例如技能文件读不了：重试也没用
            finish(AttemptLog(n, attempt_dir, "error", f"准备步骤目录出错：{type(exc).__name__}: {exc}"))
            return AttemptsResult("failed", None, job.route.model, usage, logs, logs[-1].detail)
        spec = job.package.spec
        result = job.engine.run(StepRequest(label=job.label, attempt_dir=attempt_dir, prompt=assembled.prompt,
                                            output_schema=assembled.schema_path, sandbox=spec.sandbox, network=spec.network,
                                            model=job.route.model, reasoning=job.route.reasoning, timeout_s=limits.timeout_s),
                                cancel=cancel)
        for name in TRACE_FILES:
            redact_file(attempt_dir / name, secrets)
        add_usage(usage, result.usage)
        log = AttemptLog(n, attempt_dir, result.status, result.error, usage=result.usage, duration_s=result.duration_s)
        if result.status == "cancelled" or cancel.is_set():
            log.status, log.detail = "cancelled", "已取消"
            finish(log)
            return AttemptsResult("cancelled", None, job.route.model, usage, logs, "已取消")
        if result.status == "auth_error":
            finish(log)
            return AttemptsResult("auth_error", None, job.route.model, usage, logs, log.detail)
        if result.status != "ok":
            finish(log)
            last_error, last_kind = f"{result.status}：{log.detail}", "engine"
            continue
        problems = check_output(job.package, result.output, job.rule_params, job.resolver)
        problems += seen_problems(job, result.output, result.seen_text)
        if job.extra_checks:
            problems += job.extra_checks(result.output)
        if problems:
            log.status, log.problems = "invalid", problems
            finish(log)
            last_error, last_kind, repair = "输出没通过校验：" + "；".join(log.problems[:5]), "invalid", log.problems
            continue
        finish(log)
        return AttemptsResult("succeeded", result.output, result.model or job.route.model, usage, logs)
    error = f"{limits.max_attempts} 次尝试都没有成功；最后一次：{last_error}"[:4000]
    return AttemptsResult("failed", None, job.route.model, usage, logs, error)
