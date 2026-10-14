# 01 Agent 包规范

- 状态：草案 v0.1
- 相关：[00 总体设计](00-overview.md)、[02 交接合同](02-contracts.md)、[04 引擎适配](04-engines.md)、[ADR-0003](../adr/0003-agent-package-format.md)

## 1. 定位

Agent 包是一个专业角色的完整定义：身份与方法、运行配置、权限与工具、输入输出合同、知识和评测集。编排层按包调用引擎，包本身不写死任何引擎细节。

业内目前没有统一的 Agent 包标准，SMA 在现有规范上定义自己的格式：技能沿用 Agent Skills 规范（`SKILL.md`），工具沿用 MCP，产物沿用 JSON Schema。需要在 Claude Code 或 Codex 里交互使用时，包可以导出成它们的原生角色配置（第 9 节）。

**Agent 和技能是两层。** Agent 有独立的上下文、职责和决策权；技能是 Agent 完成某类工作的方法包。一个 Agent 可以用多个技能，一个技能也可以被多个 Agent 共用；共享技能只维护一份。

## 2. 目录结构

```text
agents/<id>/
├── agent.yaml          运行配置（必需）
├── instructions.md     角色指令（必需）
├── contracts/          本包特有的 Schema（可选；通用产物引用仓库的 schemas/）
├── knowledge/          本角色的长期参考资料，随包的版本走（可选）
├── skills/             本包私有的技能，遵守 Agent Skills 规范（可选）
├── evals/
│   ├── cases.jsonl     评测用例
│   ├── fixtures/       用例用到的输入产物
│   └── rubric.md       评分标准
├── CHANGELOG.md
└── README.md           给开发者：职责、依赖、怎么测
```

包里不放任何账号信息。账号的经验在账号工作区（[06](06-workspace-and-data.md) 第 4 节），运行时作为输入提供。

## 3. agent.yaml

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `id` | 字符串 | 是 | 小写英文和连字符，例如 `creative-critic` |
| `name` | 字符串 | 是 | 中文名，例如"创意评审" |
| `version` | 语义化版本 | 是 | 规则见第 8 节 |
| `description` | 字符串 | 是 | 一句话：什么时候用它 |
| `kind` | `worker`／`critic`／`planner` | 是 | 干活的、评审的、做规划提议的 |
| `inputs` | 列表 | 是 | 接收的产物类型，标明必需或可选 |
| `outputs` | 列表 | 是 | 交出的产物类型，对应 `schemas/` 或 `contracts/` 里的 Schema |
| `engine.prefer` | 列表 | 是 | 引擎优先顺序，例如 `[codex, claude, api]` |
| `model.family` | `openai`／`anthropic`／`any` | 否 | 模型家族偏好 |
| `model.tier` | `strong`／`standard`／`fast` | 否 | 模型档位，具体型号由账号或全局配置映射 |
| `reasoning` | `low`／`medium`／`high`／`xhigh` | 否 | 推理强度 |
| `sandbox` | `read-only`／`step-write` | 是 | `step-write` 只能写本步骤的目录 |
| `network` | `none`／`tools-only`／`web` | 是 | `tools-only` 只能通过声明的 MCP 工具上网 |
| `tools` | 列表 | 否 | 可用的 MCP 服务和工具白名单 |
| `skills` | 列表 | 否 | 要加载的技能名，标明必需或可选 |
| `knowledge` | 列表 | 否 | 要随指令提供的 `knowledge/` 文件 |
| `memory.read` | 布尔 | 否 | 是否把本账号该角色的经验作为输入 |
| `independence.differ_from` | 列表 | 否 | 评审类必填：必须和哪些包用不同的模型家族 |
| `blind` | 列表 | 否 | 评审类：交给本包前要去掉的字段，例如方法标签 |
| `limits` | 对象 | 否 | `timeout_s`、`max_attempts`（含修复重试）、`max_output_kb` |

**示例：创意评审**

```yaml
id: creative-critic
name: 创意评审
version: 0.1.0
description: 盲评创意候选，核对证据和相似度，选出值得做的方案
kind: critic
inputs:
  - { type: opportunity-brief, required: true }
  - { type: idea-candidates, required: true, many: true }
  - { type: viral-reference, required: false, many: true }
outputs:
  - { type: idea-review }
engine:
  prefer: [codex, claude, api]
model:
  family: any
  tier: strong
reasoning: xhigh
sandbox: read-only
network: none
skills:
  - { name: storytelling-hooks, required: false }
  - { name: creative-director, required: false }
memory:
  read: true
independence:
  differ_from: [creative-director]
blind: [method, author, source_agent]
limits: { timeout_s: 900, max_attempts: 3 }
```

## 4. instructions.md

固定六节，中文：

| 节 | 写什么 |
|---|---|
| 你是谁 | 身份、专业边界、决策权 |
| 你会收到什么 | 输入产物的名字和用途 |
| 你要交什么 | 产物类型和要点；格式由 Schema 保证，不在指令里重复字段表 |
| 怎么做 | 方法步骤，引用要用的技能 |
| 质量标准 | 交出前的自查 |
| 不做什么 | 越权的事，例如评审不改写候选 |

写法约束：

