# 02 交接合同

- 状态：草案 v0.1
- 相关：[01 Agent 包规范](01-agent-package.md)、[03 流程与编排](03-workflow.md)、[ADR-0007](../adr/0007-structured-output-with-validation.md)

## 1. 原则

- **只传产物。** 角色之间不传聊天记录，只传有 Schema 的产物。
- **同一份 Schema 两处用。** 引擎调用时用它约束输出（Codex 的 `--output-schema`、Claude 的 `--json-schema`），编排层再用它校验。
- **元数据由代码填。** 模型只填内容；谁产出、用了哪些输入、内容哈希、时间，由编排层写进信封，模型不能伪造。
- **事实、推测、方法分开。** 观察到的数据、对原因的推测、可迁移的方法放在不同字段，推测不能当事实引用。

## 2. 公共信封

```json
{
  "artifact": "idea-review",
  "schema_version": 1,
  "id": "r-20261015-0930/review/idea-review.v1",
  "run_id": "r-20261015-0930",
  "step_id": "review",
  "producer": { "agent": "creative-critic", "version": "0.1.0", "engine": "codex", "model": "<实际型号>" },
  "created_at": "2026-10-15T09:41:00+08:00",
  "inputs": [ { "ref": "r-20261015-0930/ideation-a/idea-candidates.v1", "sha256": "…" } ],
  "evidence_refs": [ "lib:xhs-20261015-01" ],
  "content": { }
}
```

| 字段 | 谁填 | 说明 |
|---|---|---|
| `artifact`、`schema_version` | 编排层 | 产物类型和 Schema 版本 |
| `id`、`run_id`、`step_id` | 编排层 | `id` 的格式是 `<运行>/<步骤>/<类型>[-序号].v<版本>` |
| `producer` | 编排层 | 包、包版本、引擎、实际型号 |
| `created_at` | 编排层 | 带时区 |
| `inputs` | 编排层 | 本步骤实际拿到的输入及其哈希 |
| `evidence_refs` | 模型，编排层校验 | 依据：`lib:<爆款条目 id>`、工作区相对路径或公开链接 |
| `content` | 模型 | 各类型自己的字段 |

## 3. 产物类型

| 类型 | 谁产出 | 用途 | `content` 关键字段 | 里程碑 |
|---|---|---|---|---|
| `account-baseline` | 增长分析 | 本账号近期常态 | 各平台中位数、最好和最差的作品、各自用的机制、数据口径和日期 | M1 |
| `viral-reference` | 市场研究 | 一条爆款的证据和拆解 | `platform`、`url`、`author`、`published_at`、`viewed_at`、`metrics`（原名和数值）、`baseline`、`outlier_ratio`、`media_reviewed`、`observed`、`inferred`、`transferable` | M1 |
| `opportunity-brief` | 市场研究，编排层合并 | 现在值得做什么 | `opportunities`（`topic`、`why_now`、`evidence_refs`、`audience_signal`、`mechanism_hints`）、`gaps` | M1 |
| `run-plan` | 规划 | 本次的研究方向和创意方法组合 | `research_lanes`、`ideation_methods`、`notes` | M2 |
| `idea-candidates` | 创意总监 | 一种方法下的候选 | `method`、`candidates`（`id`、`one_liner`、`hook`、`beats`、`payoff`、`why`、`evidence_refs`、`borrowed_mechanism`、`difference_from_source`、`feasibility`） | M1 |
| `idea-review` | 创意评审 | 盲评结果 | `blind`、`scores`（`candidate`、`dimensions`、`total`、`risks`）、`pick`、`explore_pick`、`verdict`、`required_changes`、`similarity_flags` | M2 |
| `production-plan` | 导演 | 观众体验和制作思路 | `idea`、`experience`（第 1 秒、第 3 秒、转折、回报）、`style`、`assets`、`sound`、`risks` | M2 |
| `shot-list` | 导演 | 逐镜头方案 | `total_seconds`、`shots`（`id`、`seconds`、`visual`、`action`、`camera`、`audio`、`text`、`assets`、`generation`） | M2 |
| `asset-manifest` | 素材制作 | 素材和来源 | `assets`（`id`、`shot`、`type`、`path`、`source`、`tool`、`job_id`、`check`） | M3 |
| `edit-result` | 后期 | 成片和封面 | `media`（`path`、`sha256`、时长、分辨率）、`cover`、`notes` | M3 |
| `copy-package` | 后期 | 发布文案 | `title`、`body`、`tags`、`cover_text` | M3 |
| `review-report` | 审片 | 实际看片的结论 | `media`（`path`、`sha256`）、`method`（抽帧、转写、声音、限制）、`verdict`、`defects`（`timecode`、`shot`、`severity`、`problem`、`cause`、`owner`、`fix`）、`strengths`、`publishable` | M3 |
| `experience-update` | 增长分析 | 真实数据带来的经验 | `mechanism`、`evidence`（作品、指标、相对常态）、`sample_size`、`window`、`confidence`、`status`、`recommendation` | M4 |
| `delivery-rationale` | 编排层 | 依据卡 | 选中的创意、市场依据、评审摘要、审片摘要、风险、运行链接 | M2 |
| `blocking-report` | 编排层 | 阻塞报告 | `reason`、`done`、`needed`、`resume` | M1 |

