from __future__ import annotations

import shutil

import pytest

from sma.contracts.schemas import output_schema, strict_problems
from sma.engines.routing import resolve
from sma.orchestrator.context import MissingSkill, agents_md
from sma.orchestrator.workflow import expand, load_workflow, parse_workflow, resolve_choice
from sma.packages import skills
from sma.packages.loader import lint_package, list_packages, load_package
from sma.paths import REPO_ROOT
from sma.workspace.config import AccountConfig, AccountInfo, ModelChoice, Models


def test_repo_packages_load_and_lint():
    assert {"market-intel", "creative-director"} <= set(list_packages())
    for agent_id in list_packages():
        errors, _ = lint_package(agent_id)
        assert errors == [], (agent_id, errors)
        package = load_package(agent_id)
        assert strict_problems(output_schema(package.output_specs)) == []


def test_lint_reports_missing_section_and_schema(tmp_path, monkeypatch):
    root = tmp_path / "res"
    shutil.copytree(REPO_ROOT / "schemas", root / "schemas")
    shutil.copytree(REPO_ROOT / "agents" / "creative-director", root / "agents" / "creative-director")
    package = root / "agents" / "creative-director"
    (package / "instructions.md").write_text("# 只有标题\n\n## 你是谁\n", encoding="utf-8")
    text = (package / "agent.yaml").read_text(encoding="utf-8").replace("type: idea-candidates", "type: no-such-type")
    (package / "agent.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setenv("SMA_RESOURCE_ROOT", str(root))
    errors, _ = lint_package("creative-director")
    assert any("怎么做" in e for e in errors) and any("no-such-type" in e for e in errors)


def write_skill(root, directory, name, body="技能正文"):
    path = root / directory / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\ndescription: x\n---\n\n{body}\n", encoding="utf-8")
    return path


def test_skills_found_by_name_and_injected(tmp_path):
    roots = tmp_path / "skills"
    write_skill(roots, "social/creative-director", "creative-director", "SIT 方法")
    write_skill(roots, "video/hooks-dir", "storytelling-hooks", "六个杠杆")
    skills.clear_cache()
    assert skills.find_skill("creative-director").parent.name == "creative-director"
    assert skills.find_skill("STORYTELLING-HOOKS") is not None
    text = agents_md(load_package("creative-director"), "测试", "已验证：短开头更好")
    assert "SIT 方法" in text and "六个杠杆" in text and "已验证：短开头更好" in text
    assert text.index("## 规则") < text.index("## 角色指令") < text.index("## 参考：knowledge/创意方法.md")


def test_required_skill_missing_raises():
    package = load_package("creative-director")
    package.spec.skills[0].required = True
    with pytest.raises(MissingSkill):
        agents_md(package, "测试", None)


def test_routing_order():
    package = load_package("creative-director")
    user = Models(tiers={"strong": {"codex": "user-model"}})
    assert resolve(package, None, ["codex"], user).model == "user-model"
    config = AccountConfig(account=AccountInfo(name="x"), models=Models(tiers={"strong": {"codex": "account-model"}}))
    assert resolve(package, config, ["codex"], user).model == "account-model"
    config.models.agents["creative-director"] = ModelChoice(model="pinned", reasoning="low")
    route = resolve(package, config, ["codex"], user)
    assert (route.model, route.reasoning) == ("pinned", "low")
    with pytest.raises(RuntimeError):
        resolve(package, None, ["claude"])


def test_ideate_workflow_expands():
    workflow = load_workflow("ideate")
    static = [i.id for i in expand(workflow)]
    assert static[0] == "plan" and not any(i.startswith(("research", "ideation")) for i in static)   # 等规划结果
    lanes = resolve_choice(workflow, "plan.research_lanes", [{"id": "viral", "focus": "温暖的"}])
    methods = resolve_choice(workflow, "plan.ideation_methods", None)
    instances = expand(workflow, {"plan.research_lanes": lanes, "plan.ideation_methods": methods})
    assert [i.id for i in instances] == ["plan", "research[viral]", "merge", "ideation[mechanism-borrow]", "ideation[cross-domain]",
                                         "ideation[original]", "blind", "review", "select", "rework", "direct", "rationale"]
    assert instances[1].params["lane"] == {"id": "viral", "name": "本赛道爆款", "focus": "温暖的"}
    assert instances[3].params["candidates_range"] == [3, 4]
    assert resolve_choice(workflow, "plan.research_lanes", [{"id": "不存在"}]) == resolve_choice(workflow, "plan.research_lanes", None)
    assert {"merge", "ideation", "blind", "review", "select", "rework", "direct", "rationale"} <= workflow.downstream("research")


@pytest.mark.parametrize("text, message", [
    ("id: w\nversion: 1\nsteps:\n  a: {agent: x, needs: [b]}\n", "不存在"),
    ("id: w\nversion: 1\nsteps:\n  a: {agent: x}\n  b: {agent: y, inputs: [{from: a, type: t}]}\n", "needs"),
    ("id: w\nversion: 1\nsteps:\n  a: {code: merge_research}\n", "outputs"),
    ("id: w\nversion: 1\nsteps:\n  a: {agent: x, min_success: 2}\n", "min_success"),
    ("id: w\nversion: 1\nsteps:\n  a: {agent: x, inputs: [{from: workspace, type: opportunity-brief}]}\n", "工作区输入"),
])
def test_invalid_workflows(text, message):
    with pytest.raises(ValueError, match=message):
        parse_workflow(text)