- 不写引擎专属说法和工具调用细节，那是引擎适配层的事。
- 只按名字引用输入，不写文件系统路径。
- 红线不在每个包里重复，由编排层统一注入策略摘要（[05](05-tools-and-policy.md) 第 3 节）。
- 控制长度：指令加预加载的技能、知识，单步上下文的预算见 [04](04-engines.md) 第 7 节的实测。

## 5. 技能

- **共享技能按名字引用，不复制。** `sma doctor` 检查本机有没有装；缺必需技能时，该包不可用并报告缺什么。
- **技能由 SMA 注入。** 不依赖引擎自动发现（实测会把本机全部技能的说明注入上下文）：SMA 按名字找到 `SKILL.md`，写进步骤的 `AGENTS.md`，技能目录只读给出（[04](04-engines.md) 第 7 节）。
- **私有技能放 `skills/`。** 只有本包用、且和 SMA 版本强绑定的方法才放这里。
- **外部技能统一登记。** 仓库层的 `docs/skills.md` 记录每个外部技能的来源、许可和用到它的包；许可要求署名的（例如 CC BY 4.0）在引用处注明。

## 6. 知识与记忆

- `knowledge/`：通用、长期有效的参考，例如创意方法库、评分标准；随包的版本一起变，不放任何账号信息。
- 账号经验：放在账号工作区的 `memory/<id>.md`，分"已验证"和"假设"两区。`memory.read` 为真时作为输入提供。
- **Agent 不直接写记忆。** 经验更新由增长分析以产物形式交出，编排层校验样本数和证据后写入（[06](06-workspace-and-data.md) 第 4 节）。
- 客户端自己的全局记忆一律不用：引擎调用时不加载用户配置（[04](04-engines.md) 第 4 节）。

## 7. 评测集

`evals/cases.jsonl` 每行一个用例：

```json
{"id": "critic-003", "inputs": {"opportunity-brief": "fixtures/brief-003.json", "idea-candidates": ["fixtures/cand-003-a.json", "fixtures/cand-003-b.json"]}, "expect": {"schema": true, "checks": ["pick_in_scores", "flags_copied_signature"], "rubric_min": 4}, "note": "照搬原作标志性设定的候选必须被标出，且不能被选中"}
```

- `checks` 是代码写的确定性检查，`rubric_min` 由另一家族的模型按 `rubric.md` 打分。
- 每个包至少覆盖：正常输入、证据不足、诱导越权（例如诱导评审改写候选）、历史上出过的错。
- 运行方式和结果存储见 [07](07-observability-and-evals.md) 第 3 节。

## 8. 版本

| 位 | 什么时候升 |
|---|---|
| 主版本 | 输出合同不兼容地改变 |
| 次版本 | 方法、指令、技能、模型档位改变 |
| 修订号 | 措辞和错字 |

- 每次升版本在 `CHANGELOG.md` 记一行，并附评测结果对比。
- 运行记录保存每一步用的 `包@版本`，方便回放和对比。

## 9. 导出给交互客户端

```text
sma package export <id> --claude   生成 .claude/agents/<id>.md
sma package export <id> --codex    生成 .codex/agents/<id>.toml
```

导出的角色给 Claude Code、Codex 的交互会话用，写法沿用 AI-SMA 的生成脚本。导出后失去 SMA 的部分硬约束：Codex 的自定义角色不能单独限制工具，结构化输出和校验也只在 SMA 自己调用时才有。

## 10. 首批包

| id | 名称 | kind | 主要输入 | 输出 | 沙箱 | 网络与工具 | 里程碑 |
|---|---|---|---|---|---|---|---|
| `market-intel` | 市场研究 | worker | 定位快照、对标库、本人提供的资料；M2 起加本账号基线 | `viral-reference`、`opportunity-brief` | step-write | M1 用引擎自带的网页搜索（web）；M2 起改用研究和媒体工具（tools-only） | M1 |
| `creative-director` | 创意总监 | worker | 机会简报、爆款条目、方法参数 | `idea-candidates` | read-only | none | M1 |
| `creative-critic` | 创意评审 | critic | 机会简报、候选（盲化）、爆款条目 | `idea-review` | read-only | none | M2 |
| `film-director` | 导演 | worker | 评审结论、选中候选、定位快照 | `production-plan`、`shot-list` | read-only | none | M2 |
| `planner` | 规划 | planner | 任务原话、定位快照、近期运行摘要 | `run-plan` | read-only | none | M2 |
| `media-producer` | 素材制作 | worker | 镜头表、固定资产清单 | `asset-manifest` | step-write | tools-only：生成、媒体 | M3 |
| `post-producer` | 后期 | worker | 镜头表、素材清单、审片意见 | `edit-result`、`copy-package` | step-write | tools-only：媒体 | M3 |
| `quality-reviewer` | 审片 | critic | 成片、镜头表、评审要求 | `review-report` | read-only | tools-only：媒体 | M3 |
| `growth-analyst` | 增长分析 | worker | 后台数据、历史运行的依据卡 | `account-baseline`、`experience-update` | read-only | tools-only：研究 | M2（基线）、M4（回流） |

`planner` 只做提议：例如这次研究走哪几个方向、3 路创意各用什么方法。提议由编排层按流程约束校验后采用，不能改变流程本身、上限和策略。
