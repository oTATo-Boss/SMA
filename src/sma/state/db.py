"""状态库：每个账号工作区一个 SQLite（设计 06 第 5 节）。

WAL 模式，每个线程一个连接，写操作用 BEGIN IMMEDIATE；表结构用 PRAGMA user_version 迁移。
大文件只存路径和哈希。
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from sma.timeutil import stamp

MIGRATIONS: list[str] = [
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY, workflow TEXT NOT NULL, workflow_version INTEGER NOT NULL, task TEXT NOT NULL,
        mode TEXT NOT NULL, status TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}', blocked_reason TEXT, error TEXT,
        created_at TEXT NOT NULL, finished_at TEXT
    );
    CREATE TABLE steps (
        run_id TEXT NOT NULL, step_id TEXT NOT NULL, kind TEXT NOT NULL, agent TEXT, agent_version TEXT,
        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, engine TEXT, model TEXT, params TEXT NOT NULL DEFAULT '{}',
        started_at TEXT, finished_at TEXT, duration_s REAL, usage TEXT, error TEXT,
        PRIMARY KEY (run_id, step_id)
    );
    CREATE TABLE attempts (
        run_id TEXT NOT NULL, step_id TEXT NOT NULL, n INTEGER NOT NULL, dir TEXT NOT NULL, status TEXT NOT NULL,
        exit TEXT, problems TEXT, started_at TEXT NOT NULL, finished_at TEXT, usage TEXT,
        PRIMARY KEY (run_id, step_id, n)
    );
    CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL, type TEXT NOT NULL, schema_version INTEGER NOT NULL,
        version INTEGER NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX artifacts_by_step ON artifacts (run_id, step_id);
    CREATE TABLE confirmations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, object TEXT NOT NULL, sha256 TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL
    );
    CREATE TABLE checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, object TEXT NOT NULL, name TEXT NOT NULL, result TEXT NOT NULL,
        evidence_sha256 TEXT, summary TEXT, at TEXT NOT NULL
    );
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
        external_id TEXT, status TEXT NOT NULL, record TEXT, run_id TEXT, step_id TEXT, submitted_at TEXT NOT NULL
    );
    CREATE TABLE deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, files TEXT NOT NULL, run_id TEXT, at TEXT NOT NULL
    );
    CREATE TABLE evals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT NOT NULL, suite TEXT NOT NULL, case_id TEXT NOT NULL,
        agent TEXT, agent_version TEXT, engine TEXT, model TEXT, passed INTEGER NOT NULL, details TEXT, at TEXT NOT NULL
    );
    """,
]

JSON_FIELDS = {"params", "usage", "problems", "files", "details"}


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    for key in JSON_FIELDS & data.keys():
        if isinstance(data[key], str):
            with contextlib.suppress(json.JSONDecodeError):
                data[key] = json.loads(data[key])
    return data


