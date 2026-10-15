"""编排层：全部用假引擎，不调用模型。"""

from __future__ import annotations

import json
import threading
import time

import pytest
from helpers import candidates_output, default_responder, lib_keys, method_of, research_output

from sma.contracts.schemas import envelope_schema
from sma.contracts.validate import schema_errors
from sma.engines.base import EngineStatus
from sma.engines.fake import FakeEngine
from sma.orchestrator.runner import Runner
from sma.workspace import registry
from sma.workspace.workspace import Workspace


def runner(ws: Workspace, responder=default_responder, **kw) -> tuple[Runner, FakeEngine]:
    engine = FakeEngine(responder, delay_s=kw.pop("delay_s", 0.0))
    return Runner(ws, {"codex": engine}, retry_delay_s=0.0, **kw), engine


def steps(ws: Workspace, run_id: str) -> dict[str, dict]:
    return {row["step_id"]: row for row in ws.db.get_steps(run_id)}


def read_artifact(ws: Workspace, run_id: str, step_id: str, artifact_type: str) -> dict:
    record = ws.db.artifacts(run_id, step_id, artifact_type)[-1]
    return json.loads((ws.root / record["path"]).read_text(encoding="utf-8"))["content"]


def test_happy_path(workspace):
    r, engine = runner(workspace)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded"
    rows = steps(workspace, run_id)
    assert {k: v["status"] for k, v in rows.items()} == {
        "plan": "succeeded", "research[viral]": "succeeded", "research[adjacent]": "succeeded", "merge": "succeeded",
        "ideation[mechanism-borrow]": "succeeded", "ideation[cross-domain]": "succeeded", "ideation[original]": "succeeded",
        "blind": "succeeded", "review": "succeeded", "select": "succeeded", "rework": "skipped", "direct": "succeeded",
        "rationale": "succeeded"}
    assert engine.calls == {"plan": 1, "research[viral]": 1, "research[adjacent]": 1, "ideation[mechanism-borrow]": 1,
                            "ideation[cross-domain]": 1, "ideation[original]": 1, "review": 1, "direct": 1}

    # 爆款条目入库并按链接去重：两路各 1 条独有 + 1 条共同 = 3 条
    library = workspace.library_entries()
    assert len(library) == 3
    merged = workspace.db.artifacts(run_id, "merge", "opportunity-brief")[0]
    brief = json.loads((workspace.root / merged["path"]).read_text(encoding="utf-8"))
    refs = [ref for o in brief["content"]["opportunities"] for ref in o["evidence_refs"]]
    assert refs and all(ref.startswith("lib:") and workspace.has_library(ref[4:]) for ref in refs)
    topics = [o["topic"] for o in brief["content"]["opportunities"]]
    assert topics.count("共同机会") == 1 and len(topics) == 3
    assert sorted(brief["content"]["gaps"]) == ["adjacent 看不到播放量", "viral 看不到播放量"]

    # 每个产物都有合格的信封，输入带哈希
    for record in workspace.db.artifacts(run_id):
        envelope = json.loads((workspace.root / record["path"]).read_text(encoding="utf-8"))
        assert schema_errors(envelope_schema(), envelope) == []
    candidates = workspace.db.artifacts(run_id, "ideation[original]", "idea-candidates")[0]
    envelope = json.loads((workspace.root / candidates["path"]).read_text(encoding="utf-8"))
    assert envelope["producer"]["agent"] == "creative-director" and envelope["id"].endswith("/ideation-original/idea-candidates.v1")
    assert any(i["ref"] == "workspace:strategy/positioning.md" for i in envelope["inputs"])
    assert any(i["ref"].endswith("/merge/opportunity-brief.v1") for i in envelope["inputs"])

    # 尝试目录：AGENTS.md 有策略摘要和角色指令；任务正文有原话和参数；输入只放声明的
    attempt = workspace.run_dir(run_id) / "steps" / "ideation-original" / "attempt-1"
    agents = (attempt / "AGENTS.md").read_text(encoding="utf-8")
    assert "## 规则（SMA 统一下发" in agents and "# 创意总监" in agents and "不能联网" in agents
    prompt = (attempt / "prompt.md").read_text(encoding="utf-8")
    assert "今天做一条视频" in prompt and '"original"' in prompt
    assert sorted(p.name for p in (attempt / "inputs").iterdir()) == [
        "merge.opportunity-brief.json", "merge.viral-reference.json",
        "strategy-content-rules.md", "strategy-facts.md", "strategy-positioning.md"]
    checks = {c["name"]: c["result"] for c in workspace.db.checks(f"run:{run_id}")}
    assert checks == {"precheck": "pass", "gate:plan": "proceed", "gate:select": "proceed", "workspace-isolation": "pass"}

    # 评审是盲评：输入里没有方法标签，代号重编过；选定后解开盲化；依据卡写好了
    blinded = read_artifact(workspace, run_id, "blind", "blinded-candidates")
    assert all(c["id"].startswith("X") for c in blinded["candidates"]) and len(blinded["candidates"]) == 9
    review_inputs = workspace.run_dir(run_id) / "steps" / "review" / "attempt-1" / "inputs"
    assert "method" not in (review_inputs / "blind.blinded-candidates.json").read_text(encoding="utf-8")
    selected = read_artifact(workspace, run_id, "select", "selected-idea")
    assert selected["blind_id"] == "X1" and selected["candidate"]["id"].startswith("C") and not selected["explore"]
    rationale = read_artifact(workspace, run_id, "rationale", "delivery-rationale")
    assert rationale["pick"]["one_liner"] == selected["candidate"]["one_liner"] and rationale["independence"] == "same-family"
    card = (workspace.run_dir(run_id) / "依据卡.md").read_text(encoding="utf-8")
    assert selected["candidate"]["one_liner"] in card and "同家族评审" in card


