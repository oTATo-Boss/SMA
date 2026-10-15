"""过线检查（设计 03 第 2、5 节）：步骤成功后由代码决定往哪走。

每个 gate 按名字注册，收到这一步的产出和输入，返回一个决定：
- proceed：继续往下；
- block：阻塞，交给本人（例如任务其实是在调整方向）；
- rework：先运行一个 routed 步骤（例如返工选中的候选），它成功后从 rerun_from 起重跑；
- rerun：从 rerun_from 起重跑（例如带着评审意见重出一轮）。
返工和重出都算一轮，轮数上限由流程或账号配置给出，用完就阻塞。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class GateContext:
    """gate 只看这一步自己交出的产物，所以续跑时也能重新判断。"""

    run_id: str
    step: str
    outputs: dict[str, list[dict]]          # 这一步交出的产物：类型 → 内容
    rounds: int                             # 这个 gate 已经路由过几轮
    max_rounds: int
    params: dict = field(default_factory=dict)


@dataclass
class GateDecision:
    action: Literal["proceed", "block", "rework", "rerun"]
    reason: str = ""                        # 阻塞原因
    needed: list[str] = field(default_factory=list)
    detail: str = ""
    routed_step: str | None = None          # rework：先运行哪个 routed 步骤
    rerun_from: str | None = None           # rework 之后、或 rerun 时从哪一步起重跑
    state: dict = field(default_factory=dict)   # 要记进运行状态的信息，例如返工的对象
    note: str = ""                          # 记进检查记录的一句话


Gate = Callable[[GateContext], GateDecision]
GATES: dict[str, Gate] = {}


def gate(name: str):
    def register(fn: Gate) -> Gate:
        GATES[name] = fn
        return fn

    return register


@gate("plan_intent")
def plan_intent(ctx: GateContext) -> GateDecision:
    """规划判断这句任务不是内容任务时，创意流程不启动（AI-SMA 停更账号测试的教训：方向先复述，等本人确认）。"""
    plan = ctx.outputs["run-plan"][0]
    if plan["intent"] == "content":
        return GateDecision("proceed", note="内容任务")
    if plan["intent"] == "direction-change":
        r = plan["restatement"] or {"keep": [], "drop": [], "add": [], "questions": []}
        needed = ["这句任务涉及调整账号方向，创意流程没有启动。请先确认下面的复述："]
        needed += [f"保留：{x}" for x in r["keep"]] + [f"放弃：{x}" for x in r["drop"]] + [f"新增：{x}" for x in r["add"]]
        needed += [f"请回答：{q}" for q in r["questions"]]
        needed.append('确认后改 strategy/positioning.md，运行 sma strategy confirm --note "<原话>"，再重新下任务')
        return GateDecision("block", "direction-change", needed, plan["intent_reason"], note="方向调整，没有启动创意流程")
    advice = plan["notes"] or "这不是一条内容任务；ideate 流程只做内容"
    return GateDecision("block", "not-content-task", [advice], plan["intent_reason"], note="不是内容任务")


@gate("review_loop")
def review_loop(ctx: GateContext) -> GateDecision:
    """评审结论：pass 继续；revise 返工选中的那个再评；reject 带着意见重出一轮；轮数用完阻塞。"""
    selected = ctx.outputs["selected-idea"][0]
    review = selected["review"]
    verdict = review["verdict"]
    if verdict == "pass":
        return GateDecision("proceed", note=f"过线：{selected['candidate']['one_liner']}")
    if ctx.rounds >= ctx.max_rounds:
        needed = [f"评审 {ctx.rounds + 1} 轮仍然没有过线（最后一轮的结论：{verdict}）。评审意见：{review['summary']}"]
        needed += [f"要改：{x}" for x in review["required_changes"]]
        needed.append(f"目前最好的是：{selected['candidate']['one_liner']}（{selected['review']['total']} 分）")
        needed.append("可以补充资料、调整定位后续跑；也可以换一句任务重新开始")
        return GateDecision("block", "rounds-exhausted", needed, review["summary"], note="轮数用完")
    if verdict == "revise":
        return GateDecision("rework", routed_step=ctx.params["rework"], rerun_from=ctx.params["after_rework"],
                            state={"rework_target": selected["blind_id"]}, note=f"返工 {selected['blind_id']}")
    return GateDecision("rerun", rerun_from=ctx.params["reject_from"], note="都不行，带着评审意见重出一轮")