def _dump(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


class StateDB:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._migrate()

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def tx(self):
        conn = self.conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def _migrate(self) -> None:
        conn = self.conn()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript("BEGIN;" + script + f"PRAGMA user_version={number};COMMIT;")

    @property
    def schema_version(self) -> int:
        return self.conn().execute("PRAGMA user_version").fetchone()[0]

    # ---- 运行 ----
    def create_run(self, run_id: str, workflow: str, workflow_version: int, task: str, mode: str, params: dict) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO runs (id, workflow, workflow_version, task, mode, status, params, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (run_id, workflow, workflow_version, task, mode, "created", _dump(params), stamp()))

    def set_run_status(self, run_id: str, status: str, blocked_reason: str | None = None, error: str | None = None) -> None:
        finished = stamp() if status in {"succeeded", "failed", "cancelled", "blocked"} else None
        with self.tx() as c:
            c.execute("UPDATE runs SET status=?, blocked_reason=?, error=?, finished_at=? WHERE id=?",
                      (status, blocked_reason, error, finished, run_id))

    def get_run(self, run_id: str) -> dict | None:
        return _row(self.conn().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self, limit: int = 20) -> list[dict]:
        rows = self.conn().execute("SELECT * FROM runs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]

    # ---- 步骤 ----
    def upsert_step(self, run_id: str, step_id: str, **fields) -> None:
        fields = {k: _dump(v) for k, v in fields.items()}
        with self.tx() as c:
            exists = c.execute("SELECT 1 FROM steps WHERE run_id=? AND step_id=?", (run_id, step_id)).fetchone()
            if exists:
                if fields:
                    sets = ", ".join(f"{k}=?" for k in fields)
                    c.execute(f"UPDATE steps SET {sets} WHERE run_id=? AND step_id=?", (*fields.values(), run_id, step_id))
            else:
                fields.setdefault("kind", "agent")
                fields.setdefault("status", "pending")
                cols = ", ".join(["run_id", "step_id", *fields])
                marks = ", ".join("?" * (len(fields) + 2))
                c.execute(f"INSERT INTO steps ({cols}) VALUES ({marks})", (run_id, step_id, *fields.values()))

    def get_step(self, run_id: str, step_id: str) -> dict | None:
        return _row(self.conn().execute("SELECT * FROM steps WHERE run_id=? AND step_id=?", (run_id, step_id)).fetchone())

    def get_steps(self, run_id: str) -> list[dict]:
        rows = self.conn().execute("SELECT * FROM steps WHERE run_id=? ORDER BY COALESCE(started_at, '9'), step_id", (run_id,)).fetchall()
        return [_row(r) for r in rows]

    # ---- 尝试 ----
    def add_attempt(self, run_id: str, step_id: str, n: int, directory: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO attempts (run_id, step_id, n, dir, status, started_at) VALUES (?,?,?,?,?,?)",
                      (run_id, step_id, n, directory, "running", stamp()))

    def finish_attempt(self, run_id: str, step_id: str, n: int, status: str, exit: str | None, problems: list[str], usage: dict | None) -> None:
        with self.tx() as c:
            c.execute("UPDATE attempts SET status=?, exit=?, problems=?, usage=?, finished_at=? WHERE run_id=? AND step_id=? AND n=?",
                      (status, exit, _dump(problems), _dump(usage) if usage else None, stamp(), run_id, step_id, n))

    def list_attempts(self, run_id: str, step_id: str) -> list[dict]:
        rows = self.conn().execute("SELECT * FROM attempts WHERE run_id=? AND step_id=? ORDER BY n", (run_id, step_id)).fetchall()
        return [_row(r) for r in rows]

    # ---- 产物 ----
    def add_artifact(self, artifact_id: str, run_id: str, step_id: str, artifact_type: str, schema_version: int, version: int,
                     path: str, sha256: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO artifacts (id, run_id, step_id, type, schema_version, version, path, sha256, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (artifact_id, run_id, step_id, artifact_type, schema_version, version, path, sha256, stamp()))

    def artifacts(self, run_id: str, step_id: str | None = None, artifact_type: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM artifacts WHERE run_id=?", [run_id]
        if step_id is not None:
            sql, args = sql + " AND step_id=?", [*args, step_id]
        if artifact_type is not None:
            sql, args = sql + " AND type=?", [*args, artifact_type]
        rows = self.conn().execute(sql + " ORDER BY step_id, id", args).fetchall()
        return [_row(r) for r in rows]

    def delete_artifacts(self, run_id: str, artifact_type: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM artifacts WHERE run_id=? AND type=?", (run_id, artifact_type))

    def delete_step_artifacts(self, run_id: str, step_id: str) -> None:
        """重跑一个步骤前，撤下它旧的登记（文件保留在旧的尝试目录里）。"""
        with self.tx() as c:
            c.execute("DELETE FROM artifacts WHERE run_id=? AND step_id=?", (run_id, step_id))

    # ---- 确认与检查 ----
    def add_confirmation(self, obj: str, sha256: str, note: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO confirmations (object, sha256, note, at) VALUES (?,?,?,?)", (obj, sha256, note, stamp()))

    def latest_confirmation(self, obj: str) -> dict | None:
        return _row(self.conn().execute("SELECT * FROM confirmations WHERE object=? ORDER BY id DESC LIMIT 1", (obj,)).fetchone())

    def add_check(self, obj: str, name: str, result: str, summary: str, evidence_sha256: str | None = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO checks (object, name, result, evidence_sha256, summary, at) VALUES (?,?,?,?,?,?)",
                      (obj, name, result, evidence_sha256, summary, stamp()))

    def checks(self, obj: str) -> list[dict]:
        return [_row(r) for r in self.conn().execute("SELECT * FROM checks WHERE object=? ORDER BY id", (obj,)).fetchall()]

    # ---- 评测 ----
    def add_eval(self, batch: str, suite: str, case_id: str, passed: bool, details: dict, agent: str | None = None,
                 agent_version: str | None = None, engine: str | None = None, model: str | None = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO evals (batch, suite, case_id, agent, agent_version, engine, model, passed, details, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (batch, suite, case_id, agent, agent_version, engine, model, int(passed), _dump(details), stamp()))

    def evals(self, batch: str) -> list[dict]:
        rows = self.conn().execute("SELECT * FROM evals WHERE batch=? ORDER BY case_id", (batch,)).fetchall()
        return [_row(r) for r in rows]

    def eval_batches(self, limit: int = 20) -> list[dict]:
        rows = self.conn().execute("SELECT batch, suite, agent_version, engine, model, COUNT(*) AS cases, SUM(passed) AS passed, MIN(at) AS at "
                                   "FROM evals GROUP BY batch ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]
