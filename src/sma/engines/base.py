"""引擎接口（设计 04 第 2 节）：每个步骤交给一个现成的 Agent 引擎执行一次。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class McpServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    enabled_tools: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class StepRequest:
    label: str                      # 步骤实例 id，例如 research[viral]
    attempt_dir: Path               # 引擎的工作目录
    prompt: str
    output_schema: Path
    sandbox: str                    # read-only / step-write
    network: str                    # none / tools-only / web
    model: str | None = None
    reasoning: str | None = None
    mcp_servers: list[McpServer] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    timeout_s: int = 900


@dataclass
class StepResult:
    status: str                     # ok / engine_error / auth_error / timeout / cancelled
    output: dict | None
    engine: str
    duration_s: float
    events_path: Path | None = None
    usage: dict | None = None
    engine_version: str | None = None
    model: str | None = None
    error: str | None = None


@dataclass
class EngineStatus:
    available: bool
    version: str | None = None
    logged_in: bool | None = None
    detail: str = ""


class Engine(Protocol):
    name: str

    def check(self) -> EngineStatus: ...

    def run(self, request: StepRequest, cancel: threading.Event | None = None) -> StepResult: ...