def test_invalid_output_is_repaired(workspace):
    def responder(request, n):
        if request.label == "ideation[cross-domain]" and n == 1:
            return candidates_output("cross-domain", lib_keys(request), count=2)
        return default_responder(request, n)

    r, engine = runner(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded" and engine.calls["ideation[cross-domain]"] == 2
    attempts = workspace.db.list_attempts(run_id, "ideation[cross-domain]")
    assert [a["status"] for a in attempts] == ["invalid", "ok"]
    second = workspace.root / attempts[1]["dir"] / "prompt.md"
    assert "上一次的输出没有通过校验" in second.read_text(encoding="utf-8")


def test_links_must_have_been_seen(workspace):
    """引擎报告了看到的内容时，引用没出现过的链接要修复重试。"""

    class SeeingEngine(FakeEngine):
        def run(self, request, cancel=None):
            result = super().run(request, cancel)
            result.seen_text = "搜索结果：https://example.com/v/viral-1 https://example.com/v/adjacent-1 https://example.com/v/shared"
            return result

    def responder(request, n):
        output = default_responder(request, n)
        if request.label == "research[viral]" and n == 1:
            output["opportunity_brief"]["opportunities"][0]["evidence_refs"].append("https://made.up/video/123456789")
        return output

    engine = SeeingEngine(responder)
    r = Runner(workspace, {"codex": engine}, retry_delay_s=0)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded" and engine.calls["research[viral]"] == 2
    problems = workspace.db.list_attempts(run_id, "research[viral]")[0]["problems"]
    assert any("made.up" in p for p in problems)


def test_one_ideation_lane_may_fail(workspace):
    def responder(request, n):
        if request.label == "ideation[original]":
            return RuntimeError("引擎崩了")
        return default_responder(request, n)

    r, engine = runner(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded"
    assert steps(workspace, run_id)["ideation[original]"]["status"] == "failed"
    assert engine.calls["ideation[original]"] == 3


def test_all_research_failing_fails_run(workspace):
    def responder(request, n):
        if request.label.startswith("research["):
            return {"viral_references": [], "opportunity_brief": {"opportunities": [], "gaps": []}}
        return default_responder(request, n)

    r, _ = runner(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "failed" and "research" in workspace.db.get_run(run_id)["error"]
    assert steps(workspace, run_id)["merge"]["status"] == "pending"


def test_unconfirmed_positioning_blocks_then_resumes(workspace):
    (workspace.root / "strategy" / "positioning.md").write_text("改过的定位", encoding="utf-8")
    r, engine = runner(workspace)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "blocked" and engine.calls == {}
    run = workspace.db.get_run(run_id)
    assert run["blocked_reason"] == "positioning-unconfirmed"
    report = workspace.db.artifacts(run_id, "precheck", "blocking-report")[0]
    content = json.loads((workspace.root / report["path"]).read_text(encoding="utf-8"))["content"]
    assert content["reason"] == "positioning-unconfirmed" and run_id in content["resume"]

    workspace.confirm_positioning("确认")
    assert r.resume(run_id) == "succeeded"


def test_login_failure_blocks(workspace):
    class LoggedOut(FakeEngine):
        def check(self):
            return EngineStatus(True, "fake", False, "Not logged in")

    r = Runner(workspace, {"codex": LoggedOut(default_responder)}, retry_delay_s=0)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "blocked" and workspace.db.get_run(run_id)["blocked_reason"] == "needs-login"


def test_resume_reuses_succeeded_steps(workspace):
    broken = {"on": True}

    def responder(request, n):
        if request.label.startswith("ideation[") and broken["on"]:
            return candidates_output(method_of(request.label), lib_keys(request), count=1)
        return default_responder(request, n)

    r, engine = runner(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "failed"
    broken["on"] = False
    assert r.resume(run_id) == "succeeded"
    assert engine.calls["research[viral]"] == 1 and engine.calls["research[adjacent]"] == 1
    assert engine.calls["ideation[original]"] == 4
    assert [a["n"] for a in workspace.db.list_attempts(run_id, "ideation[original]")] == [1, 2, 3, 4]


def test_resume_reruns_tampered_step_and_downstream(workspace):
    r, engine = runner(workspace)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded"
    workspace.db.set_run_status(run_id, "failed", error="手动标记，测试用")
    record = workspace.db.artifacts(run_id, "research[viral]", "opportunity-brief")[0]
    path = workspace.root / record["path"]
    path.write_text(path.read_text(encoding="utf-8").replace("viral 机会", "被改过"), encoding="utf-8")
    assert r.resume(run_id) == "succeeded"
    assert engine.calls["research[viral]"] == 2 and engine.calls["research[adjacent]"] == 1
    assert engine.calls["ideation[original]"] == 2
    new = workspace.db.artifacts(run_id, "research[viral]", "opportunity-brief")[0]
    assert new["version"] == 2


def test_cancel_then_resume(workspace):
    r, _ = runner(workspace, delay_s=2.0)
    run_id = r.start("ideate", "今天做一条视频")
    timer = threading.Timer(0.3, r.cancel.set)
    timer.start()
    started = time.time()
    assert r.execute(run_id) == "cancelled"
    assert time.time() - started < 5
    assert {row["status"] for row in steps(workspace, run_id).values()} <= {"pending", "skipped"}
    r.cancel.clear()
    r2, _ = runner(workspace)
    assert r2.resume(run_id) == "succeeded"


def test_resume_after_crash(workspace):
    """进程被强行结束：运行和步骤停在 running，续跑时重跑这些步骤。"""
    r, _ = runner(workspace)
    run_id, _ = r.run("ideate", "今天做一条视频")
    workspace.db.set_run_status(run_id, "running")
    for step_id in ["ideation[original]", "ideation[cross-domain]"]:
        workspace.db.upsert_step(run_id, step_id, status="running")
    r2, engine2 = runner(workspace)
    assert r2.resume(run_id) == "succeeded"
    assert engine2.calls == {"ideation[original]": 1, "ideation[cross-domain]": 1, "review": 1, "direct": 1}


def test_parallel_limit(workspace):
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def responder(request, n):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.15)
        with lock:
            state["now"] -= 1
        return default_responder(request, n)

    workspace.config.autonomy.max_parallel_steps = 2
    r, _ = runner(workspace, responder)
    assert r.run("ideate", "今天做一条视频")[1] == "succeeded"
    assert state["max"] == 2


def test_other_account_names_trigger_repair(tmp_path, workspace):
    other = Workspace.init(tmp_path / "@隔壁的号", "隔壁的号")
    registry.register(other.name, other.root)

    def responder(request, n):
        if request.label == "ideation[original]" and n == 1:
            return candidates_output("original", lib_keys(request), core="阿福和隔壁的号")
        return default_responder(request, n)

    r, engine = runner(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded" and engine.calls["ideation[original]"] == 2
    problems = workspace.db.list_attempts(run_id, "ideation[original]")[0]["problems"]
    assert any("[隔离]" in p for p in problems) and not any("隔壁的号" in p for p in problems)


def test_secrets_are_redacted(workspace, monkeypatch):
    monkeypatch.setenv("FAKE_PROVIDER_API_KEY", "sk-test-0123456789abcdef")

    def responder(request, n):
        output = default_responder(request, n)
        if request.label == "research[viral]":
            output["opportunity_brief"]["gaps"].append("调试信息 sk-test-0123456789abcdef")
        return output

    r, _ = runner(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded"
    for path in workspace.run_dir(run_id).rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}:
            assert "sk-test-0123456789abcdef" not in path.read_text(encoding="utf-8"), path


def test_workspace_changes_are_reported(workspace):
    def responder(request, n):
        if request.label == "research[viral]":
            (workspace.root / "strategy" / "facts.md").write_text("被改了", encoding="utf-8")
        return default_responder(request, n)

    r, _ = runner(workspace, responder)
    run_id, _ = r.run("ideate", "今天做一条视频")
    check = next(c for c in workspace.db.checks(f"run:{run_id}") if c["name"] == "workspace-isolation")
    assert check["result"] == "warn" and "strategy/facts.md" in check["summary"]


@pytest.mark.parametrize("task", ["", "   "])
def test_empty_task_rejected(workspace, task):
    r, _ = runner(workspace)
    with pytest.raises(ValueError):
        r.start("ideate", task)


def test_research_output_helper_is_valid():
    from sma.contracts.schemas import OutputSpec
    from sma.contracts.validate import Context, validate_output
    assert validate_output([OutputSpec("viral-reference", True), OutputSpec("opportunity-brief")], research_output("x"), Context()) == []


def test_engine_errors_are_redacted(workspace, monkeypatch):
    monkeypatch.setenv("FAKE_PROVIDER_TOKEN", "tok-0123456789abcdef")

    def responder(request, n):
        if request.label == "research[viral]":
            return RuntimeError("调用失败 tok-0123456789abcdef")
        return default_responder(request, n)

    r, _ = runner(workspace, responder)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "succeeded"
    rows = [*workspace.db.list_attempts(run_id, "research[viral]"), workspace.db.get_step(run_id, "research[viral]")]
    text = json.dumps(rows, ensure_ascii=False)
    assert "tok-0123456789abcdef" not in text and "[已脱敏:FAKE_PROVIDER_TOKEN]" in text


def test_latest_blocking_report_wins(workspace):
    (workspace.root / "strategy" / "positioning.md").write_text("改过", encoding="utf-8")

    class Flaky(FakeEngine):
        def run(self, request, cancel=None):
            result = super().run(request, cancel)
            if request.label.startswith("ideation["):
                result.status, result.output, result.error = "auth_error", None, "401 Unauthorized"
            return result

    r = Runner(workspace, {"codex": Flaky(default_responder)}, retry_delay_s=0)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "blocked"
    workspace.confirm_positioning("确认")
    assert r.resume(run_id) == "blocked"
    reports = workspace.db.artifacts(run_id, artifact_type="blocking-report")
    assert len(reports) == 1
    content = json.loads((workspace.root / reports[0]["path"]).read_text(encoding="utf-8"))["content"]
    assert content["reason"] == "needs-login" and workspace.db.get_run(run_id)["blocked_reason"] == "needs-login"


def test_run_cannot_execute_twice_at_once(workspace):
    from sma.locks import file_lock
    from sma.orchestrator.runner import RunBusy
    r, engine = runner(workspace)
    run_id = r.start("ideate", "今天做一条视频")
    with file_lock(workspace.run_dir(run_id) / ".lock"), pytest.raises(RunBusy):
        r.execute(run_id)
    assert engine.calls == {}


def test_stray_attempt_directory_is_skipped(workspace):
    r, _ = runner(workspace)
    run_id = r.start("ideate", "今天做一条视频")
    (workspace.run_dir(run_id) / "steps" / "research-viral" / "attempt-1").mkdir(parents=True)
    assert r.execute(run_id) == "succeeded"
    assert [a["n"] for a in workspace.db.list_attempts(run_id, "research[viral]")] == [2]


def test_broken_skill_file_fails_step_with_record(tmp_path, workspace):
    from sma.packages import skills
    skill = tmp_path / "skills" / "creative-director" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: creative-director\n---\n\xff\xfe bad")
    skills.clear_cache()
    r, _ = runner(workspace)
    run_id, status = r.run("ideate", "今天做一条视频")
    assert status == "failed"
    attempts = workspace.db.list_attempts(run_id, "ideation[original]")
    assert attempts and attempts[0]["status"] == "error" and "准备步骤目录出错" in attempts[0]["exit"]
