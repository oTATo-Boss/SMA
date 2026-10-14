"""Codex 适配：命令组装、主目录、事件解析；用一个假的 codex 可执行文件测进程处理，不调用真实模型。"""

from __future__ import annotations

import json
import stat
import sys
import textwrap

from sma.engines.base import McpServer, StepRequest
from sma.engines.codex import CodexEngine

FAKE_CODEX = """
import json, subprocess, sys, time
args = sys.argv[1:]
mode = {mode!r}
if args[:1] == ["--version"]:
    print("codex-cli 0.0.0-test")
    sys.exit(0)
if args[:2] == ["login", "status"]:
    print("Not logged in" if mode == "auth" else "Logged in using ChatGPT")
    sys.exit(1 if mode == "auth" else 0)
out = args[args.index("-o") + 1]
if mode == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(out + ".child", "w").write(str(child.pid))
if mode in ("slow", "child"):
    time.sleep(30)
if mode == "auth":
    print(json.dumps({{"type": "turn.failed", "error": {{"message": "401 Unauthorized: please run codex login"}}}}))
    sys.exit(1)
if mode == "flaky":
    print(json.dumps({{"type": "turn.failed", "error": {{"message": "500 internal error, request id req_7a401f9c"}}}}))
    sys.exit(1)
print(json.dumps({{"type": "thread.started"}}))
print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 7}}}}))
open(out, "w").write(json.dumps({{"ok": True}}))
"""


def fake_binary(tmp_path, mode="ok"):
    path = tmp_path / f"codex-{mode}"
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_CODEX.format(mode=mode)), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def engine(tmp_path, binary="codex") -> CodexEngine:
    user_home = tmp_path / "user-codex"
    user_home.mkdir(exist_ok=True)
    (user_home / "auth.json").write_text("{}", encoding="utf-8")
    return CodexEngine(binary=binary, home=tmp_path / "sma-codex-home", user_home=user_home)


def request(tmp_path, **kw) -> StepRequest:
    attempt = tmp_path / "attempt-1"
    attempt.mkdir(exist_ok=True)
    (attempt / "schema.json").write_text("{}", encoding="utf-8")
    base = dict(label="s", attempt_dir=attempt, prompt="任务", output_schema=attempt / "schema.json", sandbox="read-only", network="none")
    return StepRequest(**{**base, **kw})


def test_command_flags(tmp_path):
    cmd = engine(tmp_path).command(request(tmp_path, network="web", model="m1", reasoning="high", sandbox="step-write",
                                            mcp_servers=[McpServer("sma-research", "python3", ["-m", "x"], ["search"], {"A": "1"})]))
    joined = " ".join(cmd)
    for flag in ["exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "--json", "--output-schema",
                 "skills.include_instructions=false", 'web_search="cached"', "-m m1", 'model_reasoning_effort="high"', "-s workspace-write",
                 'mcp_servers.sma-research.default_tools_approval_mode="approve"', 'mcp_servers.sma-research.enabled_tools=["search"]']:
        assert flag in joined, flag
    assert cmd[-1] == "任务"
    for feature in ["apps", "plugins", "browser_use", "computer_use", "multi_agent", "memories"]:
        assert f"features.{feature}=false" in joined, feature
    assert 'web_search="disabled"' in " ".join(engine(tmp_path).command(request(tmp_path)))


def test_home_links_auth_instead_of_copying(tmp_path):
    e = engine(tmp_path)
    e.ensure_home()
    link = e.home / "auth.json"
    assert link.is_symlink() and link.resolve() == (e.user_home / "auth.json").resolve()


def test_scan_events_sums_usage_and_finds_failure(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(json.dumps(e) for e in [
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"type": "error", "message": "Reconnecting... 1/5"},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 1}},
        {"type": "turn.failed", "error": {"message": "boom"}},
    ]) + "\nnot json\n", encoding="utf-8")
    usage, failure, seen = CodexEngine._scan_events(events)
    assert usage == {"input_tokens": 15, "output_tokens": 3} and failure == "boom" and seen == ""


def test_scan_events_collects_what_the_model_saw(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
        {"type": "item.completed", "item": {"type": "web_search", "query": "猫 店长", "results": [
            {"type": "text_result", "url": "https://www.bilibili.com/video/BV1abcdef/", "title": "猫店长", "snippet": "第一集"}]}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "cat a", "aggregated_output": "资料里的链接 https://example.com/x"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "https://made.up/1"}},
    ]) + "\n", encoding="utf-8")
    _, _, seen = CodexEngine._scan_events(events)
    assert "BV1abcdef" in seen and "https://example.com/x" in seen and "made.up" not in seen


def test_run_ok_with_fake_binary(tmp_path):
    result = engine(tmp_path, fake_binary(tmp_path)).run(request(tmp_path))
    assert result.status == "ok" and result.output == {"ok": True}
    assert result.usage == {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 7}


def test_run_auth_failure_is_reported(tmp_path):
    result = engine(tmp_path, fake_binary(tmp_path, "auth")).run(request(tmp_path))
    assert result.status == "auth_error" and "401" in result.error


def test_run_timeout_kills_process(tmp_path):
    result = engine(tmp_path, fake_binary(tmp_path, "slow")).run(request(tmp_path, timeout_s=1))
    assert result.status == "timeout" and result.duration_s < 15


def test_server_error_mentioning_401_is_not_a_login_problem(tmp_path):
    result = engine(tmp_path, fake_binary(tmp_path, "flaky")).run(request(tmp_path))
    assert result.status == "engine_error" and "req_7a401f9c" in result.error


def test_cancel_kills_the_whole_process_group(tmp_path):
    import os
    import threading
    import time
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    req = request(tmp_path)
    result = engine(tmp_path, fake_binary(tmp_path, "child")).run(req, cancel)
    assert result.status == "cancelled"
    pid = int((req.attempt_dir / "output.json.child").read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("引擎起的子进程没有被结束")
