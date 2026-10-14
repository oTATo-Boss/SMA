"""命令行入口：sma。"""

from __future__ import annotations

import json
import signal
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

import sma
from sma.doctor import Item, check_account, check_environment
from sma.engines.base import Engine
from sma.engines.codex import CodexEngine
from sma.evals.runner import compare_batches, eval_db, run_agent_eval
from sma.orchestrator.runner import Runner
from sma.orchestrator.workflow import expand, parse_workflow
from sma.packages.loader import lint_package, list_packages
from sma.workspace import registry
from sma.workspace.workspace import POSITIONING, Workspace, WorkspaceError

app = typer.Typer(help="SMA：内容创作的多 Agent 系统", no_args_is_help=True, add_completion=False)
account_app = typer.Typer(help="账号工作区", no_args_is_help=True)
strategy_app = typer.Typer(help="定位与策略", no_args_is_help=True)
runs_app = typer.Typer(help="运行记录", no_args_is_help=True)
package_app = typer.Typer(help="Agent 包", no_args_is_help=True)
eval_app = typer.Typer(help="评测", no_args_is_help=True)
app.add_typer(account_app, name="account")
app.add_typer(strategy_app, name="strategy")
app.add_typer(runs_app, name="runs")
app.add_typer(package_app, name="package")
app.add_typer(eval_app, name="eval")

console = Console(highlight=False)
ACCOUNT = typer.Option(None, "--account", "-a", help="账号工作区；不写就从当前目录往上找")
MARK = {True: "[green]✓[/]", None: "[yellow]![/]", False: "[red]✗[/]"}
STATUS_STYLE = {"succeeded": "green", "failed": "red", "blocked": "yellow", "cancelled": "dim", "running": "cyan", "pending": "dim"}


def engines() -> dict[str, Engine]:
    """可用的引擎。测试里替换这个函数。"""
    return {"codex": CodexEngine()}


def _workspace(account: Path | None) -> Workspace:
    try:
        return Workspace(account) if account else Workspace.find(Path.cwd())
    except WorkspaceError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc


def _status(text: str | None) -> str:
    return f"[{STATUS_STYLE.get(text or '', 'white')}]{text}[/]"


def _tokens(usage: dict | None) -> str:
    if not usage:
        return "-"
    total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    return f"{total / 1000:.1f}k"


def _items(items: list[Item]) -> bool:
    for item in items:
        console.print(f"{MARK[item.ok]} {escape(item.name)}" + (f"  [dim]{escape(item.detail)}[/]" if item.detail else ""))
    return all(i.ok is not False for i in items)


@app.callback(invoke_without_command=True)
def main(version: bool = typer.Option(False, "--version", help="显示版本")) -> None:
    if version:
        console.print(f"sma {sma.__version__}")
        raise typer.Exit()


# ---- 账号 ----
@account_app.command("init")
def account_init(path: Path = typer.Argument(Path("."), help="账号工作区（空文件夹，默认当前目录）"),
                 name: str = typer.Option(None, "--name", "-n", help="账号名；不写就用文件夹名"),
                 platform: list[str] = typer.Option([], "--platform", "-p", help="平台，可写多次，例如 -p 抖音 -p 小红书")) -> None:
    """建账号工作区并登记到本机。"""
    root = path.expanduser().resolve()
    account = name or root.name.lstrip("@")
    try:
        ws = Workspace.init(root, account, platform)
    except WorkspaceError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    registry.register(ws.name, ws.root)
    console.print(f"[green]已建好账号工作区[/]：{ws.name}（{ws.root}）")
    console.print("接下来：")
    console.print(f"  1. 在 {POSITIONING} 写好定位：账号是谁、给谁看、做什么内容、核心资产是什么")
    console.print('  2. 本人确认后运行：sma strategy confirm --note "<本人确认的原话>"')
    console.print('  3. 一句话开工：sma run "今天做一条视频"')


@account_app.command("register")
def account_register(path: Path = typer.Argument(Path("."), help="已有的账号工作区")) -> None:
    """把已有的工作区登记到本机（隔离检查用）。"""
    ws = _workspace(path)
    registry.register(ws.name, ws.root)
    console.print(f"已登记：{ws.name}（{ws.root}）")


@account_app.command("list")
def account_list() -> None:
    """本机登记的账号工作区。"""
    table = Table("账号", "路径", "状态")
    for a in registry.list_accounts():
        exists = (Path(a["path"]) / "sma.yaml").is_file()
        table.add_row(a["name"], a["path"], "可用" if exists else "[red]找不到[/]")
    console.print(table)


