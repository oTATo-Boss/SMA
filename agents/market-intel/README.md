# market-intel · 市场研究

找被市场验证过的机会：搜本赛道和相邻赛道近期明显超过作者常态的作品，拆解机制，交爆款条目（`viral-reference`）和机会简报（`opportunity-brief`）。在 `ideate` 流程里按研究方向（`lane` 参数）并行。

- 输入：定位快照（工作区 `strategy/`）、对标库（可选）、本人放在 `data/` 的资料（可选）
- 输出：`viral-reference`（多条）、`opportunity-brief`
- 权限：只能写本步骤目录；M1 用引擎自带的网页搜索，M2 起改用 `sma-research` 工具（Chrome 只读浏览）
- 技能（可选）：content-planning；找异常作品、挖评论的方法整理在 `knowledge/研究方法.md`（参考 ScrapeCreators 的技能，MIT）
- 爆款条目由编排层的 `merge` 步骤校验、去重后收进账号的爆款库，本包不直接写库

## 怎么测

```bash
uv run sma package lint market-intel
uv run sma eval agent market-intel
```

M1 的网页搜索基本拿不到抖音、小红书的作品数据，评测主要检查诚实性（没看过画面不声称看过、缺口写清）。
