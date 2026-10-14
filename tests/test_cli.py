from __future__ import annotations

import pytest
from helpers import POSITIONING, default_responder
from typer.testing import CliRunner

from sma.cli import main as cli
from sma.engines.fake import FakeEngine

runner = CliRunner()


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    monkeypatch.setattr(cli.console, "width", 200)


def test_account_strategy_run_show(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "engines", lambda: {"codex": FakeEngine(default_responder)})
    root = tmp_path / "@演示号"
    result = runner.invoke(cli.app, ["account", "init", str(root), "-p", "抖音"])
    assert result.exit_code == 0, result.output
    assert "演示号" in result.output and (root / "sma.yaml").is_file()

    result = runner.invoke(cli.app, ["run", "今天做一条视频", "--account", str(root)])
    assert result.exit_code == 1 and "positioning-unconfirmed" in result.output

    (root / "strategy" / "positioning.md").write_text(POSITIONING, encoding="utf-8")
    result = runner.invoke(cli.app, ["strategy", "confirm", "--note", "就这样", "--account", str(root)])
    assert result.exit_code == 0 and "已记录确认" in result.output
    assert "已确认" in runner.invoke(cli.app, ["strategy", "status", "--account", str(root)]).output

    result = runner.invoke(cli.app, ["run", "今天做一条视频", "--account", str(root)])
    assert result.exit_code == 0, result.output
    assert "succeeded" in result.output

    listing = runner.invoke(cli.app, ["runs", "list", "--account", str(root)])
    run_id = next(line.split()[1] for line in listing.output.splitlines() if "succeeded" in line)
    shown = runner.invoke(cli.app, ["runs", "show", run_id, "--account", str(root)])
    assert shown.exit_code == 0 and "ideation[original]" in shown.output and "用量合计" in shown.output
    step = runner.invoke(cli.app, ["runs", "show", run_id, "--step", "merge", "--account", str(root)])
    assert "opportunity-brief" in step.output

    accounts = runner.invoke(cli.app, ["account", "list"])
    assert "演示号" in accounts.output


def test_package_lint_and_doctor(monkeypatch):
    monkeypatch.setattr(cli, "engines", lambda: {"codex": FakeEngine(default_responder)})
    assert runner.invoke(cli.app, ["package", "lint"]).exit_code == 0
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "包 creative-director" in result.output and "流程 ideate" in result.output