# ---- 定位 ----
@strategy_app.command("confirm")
def strategy_confirm(note: str = typer.Option(..., "--note", help="本人确认的原话"), account: Path = ACCOUNT) -> None:
    """记录本人对定位的确认，绑定定位文件当前的版本。"""
    ws = _workspace(account)
    try:
        digest = ws.confirm_positioning(note)
    except WorkspaceError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    console.print(f"[green]已记录确认[/]（{POSITIONING} · {digest[:12]}）。这个文件以后改了，确认会自动失效。")


@strategy_app.command("status")
def strategy_status(account: Path = ACCOUNT) -> None:
    """定位是否已确认。"""
    ws = _workspace(account)
    record = ws.db.latest_confirmation(POSITIONING)
    if ws.positioning_confirmed():
        console.print(f"[green]已确认[/]：{record['at']} · {record['note']}")
    elif record:
        console.print(f"[yellow]确认已失效[/]：{POSITIONING} 在 {record['at']} 确认之后改过，需要重新确认")
    else:
        console.print(f"[yellow]还没确认[/]：写好 {POSITIONING} 后运行 sma strategy confirm --note \"原话\"")


# ---- 运行 ----
def _printer(event: dict) -> None:
    kind = event["kind"]
    now = datetime.now().strftime("%H:%M:%S")
    if kind == "run-start":
        console.print(f"[bold]▶ 运行 {event['run']}[/]：{escape(event['task'])}")
    elif kind == "resume":
        console.print(f"续跑：重跑 {len(event['rerun'])} 个步骤 {escape('、'.join(event['rerun']))}")
    elif kind == "attempt-start":
        console.print(f"[dim]{now}[/]  {escape(event['step'])} 第 {event['attempt']} 次尝试（{event['engine']} · {event['model'] or '引擎默认型号'}）")
    elif kind == "attempt-end" and event["status"] == "invalid":
        console.print(f"[dim]{now}[/]  [yellow]{escape(event['step'])} 输出没过校验（{len(event['problems'])} 个问题），带着问题重试[/]")
        for problem in event["problems"][:3]:
            console.print(f"           [dim]{escape(problem[:160])}[/]")
    elif kind == "attempt-end" and event["status"] not in {"ok", "cancelled"}:
        console.print(f"[dim]{now}[/]  [yellow]{escape(event['step'])} 引擎 {event['status']}：{escape((event.get('detail') or '')[:200])}[/]")
    elif kind == "step-end":
        mark = {"succeeded": "[green]✓[/]", "cancelled": "[dim]–[/]", "blocked": "[yellow]![/]"}.get(event["status"], "[red]✗[/]")
        console.print(f"[dim]{now}[/]  {mark} {escape(event['step'])} {event['status']}（{event['duration_s']:.0f} 秒）"
                      + (f"：{escape(event['error'][:200])}" if event.get("error") and event["status"] != "succeeded" else ""))
    elif kind == "run-end":
        console.print(f"[bold]■ 运行结束：{_status(event['status'])}[/]" + (f"（{event['reason']}）" if event.get("reason") else ""))
        for change in event.get("isolation") or []:
            console.print(f"  [yellow]运行期间工作区有改动：{escape(change)}[/]")


def _runner(ws: Workspace) -> Runner:
    runner = Runner(ws, engines(), on_event=_printer)

    def stop(signum, frame):
        if runner.cancel.is_set():
            raise KeyboardInterrupt
        console.print("[yellow]正在停止：结束引擎进程，已完成的步骤会保留，之后可以续跑[/]")
        runner.cancel.set()

    signal.signal(signal.SIGINT, stop)
    return runner


def _after_run(ws: Workspace, run_id: str, status: str) -> None:
    run = ws.db.get_run(run_id)
    if status == "blocked":
        report = next((a for a in reversed(ws.db.artifacts(run_id, artifact_type="blocking-report"))), None)
        if report:
            content = json.loads((ws.root / report["path"]).read_text(encoding="utf-8"))["content"]
            console.print(f"[yellow]需要你处理（{content['reason']}）[/]：")
            for need in content["needed"]:
                console.print(f"  - {escape(need)}")
            console.print(f"处理后续跑：{escape(content['resume'])}")
    elif status == "failed":
        console.print(f"[red]失败原因[/]：{escape(run['error'] or '')}")
        console.print(f"查看详情：sma runs show {run_id}；修好后续跑：sma runs resume {run_id}")
    elif status == "cancelled":
        console.print(f"已停止。续跑：sma runs resume {run_id}")
    else:
        console.print(f"查看结果：sma runs show {run_id}")