取值约定沿用 AI-SMA：

- 创意评审的 `verdict`：`pass`／`revise`／`reject`；审片的 `verdict`：`pass`／`rework`／`reject`。
- `severity`：`blocker`／`major`／`minor`；`fix`：`trim`／`regenerate`／`redo`／`repackage`。
- `confidence`：`low`／`medium`／`high`；`status`：`hypothesis`／`verified`。
- `blocking-report.reason`：`positioning-unconfirmed`／`needs-login`／`needs-key`／`needs-authorization`／`rounds-exhausted`／`compliance-doubt`。

## 4. 校验层级

| 层 | 检查什么 | 例子 |
|---|---|---|
| 1 Schema | 字段、类型、取值 | `verdict` 只能是三个值之一 |
| 2 语义规则 | 字段之间的关系 | `pick` 必须是打过分的候选；`blind` 必须为真；过线要满足评分阈值；`verified` 的经验样本不少于 3；审片 `pass` 时不能有 blocker |
| 3 证据存在 | 依据真的存在 | `lib:` 开头的条目要在爆款库里；工作区路径要存在；引用的公开链接要出现在输入文件或引擎本次看到的内容里（搜索结果、打开的网页、命令输出） |
| 4 哈希绑定 | 产物对应的文件没被改过 | 审片报告里的成片哈希必须等于当前文件；检查和确认绑定的文件改了就失效 |

评分阈值（例如评审总分不低于 25、每项不低于 3、"一致"不低于 4）写在对应包的 `knowledge/` 里，随包版本走。

## 5. 修复重试

输出没通过校验时，编排层把校验错误附在任务后面重跑一次，最多重试到包的 `limits.max_attempts`。每次失败都记进运行记录；重试用完仍不合格，步骤失败，按 [03](03-workflow.md) 第 5 节处理。

## 6. 存储与版本

- 产物存在 `runs/<运行>/artifacts/<步骤>/<类型>[-序号].v<版本>.json`，状态库按哈希建索引。
- 返工让版本加 1，旧版本保留。
- 爆款条目先作为运行产物存在，通过校验后由编排层收进账号工作区的 `library/viral/`，按链接去重。Agent 不直接写爆款库。

## 7. Schema 演进

- 每种产物有自己的 `schema_version`，不兼容的改动才升版本。
- Agent 包在 `inputs` 里声明接受的版本范围；已存的旧产物需要时写迁移脚本转换。

## 8. 示例：创意候选的 `content`

```json
{
  "method": "cross-domain",
  "candidates": [
    {
      "id": "B1",
      "one_liner": "把美食视频的'升级失控'结构搬到穿搭",
      "hook": { "visual": "第 1 秒：一件白 T 被放进料理锅", "line": "今天用做菜的方式搭一套衣服" },
      "beats": ["摆出食材式的单品", "每一步加一件更离谱的配饰", "最后一步翻车又救回来", "成品定格"],
      "payoff": "翻车后的成品意外好看",
      "why": "美食赛道'升级失控'结构近 7 天有 3 条超过作者常态 5 倍",
      "evidence_refs": ["lib:dy-20261015-02", "lib:dy-20261015-04"],
      "borrowed_mechanism": "预期建立 → 逐步升级 → 失控 → 反转收尾",
      "difference_from_source": "主体从食物换成服装，失控点改成配饰叠加，结尾加一次救场",
      "feasibility": "6 个镜头，其中 1 个需要生成，其余用固定资产和代码动画"
    }
  ]
}
```
