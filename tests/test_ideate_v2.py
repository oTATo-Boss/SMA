"""ideate 第 2 版：规划、动态展开、盲评、返工和重出、轮数上限、探索名额。全部用假引擎。"""

from __future__ import annotations

import json
import threading

from helpers import candidates_output, default_responder, lib_keys, method_of, plan_output, review_output

from sma.engines.fake import FakeEngine
from sma.orchestrator.runner import Runner


def make(workspace, responder=default_responder, **kw):
    engine = FakeEngine(responder, delay_s=kw.pop("delay_s", 0.0))
    return Runner(workspace, {"codex": engine}, retry_delay_s=0.0, **kw), engine


def statuses(ws, run_id):
    return {row["step_id"]: row["status"] for row in ws.db.get_steps(run_id)}


def artifact(ws, run_id, step_id, artifact_type):
    record = ws.db.artifacts(run_id, step_id, artifact_type)[-1]
    return json.loads((ws.root / record["path"]).read_text(encoding="utf-8"))["content"]


def report(ws, run_id):
    return artifact(ws, run_id, ws.db.artifacts(run_id, artifact_type="blocking-report")[-1]["step_id"], "blocking-report")


def test_plan_chooses_lanes_and_methods(workspace):
    def responder(request, n):
        if request.label == "plan":
            return plan_output(lanes=["viral"], methods=["trend-remix", "original", "cross-domain"])
        return default_responder(request, n)

    r, engine = make(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条温暖一点的")
    assert status == "succeeded"
    assert set(engine.calls) >= {"research[viral]", "ideation[trend-remix]", "ideation[original]", "ideation[cross-domain]"}
    assert "research[adjacent]" not in engine.calls and "ideation[mechanism-borrow]" not in engine.calls
    prompt = (workspace.run_dir(run_id) / "steps" / "research-viral" / "attempt-1" / "prompt.md").read_text(encoding="utf-8")
    assert "viral 的重点" in prompt and "本赛道爆款" in prompt      # 规划写的重点和流程里的名称都给了研究


def test_failed_plan_falls_back_to_defaults(workspace):
    def responder(request, n):
        if request.label == "plan":
            return RuntimeError("规划出错")
        return default_responder(request, n)

    r, engine = make(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded" and statuses(workspace, run_id)["plan"] == "failed"
    assert {"research[viral]", "research[adjacent]", "ideation[mechanism-borrow]", "ideation[cross-domain]", "ideation[original]"} <= set(engine.calls)


def test_direction_change_stops_before_research(workspace):
    def responder(request, n):
        if request.label == "plan":
            return plan_output("direction-change")
        return default_responder(request, n)

    r, engine = make(workspace, responder)
    run_id, status = r.run("ideate", "以后不做猫了，改做职场")
    assert status == "blocked" and engine.calls == {"plan": 1}
    assert workspace.db.get_run(run_id)["blocked_reason"] == "direction-change"
    needed = report(workspace, run_id)["needed"]
    assert any("阿福这个角色还保留吗" in x for x in needed) and any("放弃：猫咪日常" in x for x in needed)
    assert r.resume(run_id) == "blocked"           # 任务没变，续跑仍然停在这里


def test_not_content_task(workspace):
    def responder(request, n):
        if request.label == "plan":
            return plan_output("other")
        return default_responder(request, n)

    r, _ = make(workspace, responder)
    run_id, status = r.run("ideate", "定位已经完善了吧？")
    assert status == "blocked" and workspace.db.get_run(run_id)["blocked_reason"] == "not-content-task"


def test_revise_reworks_the_pick_and_reviews_again(workspace):
    def responder(request, n):
        if request.label == "review" and n == 1:
            return review_output(request, verdict="revise", pick_index=2)
        return default_responder(request, n)

    r, engine = make(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded"
    assert engine.calls["rework"] == 1 and engine.calls["review"] == 2 and engine.calls["ideation[original]"] == 1
    blinded = artifact(workspace, run_id, "blind", "blinded-candidates")
    assert blinded["candidates"][2]["one_liner"] == "阿福返工后的点子" and blinded["candidates"][2]["id"] == "X3"
    rework_inputs = workspace.run_dir(run_id) / "steps" / "rework" / "attempt-1" / "inputs"
    assert json.loads((rework_inputs / "select.selected-idea.json").read_text(encoding="utf-8"))["blind_id"] == "X3"
    assert workspace.db.get_run(run_id)["params"]["rounds"] == {"select": 1}
    assert statuses(workspace, run_id)["rework"] == "succeeded"


def test_reject_reruns_ideation_with_feedback(workspace):
    def responder(request, n):
        if request.label == "review" and n == 1:
            return review_output(request, verdict="reject")
        return default_responder(request, n)

    r, engine = make(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded"
    assert engine.calls["ideation[original]"] == 2 and engine.calls["review"] == 2 and "rework" not in engine.calls
    round2 = workspace.run_dir(run_id) / "steps" / "ideation-original" / "attempt-2" / "inputs"
    feedback = json.loads((round2 / "review.idea-review.json").read_text(encoding="utf-8"))
    assert feedback["verdict"] == "reject"
    round1 = workspace.run_dir(run_id) / "steps" / "ideation-original" / "attempt-1" / "inputs"
    assert not (round1 / "review.idea-review.json").exists()


def test_rounds_are_limited_then_resume_gives_another_chance(workspace):
    verdict = {"value": "revise"}

    def responder(request, n):
        if request.label == "review":
            return review_output(request, verdict=verdict["value"])
        return default_responder(request, n)

    r, engine = make(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "blocked" and workspace.db.get_run(run_id)["blocked_reason"] == "rounds-exhausted"
    assert engine.calls["review"] == 3 and engine.calls["rework"] == 2 and "direct" not in engine.calls
    verdict["value"] = "pass"
    assert r.resume(run_id) == "succeeded"
    assert engine.calls["review"] == 4 and engine.calls["direct"] == 1


def test_explore_pick_follows_ratio(workspace):
    workspace.config.autonomy.explore_ratio = 1.0

    def responder(request, n):
        if request.label == "review":
            return review_output(request, explore=True)
        return default_responder(request, n)

    r, _ = make(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded"
    selected = artifact(workspace, run_id, "select", "selected-idea")
    assert selected["explore"] and selected["blind_id"] == "X2"
    assert "探索名额" in (workspace.run_dir(run_id) / "依据卡.md").read_text(encoding="utf-8")


def test_cancel_during_rework_then_resume(workspace):
    gate = {"cancel": None}

    def responder(request, n):
        if request.label == "review" and n == 1:
            return review_output(request, verdict="revise")
        if request.label == "rework":
            gate["cancel"].set()
            return default_responder(request, n)
        return default_responder(request, n)

    r, _ = make(workspace, responder)
    gate["cancel"] = r.cancel
    run_id = r.start("ideate", "今天做一条视频")
    assert r.execute(run_id) == "cancelled"
    r2, engine2 = make(workspace)
    assert r2.resume(run_id) == "succeeded"
    assert engine2.calls.get("rework") == 1 and engine2.calls["review"] == 1 and "plan" not in engine2.calls


def test_failed_rework_fails_the_run(workspace):
    def responder(request, n):
        if request.label == "review":
            return review_output(request, verdict="revise")
        if request.label == "rework":
            return candidates_output(method_of("ideation[original]"), lib_keys(request), count=3)   # 返工交了 3 个，不合格
        return default_responder(request, n)

    r, engine = make(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "failed" and engine.calls["rework"] == 3
    assert "rework" in workspace.db.get_run(run_id)["error"]


def test_parallel_ideation_still_limited(workspace):
    lock, state = threading.Lock(), {"now": 0, "max": 0}

    def responder(request, n):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        try:
            return default_responder(request, n)
        finally:
            with lock:
                state["now"] -= 1

    workspace.config.autonomy.max_parallel_steps = 2
    r, _ = make(workspace, responder, delay_s=0.05)
    assert r.run("ideate", "今天做一条视频")[1] == "succeeded"
    assert state["max"] <= 2
