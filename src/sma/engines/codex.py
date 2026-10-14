"""Codex CLI 引擎（设计 04 第 4 节，参数依据 docs/spikes/codex-engine.md 的实测）。

要点：
- SMA 用自己的 CODEX_HOME，里面只有指向用户 auth.json 的软链接：登录可用，全局 AGENTS.md 和记忆不带入；
- --ignore-user-config、--ephemeral：不读用户配置、不留会话；
- 关掉自动技能清单（技能内容由编排层写进 AGENTS.md）；
- MCP 工具必须设 default_tools_approval_mode="approve"，否则非交互运行时调用被拒；
- 提供方连不上时 Codex 会一直重连，这里自己计时并识别"等待网络"事件尽早失败。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from sma.engines.base import EngineStatus, McpServer, StepRequest, StepResult
from sma.paths import user_config_dir

KNOWN_PATHS = ["/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"]
NETWORK_TO_WEB_SEARCH = {"none": "disabled", "tools-only": "disabled", "web": "cached"}
SANDBOX = {"read-only": "read-only", "step-write": "workspace-write"}
RECONNECT_LIMIT = 4
AUTH_HINTS = ("401", "unauthorized", "not logged in", "codex login", "authentication", "refresh token", "token expired")


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def locate_codex() -> str | None:
    env = os.environ.get("SMA_CODEX_BIN")
    if env:
        return env
    found = shutil.which("codex")
    if found:
        return found
    return next((p for p in KNOWN_PATHS if Path(p).is_file()), None)


class CodexEngine:
    name = "codex"

    def __init__(self, binary: str | None = None, home: Path | None = None, user_home: Path | None = None):
        self.binary = binary or locate_codex()
        self.home = home or (user_config_dir() / "codex-home")
        self.user_home = user_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self._version: str | None = None

    # ---- 准备 ----
    def ensure_home(self) -> None:
        """建立 SMA 的 Codex 主目录：只放一个指向用户登录文件的软链接，不复制凭据。"""
        self.home.mkdir(parents=True, exist_ok=True)
        link = self.home / "auth.json"
        if not link.exists() and not link.is_symlink():
            link.symlink_to(self.user_home / "auth.json")

    def _env(self) -> dict[str, str]:
        return {**os.environ, "CODEX_HOME": str(self.home)}

    def version(self) -> str | None:
        if self._version is None and self.binary:
            try:
                out = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
                self._version = out.split()[-1] if out else None
            except (OSError, subprocess.TimeoutExpired):
                self._version = None
        return self._version

    def check(self) -> EngineStatus:
        if not self.binary:
            return EngineStatus(False, detail="找不到 codex 可执行文件：装好 Codex CLI，或用环境变量 SMA_CODEX_BIN 指定")
        version = self.version()
        if not (self.user_home / "auth.json").exists():
            return EngineStatus(True, version, False, f"没有找到 Codex 登录文件 {self.user_home / 'auth.json'}：先运行 codex login")
        self.ensure_home()
        try:
            status = subprocess.run([self.binary, "login", "status"], capture_output=True, text=True, timeout=30, env=self._env())
            text = (status.stdout + status.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return EngineStatus(True, version, None, f"无法检查登录：{exc}")
        logged_in = status.returncode == 0 and "logged in" in text.lower()
        return EngineStatus(True, version, logged_in, text.splitlines()[0] if text else "")

    # ---- 组装命令 ----
    def command(self, request: StepRequest) -> list[str]:
        if not self.binary:
            raise RuntimeError("找不到 codex 可执行文件")
        cmd = [self.binary, "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "--json",
               "--output-schema", str(request.output_schema), "-o", str(request.attempt_dir / "output.json"),
               "-C", str(request.attempt_dir), "-s", SANDBOX[request.sandbox],
               "-c", "skills.include_instructions=false",
               "-c", f"web_search={_toml_str(NETWORK_TO_WEB_SEARCH[request.network])}"]
        if request.model:
            cmd += ["-m", request.model]
        if request.reasoning:
            cmd += ["-c", f"model_reasoning_effort={_toml_str(request.reasoning)}"]
        for server in request.mcp_servers:
            cmd += self._mcp_args(server)
        for image in request.images:
            cmd += ["-i", str(image)]
        cmd.append(request.prompt)
        return cmd

    @staticmethod
    def _mcp_args(server: McpServer) -> list[str]:
        prefix = f"mcp_servers.{server.name}"
        args = ["-c", f"{prefix}.command={_toml_str(server.command)}", "-c", f"{prefix}.args={_toml_list(server.args)}",
                "-c", f'{prefix}.default_tools_approval_mode="approve"']
        if server.enabled_tools:
            args += ["-c", f"{prefix}.enabled_tools={_toml_list(server.enabled_tools)}"]
        for key, value in server.env.items():
            args += ["-c", f"{prefix}.env.{key}={_toml_str(value)}"]
        return args

    # ---- 运行 ----
    def run(self, request: StepRequest, cancel: threading.Event | None = None) -> StepResult:
        self.ensure_home()
        events_path = request.attempt_dir / "events.jsonl"
        stderr_path = request.attempt_dir / "stderr.log"
        start = time.time()
        try:
            cmd = self.command(request)
        except RuntimeError as exc:
            return StepResult("engine_error", None, self.name, 0.0, error=str(exc))
        with open(events_path, "w", encoding="utf-8") as out, open(stderr_path, "w", encoding="utf-8") as err:
            # 单独的进程组：终端里按 Ctrl-C 只打断 SMA，由 SMA 负责结束引擎进程
            proc = subprocess.Popen(cmd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=self._env(), start_new_session=True)
            status, reason = self._wait(proc, events_path, request.timeout_s, cancel, start)
        duration = time.time() - start
        usage, failure = self._scan_events(events_path)
        output = None
        output_path = request.attempt_dir / "output.json"
        if output_path.is_file():
            try:
                output = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                output = None
                failure = failure or "最终回复不是合法的 JSON"
        if status == "ok" and (proc.returncode != 0 or output is None):
            status = "engine_error"
            reason = failure or self._tail(stderr_path) or f"codex 退出码 {proc.returncode}"
        if status == "engine_error" and reason and any(h in reason.lower() for h in AUTH_HINTS):
            status = "auth_error"
        return StepResult(status, output if status == "ok" else None, self.name, duration, events_path, usage,
                          self.version(), request.model, None if status == "ok" else reason)

    def _wait(self, proc: subprocess.Popen, events_path: Path, timeout_s: int, cancel: threading.Event | None, start: float) -> tuple[str, str | None]:
        while True:
            try:
                proc.wait(timeout=0.5)
                return "ok", None
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                self._kill(proc)
                return "cancelled", "已取消"
            if time.time() - start > timeout_s:
                self._kill(proc)
                return "timeout", f"超过 {timeout_s} 秒没有完成"
            if self._reconnecting(events_path):
                self._kill(proc)
                return "engine_error", "连不上模型提供方（Codex 在反复等待网络）"

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    @staticmethod
    def _reconnecting(events_path: Path) -> bool:
        try:
            lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
        recent = [line for line in lines[-RECONNECT_LIMIT:] if '"type":"error"' in line and "Reconnecting" in line]
        return len(recent) >= RECONNECT_LIMIT

    @staticmethod
    def _scan_events(events_path: Path) -> tuple[dict | None, str | None]:
        usage: dict[str, float] = {}
        failure: str | None = None
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "turn.completed" and isinstance(event.get("usage"), dict):
                for key, value in event["usage"].items():
                    if isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + value
            elif kind == "turn.failed":
                failure = str((event.get("error") or {}).get("message", "turn.failed"))[:2000]
            elif kind == "error" and not failure and "Reconnecting" not in str(event.get("message", "")):
                failure = str(event.get("message"))[:2000]
        return usage or None, failure

    @staticmethod
    def _tail(path: Path) -> str:
        text = path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else ""
        lines = [line for line in text.splitlines() if line and "Reading additional input from stdin" not in line]
        return "\n".join(lines[-5:])
