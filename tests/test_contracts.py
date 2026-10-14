from __future__ import annotations

import json

import pytest
from helpers import candidates_output, research_output, viral_ref

from sma.contracts.envelope import Producer, make_envelope
from sma.contracts.schemas import OutputSpec, content_schema, envelope_schema, output_schema, strict_problems
from sma.contracts.validate import (
    Context,
    collect_urls,
    evidence_errors,
    hash_error,
    schema_errors,
    unseen_urls,
    validate_content,
    validate_output,
)
from sma.hashing import sha256_file
from sma.paths import schemas_dir

TYPES = sorted(p.name.removesuffix(".schema.json") for p in (schemas_dir() / "content").glob("*.schema.json"))
RESEARCH = [OutputSpec("viral-reference", True), OutputSpec("opportunity-brief")]
IDEATION = [OutputSpec("idea-candidates")]


class Resolver:
    def __init__(self, lib=(), data=()):
        self.lib, self.data = set(lib), set(data)

    def has_library(self, entry_id):
        return entry_id in self.lib

    def has_data(self, rel):
        return rel in self.data


@pytest.mark.parametrize("artifact_type", TYPES)
def test_content_schemas_are_strict(artifact_type):
    assert strict_problems(content_schema(artifact_type)) == []


def test_output_schema_combines_and_stays_strict():
    schema = output_schema(RESEARCH)
    assert schema["required"] == ["viral_references", "opportunity_brief"]
    assert schema["properties"]["viral_references"]["type"] == "array"
    assert strict_problems(schema) == []


def test_strict_problems_reports_missing_required():
    bad = {"type": "object", "properties": {"a": {"type": "string"}}, "required": []}
    problems = strict_problems(bad)
    assert any("additionalProperties" in p for p in problems) and any("required" in p for p in problems)


def test_valid_research_output_passes():
    assert validate_output(RESEARCH, research_output("viral"), Context()) == []


def test_ref_to_missing_local_entry_fails():
    output = research_output("viral")
    output["opportunity_brief"]["opportunities"][0]["evidence_refs"] = ["ref:r9"]
    problems = validate_output(RESEARCH, output, Context())
    assert any("r9" in p for p in problems)


def test_schema_error_stops_before_rules():
    output = research_output("viral")
    del output["viral_references"][0]["url"]
    problems = validate_output(RESEARCH, output, Context())
    assert problems and all(p.startswith("[Schema]") for p in problems)


def test_semantic_rules():
    ref = viral_ref("r1", "not-a-url")
    ref["observed"] = []
    problems = validate_content("viral-reference", ref)
    assert any("url" in p for p in problems) and any("observed" in p for p in problems)
    brief = {"opportunities": [], "gaps": []}
    assert any("1 到 5" in p for p in validate_content("opportunity-brief", brief))
    no_evidence = {"opportunities": [{"topic": "t", "why_now": "w", "evidence_refs": [], "audience_signal": "a", "mechanism_hints": []}], "gaps": []}
    assert any("没有依据" in p for p in validate_content("opportunity-brief", no_evidence))
    no_evidence["gaps"] = ["没拿到数据"]
    assert validate_content("opportunity-brief", no_evidence) == []


def test_idea_candidate_rules_use_step_params():
    content = candidates_output("cross-domain", ["lib:dy-20261015-01"], count=2)["idea_candidates"]
    problems = validate_content("idea-candidates", content, Context(params={"method": "original", "candidates_range": [3, 4]}))
    assert any("3 到 4" in p for p in problems) and any("method" in p for p in problems)
    content["candidates"][1]["id"] = "C1"
    assert any("重复" in p for p in validate_content("idea-candidates", content))


def test_evidence_kinds():
    ctx = Context(local_refs={"r1"}, resolver=Resolver(lib={"dy-20261015-01"}, data={"后台/9月.csv"}))
    good = ["ref:r1", "lib:dy-20261015-01", "data:后台/9月.csv", "https://example.com/a"]
    assert evidence_errors(good, ctx, "x") == []
    bad = evidence_errors(["ref:r2", "lib:dy-20261015-09", "data:没有.csv", "xhs-01"], ctx, "x")
    assert len(bad) == 4


def test_lib_refs_checked_against_library():
    output = candidates_output("original", ["lib:dy-20261015-01"])
    ok = validate_output(IDEATION, output, Context(params={"method": "original"}, resolver=Resolver(lib={"dy-20261015-01"})))
    missing = validate_output(IDEATION, output, Context(params={"method": "original"}, resolver=Resolver()))
    assert ok == [] and any("爆款库里没有" in p for p in missing)


def test_hash_binding(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("{}", encoding="utf-8")
    digest = sha256_file(path)
    assert hash_error(path, digest) is None
    path.write_text('{"x": 1}', encoding="utf-8")
    assert "改过" in hash_error(path, digest)
    assert "不存在" in hash_error(tmp_path / "none.json", digest)


def test_envelope_is_filled_by_code():
    content = research_output("viral")["opportunity_brief"]
    envelope = make_envelope("opportunity-brief", content, run_id="r-1", step_id="research[viral]", slug="research-viral", index=None,
                             version=1, producer=Producer("market-intel", "0.1.0", "codex", None), inputs=[{"ref": "x", "sha256": "0" * 64}])
    assert schema_errors(envelope_schema(), envelope) == []
    assert envelope["id"] == "r-1/research-viral/opportunity-brief.v1"
    assert envelope["evidence_refs"] == ["ref:r1", "ref:r2"]
    json.dumps(envelope, ensure_ascii=False)


def test_collect_and_check_seen_urls():
    output = research_output("viral")
    output["opportunity_brief"]["opportunities"][0]["evidence_refs"].append("https://www.douyin.com/video/7630451277697245163")
    urls = collect_urls(output)
    assert urls == ["https://example.com/v/viral-1", "https://example.com/v/shared?from=search", "https://www.douyin.com/video/7630451277697245163"]
    corpus = ('{"url": "https://example.com/v/viral-1"} 列表里看到 example.com/v/shared/ '
              '{"url": "https://www.douyin.com/user/abc?modal_id=7630451277697245163"}')
    assert unseen_urls(urls, corpus) == []
    assert unseen_urls(["https://www.douyin.com/video/7699999999999999999"], corpus) == ["https://www.douyin.com/video/7699999999999999999"]
    assert unseen_urls(["https://other.com/video/7630451277697245163"], corpus) == ["https://other.com/video/7630451277697245163"]


def test_duplicate_viral_keys_are_rejected():
    output = research_output("viral")
    output["viral_references"][1]["key"] = "r1"
    assert any("key 重复" in p for p in validate_output(RESEARCH, output, Context()))


def test_foreign_mentions_mask_own_name_and_report_path():
    from sma.tracing.isolation import foreign_mentions
    output = {"a": {"b": ["阿福和测试号一起"]}}
    assert foreign_mentions(output, [{"name": "测试", "path": "/x/@测试"}], ["测试号"]) == []
    hits = foreign_mentions(output, [{"name": "测试号一", "path": "/x/@测试号一"}], ["测试号"])
    assert hits == [("$.a.b[0]", "测…一")]


def test_evidence_problems_name_the_candidate_once():
    output = candidates_output("original", ["data:inputs/strategy-positioning.md"])
    problems = validate_output(IDEATION, output, Context(params={"method": "original"}, resolver=Resolver()))
    evidence = [p for p in problems if p.startswith("[证据]")]
    assert len(evidence) == 3 and "idea-candidates[0].candidates[1]" in evidence[1]
