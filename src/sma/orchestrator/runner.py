"""编排运行器（设计 03）：按步骤图调度，调用引擎，四层校验，修复重试，存产物；过线检查和返工循环；阻塞、续跑和取消。

总控是代码：步骤、上限和策略都由流程定义和包的声明决定，模型只交产物。
运行状态（每个 gate 走了几轮、最近的决定、正在进行的返工）存在 runs.params 里，续跑时接着用。
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

import sma
from sma.contracts.envelope import Producer, make_envelope, schema_version
from sma.contracts.schemas import OutputSpec, envelope_schema, field_name
from sma.contracts.validate import Context, hash_error, schema_errors, split_output, validate_content, validate_output
from sma.engines.base import Engine
from sma.engines.routing import resolve
from sma.hashing import canonical_json, sha256_file
from sma.locks import LockBusy, file_lock
from sma.orchestrator.attempts import AttemptLog, StepJob, rule_params, run_attempts
from sma.orchestrator.code_steps import CODE_STEPS, CodeContext
from sma.orchestrator.context import MissingSkill
from sma.orchestrator.errors import Blocked, StepError
from sma.orchestrator.gates import GATES, GateContext
from sma.orchestrator.inputs import StepInputs, gather
from sma.orchestrator.workflow import Instance, StepDef, Workflow, expand, parse_workflow, resolve_choice, workflow_path
from sma.packages.loader import Package, PackageError, load_package
from sma.packages.skills import find_skill
from sma.timeutil import compact, stamp
from sma.tracing.isolation import compare, foreign_mentions, snapshot
from sma.tracing.redact import redact_text, secret_values
from sma.userconfig import load_user_models
from sma.workspace import registry
from sma.workspace.workspace import Workspace

FINISHED = {"succeeded", "failed"}


class RunBusy(RuntimeError):
    pass


@dataclass
class Outcome:
    status: str                       # succeeded / failed / cancelled / blocked
    error: str | None = None
    engine: str | None = None
    model: str | None = None
    usage: dict | None = None
    attempts: int = 0
    duration_s: float = 0.0
    blocked: Blocked | None = None


class Runner:
    def __init__(self, workspace: Workspace, engines: dict[str, Engine], *, on_event: Callable[[dict], None] | None = None,
                 cancel: threading.Event | None = None, retry_delay_s: float = 2.0):
        self.ws = workspace
        self.db = workspace.db
        self.engines = engines
        self.on_event = on_event
        self.cancel = cancel or threading.Event()
        self.retry_delay_s = retry_delay_s
        self._packages: dict[str, Package] = {}
        self._secrets = secret_values()
        self._others = registry.other_accounts(workspace.root)
        self._allowed = [*workspace.config.isolation.allowed_terms, workspace.name]
        self.user_models = load_user_models()

    # ---- 对外 ----
    def start(self, workflow_id: str, task: str) -> str:
        """登记一次运行，不执行。"""
        if not task.strip():
            raise ValueError("任务原话不能为空")
        path = workflow_path(workflow_id)
        if not path.is_file():
            raise FileNotFoundError(f"没有这个流程：{workflow_id}")
        text = path.read_text(encoding="utf-8")
        workflow = parse_workflow(text)
        run_id = self._new_run_id()
        run_dir = self.ws.run_dir(run_id)
        run_dir.mkdir(parents=True)
        (run_dir / "workflow.yaml").write_text(text, encoding="utf-8")      # 续跑用登记时的流程定义
        self.db.create_run(run_id, workflow.id, workflow.version, task.strip(), self.ws.config.autonomy.mode, {})
        return run_id

    def run(self, workflow_id: str, task: str) -> tuple[str, str]:
        run_id = self.start(workflow_id, task)
        return run_id, self.execute(run_id)

    def workflow_of(self, run_id: str) -> Workflow:
        return parse_workflow((self.ws.run_dir(run_id) / "workflow.yaml").read_text(encoding="utf-8"))

    def package(self, agent_id: str) -> Package:
        if agent_id not in self._packages:
            self._packages[agent_id] = load_package(agent_id)
        return self._packages[agent_id]

    @contextlib.contextmanager
    def _run_lock(self, run_id: str):
        """同一次运行同时只能由一个进程执行（例如运行还没结束时又执行了续跑）。"""
        try:
            with file_lock(self.ws.run_dir(run_id) / ".lock", wait=False):
                yield
        except LockBusy as exc:
            raise RunBusy(f"运行 {run_id} 正在另一个进程里执行；等它结束，或先停掉它再续跑") from exc

    def execute(self, run_id: str) -> str:
        if self.db.get_run(run_id) is None:
            raise ValueError(f"没有这次运行：{run_id}")
        with self._run_lock(run_id):
            return self._execute(run_id)

    def resume(self, run_id: str) -> str:
        """已成功且产物完好的步骤直接复用；其余的重跑，被重跑步骤的下游全部重跑。"""
        run = self.db.get_run(run_id)
        if run is None:
            raise ValueError(f"没有这次运行：{run_id}")
        if run["status"] == "succeeded":
            raise ValueError(f"运行 {run_id} 已经成功，不需要续跑")
        with self._run_lock(run_id):
            self._reset_for_resume(run_id)
            return self._execute(run_id)

    # ---- 运行状态 ----
    def _state(self, run_id: str) -> dict:
        state = dict(self.db.get_run(run_id)["params"] or {})
        state.setdefault("rounds", {})
        state.setdefault("gates", {})
        return state

    def _save_state(self, run_id: str, state: dict) -> None:
        self.db.update_run_params(run_id, state)

    def _statuses(self, run_id: str) -> dict[str, str]:
        """中断时正在运行、被取消或阻塞的步骤记为待运行。"""
        return {r["step_id"]: r["status"] if r["status"] in FINISHED | {"skipped"} else "pending" for r in self.db.get_steps(run_id)}

    def _expand(self, run_id: str, workflow: Workflow, status: dict[str, str]) -> list[Instance]:
        dynamic: dict[str, list[dict] | None] = {}
        for step in workflow.steps.values():
            source = step.foreach.source if step.foreach else None
            if source and source not in dynamic:
                dynamic[source] = self._choice(run_id, workflow, source, status)
        return expand(workflow, dynamic)

    def _choice(self, run_id: str, workflow: Workflow, source: str, status: dict[str, str]) -> list[dict] | None:
        """foreach.from 的取值：来源步骤成功就用它的结果，失败（且允许继续）就用默认值，还没结束就是 None。"""
        name, _, field_ = source.partition(".")
        state = status.get(name)
        if state == "succeeded":
            records = self.db.artifacts(run_id, name)
            picked = None
            if records:
                content = json.loads((self.ws.root / records[0]["path"]).read_text(encoding="utf-8"))["content"]
                picked = content.get(field_)
            return resolve_choice(workflow, source, picked)
        if state == "failed" and workflow.steps[name].on_fail.action == "continue":
            return resolve_choice(workflow, source, None)
        return None

    @staticmethod
    def _group(workflow: Workflow, instances: list[Instance]) -> dict[str, list[Instance]]:
        return {name: [i for i in instances if i.step == name] for name in workflow.steps}

    def _ensure_rows(self, run_id: str, workflow: Workflow, instances: list[Instance], status: dict[str, str]) -> None:
        for inst in instances:
            if inst.id in status:
                continue
            step = workflow.steps[inst.step]
            initial = "skipped" if step.routed else "pending"
            if self.db.get_step(run_id, inst.id) is None:
                self.db.upsert_step(run_id, inst.id, kind="code" if step.code else "agent", agent=step.agent or step.code,
                                    params=inst.params, status=initial)
            status[inst.id] = initial

    def _group_state(self, workflow: Workflow, by_step: dict[str, list[Instance]], status: dict[str, str], state: dict, name: str) -> str:
        step = workflow.steps[name]
        instances = by_step.get(name, [])
        if not instances:
            return "waiting"                      # foreach.from 的来源还没有结果
        states = [status.get(i.id, "pending") for i in instances]
        if step.routed:
            return "skipped" if states == ["skipped"] else {"pending": "open", "running": "open"}.get(states[0], states[0])
        ok = states.count("succeeded")
        open_ = sum(1 for s in states if s not in FINISHED)
        if ok + open_ < step.required_successes(len(instances)):
            return "failed"
        if open_:
            return "open"
        if step.gate and state["gates"].get(name) != "proceed":
            return "open"                         # 还没过 gate，或者正在返工、重出
        return "succeeded"

    def _needs_met(self, workflow: Workflow, by_step, status, state, inst: Instance) -> bool:
        step = workflow.steps[inst.step]
        for need in step.needs:
            if step.routed:
                # 被 gate 路由的步骤只要上游的实例都成功了就能跑（上游的 gate 此时正等着它）
                if not by_step.get(need) or any(status.get(i.id) != "succeeded" for i in by_step[need]):
                    return False
                continue
            group = self._group_state(workflow, by_step, status, state, need)
            if group == "succeeded" or (group == "failed" and workflow.steps[need].on_fail.action == "continue"):
                continue
            return False
        return True

    def _reset(self, run_id: str, workflow: Workflow, status: dict[str, str], state: dict, by_step: dict[str, list[Instance]],
               steps: set[str], keep: set[str] = frozenset()) -> list[str]:
        """把这些步骤重置为待运行；routed 步骤回到跳过并撤下产物；清掉这些步骤的 gate 决定。
        不撤普通步骤的产物登记：下一轮要读上一轮的评审意见，步骤真正开始时才撤。"""
        reset = []
        for name in steps:
            step = workflow.steps[name]
            for inst in by_step.get(name, []):
                if inst.id in keep:
                    continue
                new = "skipped" if step.routed else "pending"
                status[inst.id] = new
                self.db.upsert_step(run_id, inst.id, status=new, error=None)
                if step.routed:
                    self.db.delete_step_artifacts(run_id, inst.id)
                reset.append(inst.id)
            if name not in keep:
                state["gates"].pop(name, None)
                if step.routed and (state.get("route") or {}).get("after") == name:
                    state.pop("route", None)
                    state.pop("rework_target", None)
        return reset

    def _reset_for_resume(self, run_id: str) -> None:
        workflow = self.workflow_of(run_id)
        state = self._state(run_id)
        status = self._statuses(run_id)
        by_step = self._group(workflow, self._expand(run_id, workflow, status))
        route = state.get("route") or {}
        rerun: set[str] = set()
        dirty: set[str] = set()
        for name, instances in by_step.items():
            step = workflow.steps[name]
            states = {i.id: status.get(i.id, "pending") for i in instances}
            if step.routed:
                if route.get("after") == name and any(s != "succeeded" for s in states.values()):
                    rerun |= set(states)          # 中断时正在返工：接着返工
                continue
            broken = {i for i, s in states.items() if s == "succeeded" and not self._artifacts_intact(run_id, i)}
            ok = sum(1 for i, s in states.items() if s == "succeeded" and i not in broken)
            open_ = any(s not in FINISHED for s in states.values())
            if instances and (broken or open_ or ok < step.required_successes(len(instances))):
                dirty.add(name)
                rerun |= broken | {i for i, s in states.items() if s != "succeeded"}
        for inst_id in rerun:
            status[inst_id] = "pending"
            self.db.upsert_step(run_id, inst_id, status="pending", error=None)
        downstream = set().union(*(workflow.downstream(name) for name in dirty)) if dirty else set()
        reset = self._reset(run_id, workflow, status, state, by_step, downstream - dirty)
        for name in dirty:
            state["gates"].pop(name, None)
        for name, decision in list(state["gates"].items()):
            if decision == "block":           # 续跑 = 再判断一次；轮数用完的重新计轮，再给一次机会
                state["gates"].pop(name)
                state["rounds"][name] = 0
        self._save_state(run_id, state)
        self._emit("resume", run=run_id, rerun=sorted(rerun | set(reset)))

    # ---- 调度 ----
    def _execute(self, run_id: str) -> str:
        run = self.db.get_run(run_id)
        workflow = self.workflow_of(run_id)
        state = self._state(run_id)
        self.db.set_run_status(run_id, "running")
        self._emit("run-start", run=run_id, task=run["task"])
        before = snapshot(self.ws.root)
        try:
            self._precheck(run_id, workflow)
        except Blocked as blocked:
            return self._finish(run_id, before, "blocked", blocked=blocked, step_id="precheck", status={})
        except StepError as exc:
            return self._finish(run_id, before, "failed", error=str(exc), status={})

        status = self._statuses(run_id)
        limit = self.ws.config.autonomy.max_parallel_steps
        running: dict[Future, Instance] = {}
        halt: tuple | None = None
        by_step: dict[str, list[Instance]] = {}
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="sma-step") as pool:
            while True:
                try:
                    instances = self._expand(run_id, workflow, status)
                    by_step = self._group(workflow, instances)
                    self._ensure_rows(run_id, workflow, instances, status)
                    if halt is None:
                        blocked = self._advance(run_id, workflow, status, state, by_step)
                        if blocked is not None:
                            halt = ("blocked", blocked[0], blocked[1])
                    if self.cancel.is_set() and halt is None:
                        halt = ("cancelled",)
                    if halt is None:
                        for inst in instances:
                            if len(running) >= limit:
                                break
                            if status[inst.id] == "pending" and self._needs_met(workflow, by_step, status, state, inst):
                                status[inst.id] = "running"
                                self.db.upsert_step(run_id, inst.id, status="running", started_at=stamp(), finished_at=None, error=None)
                                step = workflow.steps[inst.step]
                                sources = {need: [i.id for i in by_step.get(need, []) if status[i.id] == "succeeded"] for need in step.needs}
                                all_ids = {name: [i.id for i in group] for name, group in by_step.items()}
                                self._emit("step-start", step=inst.id)
                                running[pool.submit(self._run_step, run, workflow, inst, sources, all_ids, dict(state))] = inst
                    if not running:
                        break
                    done, _ = wait(list(running), timeout=0.5, return_when=FIRST_COMPLETED)
                except KeyboardInterrupt:
                    self.cancel.set()
                    continue
                for future in done:
                    inst = running.pop(future)
                    outcome = self._outcome(run_id, inst, future)
                    status[inst.id] = {"cancelled": "pending"}.get(outcome.status, outcome.status)
                    outcome.error = self._clean(outcome.error)
                    self.db.upsert_step(run_id, inst.id, status=status[inst.id], finished_at=stamp(), duration_s=round(outcome.duration_s, 2),
                                        error=outcome.error, usage=outcome.usage)
                    self._emit("step-end", step=inst.id, status=outcome.status, duration_s=outcome.duration_s, error=outcome.error)
                    if halt is not None:
                        continue
                    step = workflow.steps[inst.step]
                    if outcome.status == "blocked":
                        halt = ("blocked", inst.id, outcome.blocked)
                    elif outcome.status == "failed" and step.on_fail.action != "continue" and (
                            step.routed or self._group_state(workflow, by_step, status, state, inst.step) == "failed"):
                        halt = ("failed", inst)

        if halt is None:
            if self._all_done(workflow, by_step, status, state):
                return self._finish(run_id, before, "succeeded", status=status)
            return self._finish(run_id, before, "failed", error="有步骤没有完成", status=status)
        if halt[0] == "cancelled":
            return self._finish(run_id, before, "cancelled", status=status)
        if halt[0] == "blocked":
            return self._finish(run_id, before, "blocked", blocked=halt[2], step_id=halt[1], status=status)
        inst = halt[1]
        step = workflow.steps[inst.step]
        error = f"步骤 {inst.id} 失败：{(self.db.get_step(run_id, inst.id) or {}).get('error')}"
        if step.on_fail.action == "block":
            blocked = Blocked(step.on_fail.reason, [f"查看原因：sma runs show {run_id} --step {inst.slug}", "处理后续跑"], error)
            return self._finish(run_id, before, "blocked", blocked=blocked, step_id=inst.id, status=status)
        return self._finish(run_id, before, "failed", error=error, status=status)

    def _all_done(self, workflow: Workflow, by_step, status, state) -> bool:
        for name, step in workflow.steps.items():
            group = self._group_state(workflow, by_step, status, state, name)
            if group in {"succeeded", "skipped"} or (group == "failed" and step.on_fail.action == "continue"):
                continue
            return False
        return True

    def _advance(self, run_id: str, workflow: Workflow, status, state, by_step) -> tuple[str, Blocked] | None:
        """返工完成就按路由重跑；做完的 gate 步骤还没判断的，判断它往哪走。返回阻塞（步骤 id, 原因）或 None。"""
        changed = False
        route = state.get("route")
        if route:
            routed = by_step.get(route["after"], [])
            if routed and all(status.get(i.id) == "succeeded" for i in routed):
                steps = {route["rerun_from"], *workflow.downstream(route["rerun_from"])}
                self._reset(run_id, workflow, status, state, by_step, steps, keep={i.id for i in routed} | {route["after"]})
                state.pop("route", None)
                self._emit("route", step=route["after"], rerun=sorted(steps))
                changed = True
        result = None
        for name, step in workflow.steps.items():
            instances = by_step.get(name, [])
            if not step.gate or name in state["gates"] or not instances or any(status.get(i.id) != "succeeded" for i in instances):
                continue
            outputs: dict[str, list[dict]] = {}
            for record in self.db.artifacts(run_id, instances[0].id):
                content = json.loads((self.ws.root / record["path"]).read_text(encoding="utf-8"))["content"]
                outputs.setdefault(record["type"], []).append(content)
            rounds = state["rounds"].get(name, 0)
            decision = GATES[step.gate](GateContext(run_id, name, outputs, rounds, self._max_rounds(step), step.gate_params))
            self.db.add_check(f"run:{run_id}", f"gate:{name}", decision.action, decision.note or decision.reason)
            self._emit("gate", step=name, action=decision.action, note=decision.note, round=rounds + 1)
            changed = True
            if decision.action == "proceed":
                state["gates"][name] = "proceed"
            elif decision.action == "block":
                state["gates"][name] = "block"
                result = (instances[0].id, Blocked(decision.reason, decision.needed, decision.detail))
                break
            elif decision.action == "rework":
                state["rounds"][name] = rounds + 1
                state["gates"][name] = "rework"
                state.update(decision.state)
                state["route"] = {"after": decision.routed_step, "rerun_from": decision.rerun_from}
                for inst in by_step.get(decision.routed_step, []):
                    status[inst.id] = "pending"
                    self.db.upsert_step(run_id, inst.id, status="pending", error=None)
            elif decision.action == "rerun":
                state["rounds"][name] = rounds + 1
                state.pop("rework_target", None)
                steps = {decision.rerun_from, *workflow.downstream(decision.rerun_from)}
                self._reset(run_id, workflow, status, state, by_step, steps)
        if changed:
            self._save_state(run_id, state)
        return result

    def _max_rounds(self, step: StepDef) -> int:
        value = step.gate_params.get("max_rounds", 0)
        if isinstance(value, str) and value.startswith("autonomy."):
            return int(getattr(self.ws.config.autonomy, value.split(".", 1)[1]))
        return int(value)

    def _outcome(self, run_id: str, inst: Instance, future: Future) -> Outcome:
        try:
            return future.result()
        except Blocked as blocked:
            return Outcome("blocked", error=blocked.detail or blocked.reason, blocked=blocked)
        except Exception as exc:   # 编排层自己的错误：记下来，不让整个运行崩掉
            log = self.ws.run_dir(run_id) / "steps" / inst.slug / "error.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc(), encoding="utf-8")
            return Outcome("failed", error=f"{type(exc).__name__}: {exc}（详情见 {log.relative_to(self.ws.root)}）")

    # ---- 前提检查 ----
    def _precheck(self, run_id: str, workflow: Workflow) -> None:
        if "positioning_confirmed" in workflow.preconditions and not self.ws.positioning_confirmed():
            self.db.add_check(f"run:{run_id}", "precheck", "blocked", "定位没有确认")
            raise Blocked("positioning-unconfirmed", ["在 strategy/positioning.md 写好定位", '本人确认后运行：sma strategy confirm --note "<本人原话>"'],
                          "定位还没确认，或确认之后定位文件又改过")
        engines: set[str] = set()
        for name, step in workflow.steps.items():
            if step.gate and step.gate not in GATES:
                raise StepError(f"步骤 {name} 的 gate {step.gate} 不存在")
            if step.code:
                if step.code not in CODE_STEPS:
                    raise StepError(f"流程里的代码步骤 {step.code} 不存在")
                continue
            try:
                package = self.package(step.agent)
            except PackageError as exc:
                raise StepError(str(exc)) from exc
            missing = [s.name for s in package.spec.skills if s.required and find_skill(s.name) is None]
            if missing:
                raise StepError(f"{package.id} 缺少必需的技能：{'、'.join(missing)}")
            try:
                engines.add(resolve(package, self.ws.config, list(self.engines), self.user_models).engine)
            except RuntimeError as exc:
                raise StepError(str(exc)) from exc
        for name in sorted(engines):
            state = self.engines[name].check()
            if not state.available:
                raise StepError(f"引擎 {name} 不可用：{state.detail}")
            if state.logged_in is False:
                self.db.add_check(f"run:{run_id}", "precheck", "blocked", f"{name} 没有登录")
                raise Blocked("needs-login", [f"登录 {name} 引擎：{state.detail or '按引擎的说明登录'}", "登录后续跑"], state.detail)
        self.db.add_check(f"run:{run_id}", "precheck", "pass", "定位已确认，包、技能和引擎可用")

    # ---- 步骤执行 ----
    def _run_step(self, run: dict, workflow: Workflow, inst: Instance, sources: dict[str, list[str]], all_ids: dict[str, list[str]],
                  state: dict) -> Outcome:
        started = time.time()
        step = workflow.steps[inst.step]
        try:
            inputs = gather(self.ws, run["id"], workflow, inst, sources, all_ids)
            self.db.delete_step_artifacts(run["id"], inst.id)      # 读完输入再撤自己的旧登记（自己的旧产物可能是别人的 feedback）
            outcome = self._run_code(run, step, inst, inputs, state) if step.code else self._run_agent(run, step, inst, inputs)
        except (StepError, MissingSkill) as exc:
            outcome = Outcome("failed", error=str(exc))
        outcome.duration_s = time.time() - started
        return outcome

    @staticmethod
    def _contents(inputs: StepInputs) -> dict[str, list[dict]]:
        return {t: [a.content for a in items] for t, items in inputs.artifacts.items()}

    def _run_code(self, run: dict, step: StepDef, inst: Instance, inputs: StepInputs, state: dict) -> Outcome:
        fn = CODE_STEPS[step.code]
        parts = fn(CodeContext(self.ws, run["id"], inst, inputs.artifacts, state))
        specs = [OutputSpec(o.type, o.many) for o in step.outputs]
        output = {field_name(s.type, s.many): parts.get(s.type, []) if s.many else (parts.get(s.type) or [None])[0] for s in specs}
        problems = validate_output(specs, output, Context(params=rule_params(inst.params), resolver=self.ws, inputs=self._contents(inputs)))
        if problems:
            return Outcome("failed", engine="code", error="代码步骤的产物没过校验：" + "；".join(problems[:10]))
        self._store(run["id"], inst, split_output(specs, output), specs, Producer(f"code:{step.code}", sma.__version__, "code", None), inputs.refs)
        return Outcome("succeeded", engine="code")

    def _run_agent(self, run: dict, step: StepDef, inst: Instance, inputs: StepInputs) -> Outcome:
        package = self.package(step.agent)
        route = resolve(package, self.ws.config, list(self.engines), self.user_models)
        run_id = run["id"]
        params = {**package.spec.params, **inst.params}
        first_n = max((a["n"] for a in self.db.list_attempts(run_id, inst.id)), default=0) + 1
        job = StepJob(label=inst.id, package=package, route=route, engine=self.engines[route.engine],
                      step_dir=self.ws.run_dir(run_id) / "steps" / inst.slug, first_n=first_n, task=run["task"],
                      params=params, rule_params=rule_params(params), inputs=inputs.files, extra_paths=inputs.paths,
                      memory=self._memory(package), resolver=self.ws, run_label=f"{self.ws.name} · {run_id} · {inst.id}",
                      extra_checks=self._isolation_problems, rule_inputs=self._contents(inputs))

        def on_start(n: int, attempt_dir) -> None:
            self.db.add_attempt(run_id, inst.id, n, attempt_dir.relative_to(self.ws.root).as_posix())
            self.db.upsert_step(run_id, inst.id, attempts=n, engine=route.engine, model=route.model, agent=package.id,
                                agent_version=package.version)
            self._emit("attempt-start", step=inst.id, attempt=n, engine=route.engine, model=route.model)

        def on_end(log: AttemptLog) -> None:
            self.db.finish_attempt(run_id, inst.id, log.n, log.status, log.detail, log.problems, log.usage)
            self._emit("attempt-end", step=inst.id, attempt=log.n, status=log.status, detail=log.detail, problems=log.problems)

        result = run_attempts(job, cancel=self.cancel, secrets=self._secrets, retry_delay_s=self.retry_delay_s,
                              on_start=on_start, on_end=on_end)
        done = Outcome(result.status, error=result.error, engine=route.engine, model=result.model, usage=result.usage or None,
                       attempts=len(result.attempts))
        if result.status == "auth_error":
            raise Blocked("needs-login", [f"登录 {route.engine} 引擎（例如在终端运行 codex login）", "登录后续跑"], result.error or "")
        if result.status == "succeeded":
            self._store(run_id, inst, split_output(package.output_specs, result.output), package.output_specs,
                        Producer(package.id, package.version, route.engine, result.model), inputs.refs)
        return done

    def _isolation_problems(self, output: dict) -> list[str]:
        hits = foreign_mentions(output, self._others, self._allowed)
        return [f"[隔离] {where} 里出现了本机其他账号的名称（{masked}）；只能使用本账号的资料，请改掉" for where, masked in hits[:10]]

    def _memory(self, package: Package) -> str | None:
        if not package.spec.memory.read:
            return None
        path = self.ws.root / "memory" / f"{package.id}.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    # ---- 存档 ----
    def _store(self, run_id: str, inst: Instance, parts: dict[str, list[dict]], specs: list[OutputSpec], producer: Producer,
               inputs: list[dict]) -> list[str]:
        directory = self.ws.run_dir(run_id) / "artifacts" / inst.slug
        directory.mkdir(parents=True, exist_ok=True)
        versions = [int(m.group(1)) for p in directory.glob("*.json") if (m := re.search(r"\.v(\d+)\.json$", p.name))]
        version = max(versions, default=0) + 1
        many = {s.type: s.many for s in specs}
        ids = []
        for artifact_type, items in parts.items():
            for index, content in enumerate(items, 1):
                idx = index if many[artifact_type] else None
                envelope = make_envelope(artifact_type, content, run_id=run_id, step_id=inst.id, slug=inst.slug, index=idx,
                                         version=version, producer=producer, inputs=inputs)
                problems = schema_errors(envelope_schema(), envelope)
                if problems:
                    raise RuntimeError(f"信封不合格（编排层的问题）：{problems}")
                path = directory / f"{artifact_type}{f'-{idx}' if idx else ''}.v{version}.json"
                path.write_text(redact_text(canonical_json(envelope), self._secrets), encoding="utf-8")
                self.db.add_artifact(envelope["id"], run_id, inst.id, artifact_type, schema_version(artifact_type), version,
                                     path.relative_to(self.ws.root).as_posix(), sha256_file(path))
                ids.append(envelope["id"])
        return ids

    def _artifacts_intact(self, run_id: str, step_id: str) -> bool:
        records = self.db.artifacts(run_id, step_id)
        return bool(records) and all(hash_error(self.ws.root / r["path"], r["sha256"]) is None for r in records)

    # ---- 收尾 ----
    def _finish(self, run_id: str, before: dict, final: str, *, status: dict[str, str], error: str | None = None,
                blocked: Blocked | None = None, step_id: str = "") -> str:
        changes = compare(before, snapshot(self.ws.root))
        changed = [f"{kind}：{'、'.join(paths[:10])}" for kind, paths in changes.items() if paths]
        self.db.add_check(f"run:{run_id}", "workspace-isolation", "warn" if changed else "pass",
                          "；".join(changed) or "运行期间工作区里 SMA 目录以外的文件没有变化")
        error = self._clean(error)
        if blocked is not None:
            blocked.detail = self._clean(blocked.detail) or ""
            content = {"reason": blocked.reason, "done": sorted(i for i, s in status.items() if s == "succeeded"),
                       "needed": blocked.needed, "resume": f"sma runs resume {run_id} --account {self.ws.root}"}
            problems = validate_content("blocking-report", content)
            if problems:
                raise RuntimeError(f"阻塞报告不合格（编排层的问题）：{problems}")
            self.db.delete_artifacts(run_id, "blocking-report")      # 只留最新的一份，旧的文件仍在运行目录里
            self._store(run_id, Instance(step_id, step_id), {"blocking-report": [content]}, [OutputSpec("blocking-report")],
                        Producer("sma", sma.__version__, "code", None), [])
            self.db.set_run_status(run_id, "blocked", blocked_reason=blocked.reason, error=blocked.detail or None)
        else:
            self.db.set_run_status(run_id, final, error=error)
        self._emit("run-end", run=run_id, status=final, error=error or (blocked.detail if blocked else None),
                   reason=blocked.reason if blocked else None, isolation=changed)
        return final

    # ---- 其他 ----
    def _clean(self, text: str | None) -> str | None:
        return redact_text(text, self._secrets) if text else text

    def _new_run_id(self) -> str:
        base = f"r-{compact()}"
        run_id, n = base, 1
        while self.db.get_run(run_id) or self.ws.run_dir(run_id).exists():
            n += 1
            run_id = f"{base}-{n}"
        return run_id

    def _emit(self, kind: str, **data) -> None:
        if self.on_event is None:
            return
        with contextlib.suppress(Exception):      # 显示出错不影响运行
            self.on_event({"kind": kind, **data})