@app.command("run")
def run_command(task: str = typer.Argument(..., help="本人的一句话任务"), workflow: str = typer.Option("ideate", "--workflow", "-w"),
                account: Path = ACCOUNT) -> None:
    """一句话开工：按流程自主跑完，中途不找你确认。"""
    ws = _workspace(account)
    runner = _runner(ws)
    run_id = runner.start(workflow, task)
    status = runner.execute(run_id)
    _after_run(ws, run_id, status)
    raise typer.Exit(0 if status == "succeeded" else 1)


@runs_app.command("list")
def runs_list(limit: int = typer.Option(20, "--limit", "-n"), account: Path = ACCOUNT) -> None:
    """最近的运行。"""
    ws = _workspace(account)
    table = Table("运行", "状态", "任务", "开始", "结束")
    for run in ws.db.list_runs(limit):
        table.add_row(run["id"], _status(run["status"]), escape(run["task"][:40]), run["created_at"][5:16].replace("T", " "),
                      (run["finished_at"] or "")[5:16].replace("T", " "))
    console.print(table)


@runs_app.command("show")
def runs_show(run_id: str, step: str = typer.Option(None, "--step", "-s", help="只看一个步骤"), account: Path = ACCOUNT) -> None:
    """一次运行的步骤、耗时、用量和产物。"""
    ws = _workspace(account)
    run = ws.db.get_run(run_id)
    if run is None:
        console.print(f"[red]没有这次运行：{run_id}[/]")
        raise typer.Exit(2)
    if step:
        _show_step(ws, run_id, step)
        return
    console.print(f"[bold]{run['id']}[/] · {run['workflow']} v{run['workflow_version']} · {_status(run['status'])} · {run['mode']}")
    console.print(f"任务：{escape(run['task'])}")
    if run["blocked_reason"] or run["error"]:
        console.print(f"[yellow]{escape(run['blocked_reason'] or '')} {escape(run['error'] or '')}[/]")
    table = Table("步骤", "状态", "尝试", "引擎 · 型号", "耗时", "用量", "产物")
    total: dict = {}
    snapshot = ws.run_dir(run_id) / "workflow.yaml"
    order = [i.id for i in expand(parse_workflow(snapshot.read_text(encoding="utf-8")))] if snapshot.is_file() else []
    rows = sorted(ws.db.get_steps(run_id), key=lambda r: order.index(r["step_id"]) if r["step_id"] in order else len(order))
    for row in rows:
        artifacts = ws.db.artifacts(run_id, row["step_id"])
        for key, value in (row["usage"] or {}).items():
            total[key] = total.get(key, 0) + value
        engine = " · ".join(x for x in [row["engine"], row["model"]] if x) or "-"
        table.add_row(escape(row["step_id"]), _status(row["status"]), str(row["attempts"]), escape(engine),
                      f"{row['duration_s']:.0f}s" if row["duration_s"] is not None else "-", _tokens(row["usage"]), str(len(artifacts)))
    console.print(table)
    console.print(f"用量合计：输入 {total.get('input_tokens', 0):,}（缓存 {total.get('cached_input_tokens', 0):,}） · "
                  f"输出 {total.get('output_tokens', 0):,}（推理 {total.get('reasoning_output_tokens', 0):,}）")
    for check in ws.db.checks(f"run:{run_id}")[-2:]:
        console.print(f"检查 {check['name']}：{check['result']} · {escape(check['summary'] or '')}")
    console.print(f"目录：{ws.run_dir(run_id)}")


def _show_step(ws: Workspace, run_id: str, step_id: str) -> None:
    row = ws.db.get_step(run_id, step_id)
    if row is None:
        console.print(f"[red]这次运行里没有步骤 {escape(step_id)}[/]")
        raise typer.Exit(2)
    console.print(f"[bold]{escape(step_id)}[/] · {row['agent']} {row['agent_version'] or ''} · {_status(row['status'])}")
    if row["error"]:
        console.print(f"[yellow]{escape(row['error'])}[/]")
    table = Table("尝试", "状态", "问题", "用量", "目录")
    for attempt in ws.db.list_attempts(run_id, step_id):
        table.add_row(str(attempt["n"]), attempt["status"], str(len(attempt["problems"] or [])), _tokens(attempt["usage"]), escape(attempt["dir"]))
    console.print(table)
    attempts = ws.db.list_attempts(run_id, step_id)
    if attempts and attempts[-1]["problems"]:
        console.print("最后一次的校验问题：")
        for problem in attempts[-1]["problems"][:10]:
            console.print(f"  - {escape(problem)}")
    for artifact in ws.db.artifacts(run_id, step_id):
        console.print(f"产物 {artifact['type']}：{escape(artifact['path'])}")


