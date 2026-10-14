"""假引擎：按步骤返回预设输出，不调用模型。给编排层、评测的测试用。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable

from sma.engines.base import EngineStatus, StepRequest, StepResult

Responder = Callable[[StepRequest, int], dict | Exception | None]


class FakeEngine:
    """responder(request, 第几次调用该步骤) 返回输出字典；返回异常表示引擎出错；返回 None 表示没有输出。"""

    name = "fake"

    def __init__(self, responder: Responder, delay_s: float = 0.0):
        self.responder = responder
        self.delay_s = delay_s
        self.calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def check(self) -> EngineStatus:
        return EngineStatus(available=True, version="fake", logged_in=True)

    def run(self, request: StepRequest, cancel: threading.Event | None = None) -> StepResult:
        with self._lock:
            self.calls[request.label] = self.calls.get(request.label, 0) + 1
            count = self.calls[request.label]
        start = time.time()
        deadline = start + self.delay_s
        while time.time() < deadline:
            if cancel is not None and cancel.is_set():
                return StepResult("cancelled", None, self.name, time.time() - start, error="已取消")
            time.sleep(0.01)
        events = request.attempt_dir / "events.jsonl"
        events.write_text(json.dumps({"type": "fake.call", "label": request.label, "n": count}) + "\n", encoding="utf-8")
        reply = self.responder(request, count)
        if isinstance(reply, Exception):
            return StepResult("engine_error", None, self.name, time.time() - start, events, error=str(reply))
        if reply is not None:
            (request.attempt_dir / "output.json").write_text(json.dumps(reply, ensure_ascii=False), encoding="utf-8")
        usage = {"input_tokens": 0, "output_tokens": 0}
        return StepResult("ok", reply, self.name, time.time() - start, events, usage, "fake", request.model)
