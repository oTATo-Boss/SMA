from __future__ import annotations

from pathlib import Path

import pytest
from helpers import viral_ref

from sma.state.db import MIGRATIONS, StateDB
from sma.workspace import registry
from sma.workspace.workspace import Workspace, WorkspaceError, normalize_url


def test_init_creates_layout(workspace):
    for d in ["strategy", "library/viral", "memory", "data", "runs", "交付", "发布内容", "固定资产", ".sma"]:
        assert (workspace.root / d).is_dir()
    assert workspace.config.account.platforms == ["抖音"]
    assert (workspace.root / ".sma" / "state.db").is_file()


def test_init_refuses_non_empty_or_existing(tmp_path, workspace):
    with pytest.raises(WorkspaceError, match="已经是账号工作区"):
        Workspace.init(workspace.root, "again")
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "笔记.md").write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="已有文件"):
        Workspace.init(busy, "busy")


def test_find_from_subdirectory(workspace):
    assert Workspace.find(workspace.root / "strategy").root == workspace.root


def test_confirmation_is_bound_to_file_hash(workspace):
    assert workspace.positioning_confirmed()
    (workspace.root / "strategy" / "positioning.md").write_text("改了", encoding="utf-8")
    assert not workspace.positioning_confirmed()
    workspace.confirm_positioning("改后的也确认")
    assert workspace.positioning_confirmed()
    with pytest.raises(WorkspaceError):
        workspace.confirm_positioning("  ")


def test_library_dedupes_by_url(workspace):
    first = workspace.add_library_entries([viral_ref("r1", "https://example.com/v/1?utm_source=a"), viral_ref("r2", "https://example.com/v/2")], "run/a")
    second = workspace.add_library_entries([viral_ref("r1", "https://EXAMPLE.com/v/1/"), viral_ref("r3", "https://example.com/v/3")], "run/b")
    assert first["r1"] == second["r1"]
    ids = sorted({*first.values(), *second.values()})
    assert len(ids) == 3 and all(Workspace.is_library_id(i) and i.startswith("dy-") for i in ids)
    entry = next(e for e in workspace.library_entries() if e["id"] == first["r1"])
    assert entry["source"] == "run/a" and "key" not in entry
    assert workspace.has_library(first["r1"]) and not workspace.has_library("dy-20000101-99")


def test_platform_code_prefers_domain():
    from sma.workspace.workspace import platform_code
    assert platform_code("哔哩哔哩", "https://www.bilibili.com/video/BV1/") == "bili"
    assert platform_code("某平台", "https://v.douyin.com/abc") == "dy"
    assert platform_code("B 站") == "bili" and platform_code("没听过的平台") == "web"


def test_normalize_url_keeps_identity_params():
    assert normalize_url("HTTPS://Example.com/a/?utm_source=x&share_from=y&spm_id_from=z") == "https://example.com/a"
    assert normalize_url("https://www.youtube.com/watch?v=AAA&si=x") != normalize_url("https://www.youtube.com/watch?v=BBB&si=x")
    assert normalize_url("https://www.douyin.com/user/u?modal_id=1&from=s") == "https://www.douyin.com/user/u?modal_id=1"


def test_different_videos_with_query_ids_stay_separate(workspace):
    ids = workspace.add_library_entries([viral_ref("r1", "https://www.youtube.com/watch?v=AAAAAAAAAAA", platform="YouTube"),
                                         viral_ref("r2", "https://www.youtube.com/watch?v=BBBBBBBBBBB", platform="YouTube")], "run/a")
    assert ids["r1"] != ids["r2"] and all(i.startswith("yt-") for i in ids.values())


def test_stray_library_files_do_not_break_ingest(workspace):
    from sma.timeutil import now
    (workspace.library_dir() / f"dy-{now():%Y%m%d}-01 copy.json").write_text("{}", encoding="utf-8")
    ids = workspace.add_library_entries([viral_ref("r1", "https://example.com/v/9")], "run/a")
    assert ids["r1"].endswith("-01")


def test_concurrent_ingest_from_two_processes(workspace):
    import subprocess
    import sys
    script = (
        "import sys; sys.path.insert(0, sys.argv[2]); from pathlib import Path; from helpers import viral_ref;"
        "from sma.workspace.workspace import Workspace; ws = Workspace(Path(sys.argv[1]));"
        "ws.add_library_entries([viral_ref(f'r{i}', f'https://example.com/{sys.argv[3]}/{i}') for i in range(40)], 'p')"
    )
    tests_dir = str(Path(__file__).parent)
    procs = [subprocess.Popen([sys.executable, "-c", script, str(workspace.root), tests_dir, name]) for name in ("a", "b")]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    assert len(list(workspace.library_dir().glob("*.json"))) == 80


def test_data_paths_stay_inside_workspace(workspace):
    (workspace.root / "data" / "后台.csv").write_text("a,b", encoding="utf-8")
    assert workspace.has_data("后台.csv")
    assert not workspace.has_data("../sma.yaml")
    assert not workspace.has_data("坏\x00名字.csv")


def test_symlinked_data_dir(tmp_path, workspace):
    real = tmp_path / "真实资料"
    real.mkdir()
    (real / "a.csv").write_text("1", encoding="utf-8")
    (workspace.root / "data").rmdir()
    (workspace.root / "data").symlink_to(real)
    assert workspace.has_data("a.csv")


def test_registry_lists_other_accounts(tmp_path, workspace):
    other = Workspace.init(tmp_path / "@另一个号", "另一个号")
    registry.register(other.name, other.root)
    names = [a["name"] for a in registry.other_accounts(workspace.root)]
    assert names == ["另一个号"]


def test_db_migrations_run_once(tmp_path):
    db = StateDB(tmp_path / "s.db")
    assert db.schema_version == len(MIGRATIONS)
    db.create_run("r-1", "ideate", 1, "任务", "full", {"a": 1})
    again = StateDB(tmp_path / "s.db")
    assert again.get_run("r-1")["params"] == {"a": 1}


def test_steps_upsert_and_json_fields(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.create_run("r-1", "ideate", 1, "任务", "full", {})
    db.upsert_step("r-1", "research[viral]", params={"lane": {"id": "viral"}})
    db.upsert_step("r-1", "research[viral]", status="succeeded", usage={"input_tokens": 3})
    row = db.get_step("r-1", "research[viral]")
    assert row["status"] == "succeeded" and row["usage"] == {"input_tokens": 3} and row["params"]["lane"]["id"] == "viral"
    db.set_run_status("r-1", "failed", error="原因")
    assert db.get_run("r-1")["error"] == "原因" and db.get_run("r-1")["finished_at"]