@runs_app.command("resume")
def runs_resume(run_id: str, account: Path = ACCOUNT) -> None:
    """续跑：已成功的步骤按哈希复用，其余的重跑。"""
    ws = _workspace(account)
    runner = _runner(ws)
    try:
        status = runner.resume(run_id)
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    _after_run(ws, run_id, status)
    raise typer.Exit(0 if status == "succeeded" else 1)


# ---- 包 ----
@package_app.command("list")
def package_list() -> None:
    """仓库里的 Agent 包。"""
    for agent_id in list_packages():
        console.print(agent_id)


@package_app.command("lint")
def package_lint(agent_id: str = typer.Argument(None, help="包 id；不写就检查全部")) -> None:
    """检查包的字段、文件、技能和评测集。"""
    ok = True
    for pid in [agent_id] if agent_id else list_packages():
        errors, warnings = lint_package(pid)
        ok &= not errors
        console.print(f"{MARK[False if errors else (None if warnings else True)]} {pid}")
        for e in errors:
            console.print(f"  [red]{escape(e)}[/]")
        for w in warnings:
            console.print(f"  [yellow]{escape(w)}[/]")
    raise typer.Exit(0 if ok else 1)


# ---- 评测 ----
def _eval_printer(event: dict) -> None:
    kind = event["kind"]
    if kind == "eval-start":
        console.print(f"[bold]▶ 评测 {event['batch']}[/]：{event['agent']} {event['version']} · {event['engine']} · "
                      f"{event['model'] or '引擎默认型号'} · {len(event['cases'])} 个用例")
    elif kind == "attempt-end" and event["status"] != "ok":
        console.print(f"  [yellow]{event['case']} 第 {event['attempt']} 次：{event['status']}（{len(event['problems'])} 个问题）[/]")
    elif kind == "case-end":
        console.print(f"{MARK[event['passed']]} {event['case']} · {event['status']} · 尝试 {event['attempts']} 次 · {event['duration_s']:.0f} 秒")
        for name, (ok, detail) in event["checks"].items():
            console.print(f"    {MARK[ok]} {name}：{escape(detail)}")
        if event.get("error"):
            console.print(f"    [red]{escape(event['error'][:300])}[/]")


@eval_app.command("agent")
def eval_agent(agent_id: str, case: list[str] = typer.Option([], "--case", "-c", help="只跑这些用例，可写多次"),
               engine: str = typer.Option(None, "--engine"), model: str = typer.Option(None, "--model", "-m"),
               parallel: int = typer.Option(2, "--parallel", help="同时跑几个用例")) -> None:
    """跑一个包的评测集。"""
    available = engines()
    batch = run_agent_eval(agent_id, available, engine=engine, model=model, cases=case or None, parallel=parallel, on_event=_eval_printer)
    rows = eval_db().evals(batch)
    passed = sum(r["passed"] for r in rows)
    console.print(f"[bold]{passed}/{len(rows)} 通过[/] · 批次 {batch}")
    raise typer.Exit(0 if passed == len(rows) else 1)


@eval_app.command("list")
def eval_list(limit: int = typer.Option(20, "--limit", "-n")) -> None:
    """最近的评测批次。"""
    table = Table("批次", "包", "版本", "引擎 · 型号", "通过", "时间")
    for b in eval_db().eval_batches(limit):
        table.add_row(b["batch"], b["suite"], b["agent_version"] or "", " · ".join(x for x in [b["engine"], b["model"]] if x),
                      f"{b['passed']}/{b['cases']}", b["at"][5:16].replace("T", " "))
    console.print(table)


@eval_app.command("compare")
def eval_compare(batch_a: str, batch_b: str) -> None:
    """两次评测逐条对比。"""
    table = Table("用例", batch_a, batch_b, "第一次就合格", "结果变了的检查")
    show = {True: "通过", False: "[red]不通过[/]", None: "-"}
    for row in compare_batches(batch_a, batch_b):
        table.add_row(row["case"], show[row["a"]], show[row["b"]], f"{show[row['a_first_pass']]} → {show[row['b_first_pass']]}",
                      "、".join(row["changed"]) or "-")
    console.print(table)


# ---- 检查 ----
@app.command("doctor")
def doctor(account: Path = ACCOUNT) -> None:
    """检查环境、引擎登录、包和技能；在账号工作区里运行时也检查账号。"""
    ok = _items(check_environment(engines()))
    root = account
    if root is None:
        try:
            root = Workspace.find(Path.cwd()).root
        except WorkspaceError:
            root = None
    if root is not None:
        console.print("[bold]账号[/]")
        ok &= _items(check_account(root))
    raise typer.Exit(0 if ok else 1)
