"""Codex CLI 引擎实测（设计 04 第 10 节 T1–T9）。

用法：python scripts/spikes/codex_spike.py <结果目录> [--only A,B,...] [--codex <可执行文件>]
每个实测在结果目录下有自己的子目录：命令、事件流、错误输出、最终回复和摘要。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
MODEL = "gpt-6.1-sol"
BASE = ["exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "--json", "-c", 'model_reasoning_effort="low"']


def strict(properties: dict) -> dict:
    """严格模式的对象 Schema：所有字段必填，不允许多余字段。"""
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def run(codex: str, name: str, out: Path, args: list[str], prompt: str, schema: dict | None, cwd_files: dict[str, str] | None = None, env: dict | None = None, timeout: int = 600) -> dict:
    case = out / name
    work = case / "work"
    work.mkdir(parents=True, exist_ok=True)
    for rel, text in (cwd_files or {}).items():
        (work / rel).write_text(text, encoding="utf-8")
    cmd = [codex, *BASE, "-C", str(work), "-o", str(case / "output.json"), *args]
    if schema is not None:
        (case / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")
        cmd += ["--output-schema", str(case / "schema.json")]
    cmd.append(prompt)
    (case / "cmd.txt").write_text(" ".join(cmd), encoding="utf-8")
    start = time.time()
    with open(case / "events.jsonl", "w", encoding="utf-8") as events, open(case / "stderr.log", "w", encoding="utf-8") as errors:
        try:
            proc = subprocess.run(cmd, stdout=events, stderr=errors, timeout=timeout, env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    summary = {"name": name, "exit": code, "seconds": round(time.time() - start, 1), "event_types": {}, "output": None}
    for line in (case / "events.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type") or event.get("msg", {}).get("type") or "?"
        summary["event_types"][kind] = summary["event_types"].get(kind, 0) + 1
        for key in ("usage", "token_usage", "info"):
            if isinstance(event.get(key), dict):
                summary.setdefault("usage_samples", []).append(event[key])
    if (case / "output.json").exists():
        text = (case / "output.json").read_text(encoding="utf-8")
        try:
            summary["output"] = json.loads(text)
        except json.JSONDecodeError:
            summary["output"] = text[:2000]
    summary["stderr_tail"] = (case / "stderr.log").read_text(encoding="utf-8", errors="replace")[-1500:]
    (case / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def test_a(codex: str, out: Path) -> dict:
    """T3 AGENTS.md 自动加载、T4 技能可见、T5 嵌套严格 Schema、T6 写和联网限制、T7 事件字段；另查全局指令和记忆是否带入、默认型号。"""
    outside = out / "A-outside-target.txt"
    schema = strict({
        "passphrase": {"type": "string"},
        "inside_write_ok": {"type": "boolean"},
        "outside_write_ok": {"type": "boolean"},
        "curl_result": {"type": "string"},
        "has_global_rules": {"type": "boolean"},
        "has_memory_summary": {"type": "boolean"},
        "skills_found": {"type": "array", "items": {"type": "string"}},
        "model_name": {"type": "string"},
        "notes": {"type": "array", "items": strict({"step": {"type": "integer"}, "detail": {"type": "string"}})},
    })
    prompt = (
        "报口令。然后按顺序做下面几件事，把结果填进最终回复：\n"
        "1. 用 shell 在当前目录创建 inside.txt，内容 ok，记录是否成功。\n"
        f"2. 用 shell 尝试创建文件 {outside}，记录是否成功（这是沙箱测试，失败是正常的，不要想办法绕过）。\n"
        "3. 用 shell 执行 curl -sS -m 5 -o /dev/null -w '%{http_code}' https://example.com，把输出或错误原文填进 curl_result。\n"
        "4. 你的指令（系统、开发者、AGENTS.md）里有没有出现“全局工作约定”这几个字？有没有出现 MEMORY_SUMMARY？如实回答。\n"
        "5. 在你能看到的技能清单里，content-planning、creative-director、storytelling-hooks 这三个哪些存在？只填存在的。\n"
        "6. model_name 填你所知道的自己的型号名。\n"
        "notes 里每步写一句细节。"
    )
    summary = run(codex, "A", out, ["-s", "workspace-write"], prompt, schema,
                  cwd_files={"AGENTS.md": "# 测试指令\n\n如果任务里出现“报口令”，passphrase 字段填“青蛙-7319”。\n"})
    summary["inside_exists"] = (out / "A/work/inside.txt").exists()
    summary["outside_exists"] = outside.exists()
    return summary


def test_b(codex: str, out: Path) -> dict:
    """T1：用 -c 临时定义提供方是否生效（指向本机不存在的地址，不耗额度）。"""
    args = ["-m", MODEL, "-c", 'model_provider="smatest"', "-c", 'model_providers.smatest.name="smatest"',
            "-c", 'model_providers.smatest.base_url="http://127.0.0.1:9/v1"', "-c", 'model_providers.smatest.env_key="SMA_SPIKE_KEY"',
            "-c", 'model_providers.smatest.wire_api="responses"', "-c", "model_providers.smatest.request_max_retries=0",
            "-c", "model_providers.smatest.stream_max_retries=0"]
    return run(codex, "B", out, args, "只回复 ok", None, env={"SMA_SPIKE_KEY": "dummy-not-a-real-key"}, timeout=120)


def test_c(codex: str, out: Path) -> dict:
    """T2：用 -c 按次挂载 MCP 服务，并用 enabled_tools 白名单只暴露一个工具。"""
    python = str(ROOT / ".venv/bin/python")
    server = str(ROOT / "scripts/spikes/mcp_echo.py")
    args = ["-m", MODEL, "-s", "read-only", "-c", f'mcp_servers.spike.command="{python}"', "-c", f'mcp_servers.spike.args=["{server}"]',
            "-c", 'mcp_servers.spike.enabled_tools=["spike_echo"]']
    schema = strict({"echo_result": {"type": "string"}, "tools_seen": {"type": "array", "items": {"type": "string"}}})
    prompt = "调用 MCP 工具 spike_echo，参数 text 为 hi，把工具返回的原文填进 echo_result；tools_seen 列出你能看到的、名字里带 spike 的全部工具。"
    return run(codex, "C", out, args, prompt, schema)


def test_d(codex: str, out: Path) -> dict:
    """T8：-i 传两张图。"""
    imgs = []
    for name, color in (("red", "red"), ("blue", "blue")):
        path = out / f"D-{name}.png"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x64", "-frames:v", "1", str(path)], check=True)
        imgs += ["-i", str(path)]
    schema = strict({"colors": {"type": "array", "items": {"type": "string"}}})
    return run(codex, "D", out, ["-m", MODEL, "-s", "read-only", *imgs], "按顺序说出这几张图片的主色，用英文单词填进 colors。", schema)


def test_e(codex: str, out: Path) -> dict:
    """T9：3 个 exec 并行。"""
    schema = strict({"reply": {"type": "string"}})
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda i: run(codex, f"E{i}", out, ["-m", MODEL, "-s", "read-only"], "只回复 ok", schema), range(3)))
    return {"name": "E", "runs": [{k: r[k] for k in ("name", "exit", "seconds", "output")} for r in results]}


def test_f(codex: str, out: Path) -> dict:
    """T5 补充：非严格 Schema（有可选字段、没写 additionalProperties: false）会怎样。"""
    schema = {"type": "object", "required": ["reply"], "properties": {"reply": {"type": "string"}, "extra": {"type": "string"}}}
    return run(codex, "F", out, ["-m", MODEL, "-s", "read-only"], "只回复 ok", schema, timeout=180)


def test_g(codex: str, out: Path) -> dict:
    """T6 补充：网页搜索。默认 cached 和 live 各跑一次。"""
    schema = strict({"has_web_search": {"type": "boolean"}, "first_result_title": {"type": "string"}, "first_result_url": {"type": "string"}})
    prompt = "如果你有网页搜索工具，搜索 “IANA example domain”，填第一条结果的标题和网址；没有就把 has_web_search 填 false，其余填空字符串。"
    cached = run(codex, "G-cached", out, ["-m", MODEL, "-s", "read-only"], prompt, schema)
    live = run(codex, "G-live", out, ["-m", MODEL, "-s", "read-only", "-c", 'web_search="live"'], prompt, schema)
    return {"name": "G", "cached": {k: cached[k] for k in ("exit", "seconds", "output")}, "live": {k: live[k] for k in ("exit", "seconds", "output")}}


def isolated_home(base: Path) -> Path:
    """SMA 自己的 Codex 主目录：只有一个指向用户登录文件的软链接，不复制凭据。"""
    home = base / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    link = home / "auth.json"
    if not link.exists():
        link.symlink_to(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json")
    return home


def probe_isolation(codex: str, out: Path, name: str, extra: list[str], home: Path | None) -> dict:
    """在家目录下（不在 /tmp）建工作区，测写隔离、全局指令、技能可见性和上下文大小。"""
    base = Path.home() / ".cache" / "sma-spike" / name
    shutil.rmtree(base, ignore_errors=True)
    work = base / "ws" / "runs" / "r1" / "attempt-1"
    work.mkdir(parents=True)
    (work / "AGENTS.md").write_text("# 测试指令\n\n如果任务里出现“报口令”，passphrase 字段填“青蛙-7319”。\n", encoding="utf-8")
    sibling = base / "ws" / "outside.txt"
    far = base / "outside-far.txt"
    schema = strict({
        "passphrase": {"type": "string"},
        "sibling_write_ok": {"type": "boolean"}, "far_write_ok": {"type": "boolean"},
        "has_global_rules": {"type": "boolean"}, "has_memory_summary": {"type": "boolean"},
        "skills_found": {"type": "array", "items": {"type": "string"}}, "skill_list_size": {"type": "integer"},
    })
    prompt = (f"报口令。然后用 shell 分别尝试创建 {sibling} 和 {far}，如实记录是否成功（这是沙箱测试，失败是正常的，不要绕过）。"
              "再回答：你的指令里有没有“全局工作约定”这几个字？有没有 MEMORY_SUMMARY？"
              "你能看到的技能清单里，content-planning、creative-director、storytelling-hooks 哪些存在？skill_list_size 填技能清单里大约有几个技能，没有清单填 0。")
    cmd = [codex, *BASE, "-C", str(work), "-o", str(base / "output.json"), "--output-schema", str(base / "schema.json"), "-m", MODEL, "-s", "workspace-write", *extra, prompt]
    (base / "schema.json").write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, **({"CODEX_HOME": str(home)} if home else {})}
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, stdin=subprocess.DEVNULL)
    usage = [json.loads(line).get("usage") for line in proc.stdout.splitlines() if line.startswith("{") and '"turn.completed"' in line]
    result = {"name": name, "exit": proc.returncode, "seconds": round(time.time() - start, 1), "usage": usage,
              "sibling_exists": sibling.exists(), "far_exists": far.exists(),
              "output": json.loads((base / "output.json").read_text()) if (base / "output.json").exists() else proc.stderr[-800:]}
    (out / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def test_h(codex: str, out: Path) -> dict:
    """独立 Codex 主目录：全局 AGENTS.md 是否还在、登录是否可用、技能是否可见。"""
    return probe_isolation(codex, out, "H-home", [], isolated_home(out))


def test_i(codex: str, out: Path) -> dict:
    """用户主目录 + 关掉技能清单注入：技能是否隐藏、上下文能省多少。"""
    return probe_isolation(codex, out, "I-noskills", ["-c", "skills.include_instructions=false"], None)


def test_j(codex: str, out: Path) -> dict:
    """独立主目录 + 关掉技能清单：最小上下文。"""
    return probe_isolation(codex, out, "J-minimal", ["-c", "skills.include_instructions=false"], isolated_home(out))


def test_c2(codex: str, out: Path) -> dict:
    """T2 重测：在独立主目录下按次挂载 MCP，并用白名单只暴露一个工具。"""
    python = str(ROOT / ".venv/bin/python")
    server = str(ROOT / "scripts/spikes/mcp_echo.py")
    args = ["-m", MODEL, "-s", "read-only", "-c", f'mcp_servers.spike.command="{python}"', "-c", f'mcp_servers.spike.args=["{server}"]',
            "-c", 'mcp_servers.spike.enabled_tools=["spike_echo"]']
    schema = strict({"echo_result": {"type": "string"}, "tools_seen": {"type": "array", "items": {"type": "string"}}})
    prompt = "调用 MCP 工具 spike_echo，参数 text 为 hi，把工具返回的原文填进 echo_result；tools_seen 列出你能看到的、名字里带 spike 的全部工具。"
    home = isolated_home(out)
    os.environ["CODEX_HOME"], saved = str(home), os.environ.get("CODEX_HOME")
    try:
        return run(codex, "C2", out, args, prompt, schema)
    finally:
        if saved is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = saved


TESTS = {"A": test_a, "B": test_b, "C": test_c, "D": test_d, "E": test_e, "F": test_f, "G": test_g,
         "H": test_h, "I": test_i, "J": test_j, "C2": test_c2}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--only", default=",".join(TESTS))
    parser.add_argument("--codex", default=shutil.which("codex") or DEFAULT_CODEX)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = {}
    for name in args.only.split(","):
        print(f"== 实测 {name}", flush=True)
        results[name] = TESTS[name](args.codex, args.out)
        print(json.dumps(results[name], ensure_ascii=False)[:1500], flush=True)
    (args.out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
