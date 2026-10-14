# 外部技能登记

Agent 包按名字引用技能，不把技能复制进仓库；运行时 SMA 在本机技能根目录里按名字找到 `SKILL.md`，写进步骤的 `AGENTS.md`（[04 引擎适配](design/04-engines.md) 第 7 节）。技能根目录默认是 `~/.agents/skills`、`~/.codex/skills`、`~/.claude/skills`，可用环境变量 `SMA_SKILL_ROOTS` 改。

| 技能 | 来源 | 许可 | 用到它的包 | 必需 | 说明 |
|---|---|---|---|---|---|
| creative-director | [smixs/creative-director-skill](https://github.com/smixs/creative-director-skill)（Serge Shima） | CC BY 4.0 | creative-director | 否 | 引用它的案例和方法原文时注明出处 |
| storytelling-hooks | [yaxeen/storytelling-skills](https://github.com/yaxeen/storytelling-skills) | 未声明 | creative-director | 否 | 许可未确认，只在本机运行时加载，不随仓库分发 |
| content-planning | [oTATo-Boss/my-skills](https://github.com/oTATo-Boss/my-skills) | MIT | market-intel、creative-director | 否 | — |

## 只借方法、不加载的技能

| 技能 | 来源 | 许可 | 用在哪里 |
|---|---|---|---|
| outlier-post-finder、comment-mining | [ScrapeCreators/social-media-research-skills](https://github.com/ScrapeCreators/social-media-research-skills) | MIT | 方法整理进 `agents/market-intel/knowledge/研究方法.md`；原技能调用付费接口，SMA 不加载 |

新增或更换技能时：在这里登记一行，包的 `agent.yaml` 里写名字和是否必需，`sma package lint` 会检查本机有没有装。
