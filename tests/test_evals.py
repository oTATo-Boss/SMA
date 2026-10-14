from __future__ import annotations

import json

from helpers import candidates_output

from sma.engines.fake import FakeEngine
from sma.evals.checks import CHECKS, CaseContext
from sma.evals.runner import case_inputs, compare_batches, eval_db, load_cases, run_agent_eval
from sma.packages.loader import load_package


def cd_output(core="阿福", refs=("lib:dy-20261010-01",), why="近期同类 18 倍", feasibility="5 个镜头", extra=""):
    output = candidates_output("cross-domain", list(refs), core=core)
    for c in output["idea_candidates"]["candidates"]:
        c["why"], c["feasibility"], c["one_liner"] = why, feasibility, c["one_liner"] + extra
        c["borrowed_mechanism"] = c["borrowed_mechanism"] + c["id"]
    return output


def run_check(name, output, **expect):
    return CHECKS[name](output, CaseContext(expect, {"dy-20261010-01"}, {"https://example.com/dy/1"}))


def test_checks():
    good = cd_output()
    assert run_check("cites_evidence", good)[0]
    assert not run_check("cites_evidence", cd_output(refs=()))[0]
    assert run_check("distinct_mechanisms", good)[0]
    assert run_check("keeps_core_asset", good, core_terms=["阿福"])[0]
    assert not run_check("keeps_core_asset", cd_output(core="柯基"), core_terms=["阿福"])[0]
    assert not run_check("no_signature_copy", cd_output(extra="团团来送餐啦"), banned_terms=["团团"])[0]
    assert run_check("refs_within_inputs", good)[0]
    assert not run_check("refs_within_inputs", cd_output(refs=("lib:dy-20991231-01",)))[0]
    assert run_check("refs_within_inputs", cd_output(refs=("https://example.com/dy/1",)))[0]
    assert run_check("acknowledges_gap", cd_output(why="依据不足，属于假设"))[0]
    assert not run_check("acknowledges_gap", good)[0]
    assert run_check("flags_positioning_change", cd_output(feasibility="换核心资产要先改定位"))[0]
    research = {"viral_references": [{"key": "r1", "url": "https://a.com/1", "media_reviewed": True}],
                "opportunity_brief": {"opportunities": [], "gaps": []}}
    assert not run_check("media_honest", research)[0] and not run_check("gaps_reported", research)[0]


def test_case_inputs_from_fixtures():
    package = load_package("creative-director")
    cases = {c["id"]: c for c in load_cases(package)}
    assert set(cases) == {"cd-001", "cd-002", "cd-003", "cd-004"}
    files, lib_ids, urls = case_inputs(package, cases["cd-001"])
    assert {f.name for f in files} == {"strategy-positioning.md", "fixture.opportunity-brief.json", "fixture.viral-reference.json"}
    assert "dy-20261010-01" in lib_ids and any("example.com" in u for u in urls)
    assert "lib:<key>" in next(f.description for f in files if f.name == "fixture.viral-reference.json")


def test_run_agent_eval_records_results():
    def responder(request, n):
        refs = json.loads((request.attempt_dir / "inputs" / "fixture.viral-reference.json").read_text(encoding="utf-8"))
        return cd_output(refs=[f"lib:{refs[0]['key']}"])

    engine = FakeEngine(responder)
    batch = run_agent_eval("creative-director", {"codex": engine}, cases=["cd-001"], retry_delay_s=0)
    rows = eval_db().evals(batch)
    assert len(rows) == 1 and rows[0]["passed"] == 1
    details = rows[0]["details"]
    assert details["first_pass"] and set(details["checks"]) == {"cites_evidence", "distinct_mechanisms", "keeps_core_asset"}

    failing = run_agent_eval("creative-director", {"codex": FakeEngine(lambda r, n: cd_output(core="柯基", refs=()))},
                             cases=["cd-001"], retry_delay_s=0)
    assert eval_db().evals(failing)[0]["passed"] == 0
    row = compare_batches(batch, failing)[0]
    assert row["a"] is True and row["b"] is False and "keeps_core_asset" in row["changed"]
