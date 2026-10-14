# creative-director · 创意总监

按指定的一种方法（`method` 参数），基于机会简报和爆款条目独立产出 3–4 个机制不同的创意候选（`idea-candidates`）。在 `ideate` 流程里 3 路并行，各用一种方法。

- 输入：定位快照（工作区 `strategy/`）、机会简报、爆款条目（可选）
- 输出：`idea-candidates`
- 权限：只读，不联网
- 技能（都可选）：creative-director（CC BY 4.0，引用原文注明出处）、storytelling-hooks、content-planning；来源见仓库 `docs/skills.md`
- 知识：`knowledge/创意方法.md`

## 怎么测

```bash
uv run sma package lint creative-director
uv run sma eval agent creative-director              # 跑 evals/cases.jsonl 的全部用例
uv run sma eval agent creative-director --case cd-003
```

评测用例的输入在 `evals/fixtures/`，是虚构的测试账号"阿福便利店"，不含任何真实账号的数据。
