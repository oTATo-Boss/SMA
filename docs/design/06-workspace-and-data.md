# 06 工作区与数据

- 状态：草案 v0.1
- 相关：[02 交接合同](02-contracts.md)、[05 工具与策略](05-tools-and-policy.md)、[ADR-0005](../adr/0005-workspace-holds-data-only.md)、[ADR-0006](../adr/0006-sqlite-plus-files-state.md)、[ADR-0008](../adr/0008-account-scoped-memory.md)

## 1. 系统和账号分开

- **系统**：SMA 的代码、Agent 包、流程、Schema、MCP 服务，装一份，所有账号共用，升级只升这一份。
- **账号工作区**：一个账号一个文件夹，只放这个账号的配置和数据，不复制任何系统文件。

本机登记的工作区列在 SMA 的用户配置里（`sma account list`），隔离检查要用到这份清单。

用户级配置在 `~/.config/sma/`（可用环境变量 `SMA_CONFIG_DIR` 改），不放任何账号数据：

| 文件 | 内容 |
|---|---|
| `accounts.yaml` | 本机登记的账号工作区 |
| `config.yaml` | 各档位在各引擎上的默认型号（[04](04-engines.md) 第 8 节） |
| `codex-home/` | SMA 专用的 Codex 主目录，只有指向用户登录文件的软链接 |
| `evals.db`、`evals/` | 包级评测的结果和尝试目录（评测用虚构数据，不属于任何账号） |

## 2. 账号工作区结构

```text
@账号名/
├── sma.yaml                账号配置
├── strategy/
│   ├── positioning.md      定位与策略（定位状态由确认记录决定）
│   ├── content-rules.md    品牌、声音、制作、包装、分发规则
│   └── facts.md            账号、本人、品牌、产品的事实
├── library/
│   ├── viral/<id>.json     爆款库，一条一个文件
│   └── benchmarks.json     对标账号
├── memory/<包 id>.md       各角色在本账号的经验：已验证／假设
├── data/                   后台导出、截图、数据快照
├── runs/<运行 id>/         每次运行：步骤目录、产物、事件流
├── 交付/                   完整内容包，只增不改
├── 发布内容/               本人归档，SMA 只读
├── 固定资产/               品牌和角色的原始资产，SMA 只读
└── .sma/
    ├── state.db            状态库
    └── browser/            专用 Chrome 用户目录（含登录信息，不进备份）
```

- 用户能看到、会直接用的三个目录保留中文名；其余目录和所有代码标识用英文（[00](00-overview.md) 第 13 节 R8）。
- `strategy/` 里是给人读、也给 Agent 读的 Markdown；需要程序判断的字段（例如定位状态、平台列表）放在 `sma.yaml` 或状态库里，不靠解析 Markdown。

## 3. sma.yaml

| 段 | 内容 |
|---|---|
| `account` | 账号名、平台列表、时区 |
| `autonomy` | `mode`（`full`／`checkpoints`）、`max_parallel_steps`、各循环的轮数上限、`explore_ratio` |
| `models` | 档位到型号的映射、按包的覆盖（[04](04-engines.md) 第 8 节） |
| `routes` | 启用的制作路线（AI 生成、代码动画、实拍剪辑、图文） |
| `authorizations` | 各付费提供方的授权：本人原话、日期、范围 |
| `isolation` | 本人允许出现的其他名称（白名单） |
| `deliverables` | 交付包的规格：视频、封面、文案的格式和命名 |

格式由 `schemas/sma-account.schema.json` 约束，`sma doctor` 会校验。

## 4. 账号经验

- 每个包在本账号一份 `memory/<包 id>.md`，分"已验证"和"假设"两区，写清样本数、时间窗、置信度和证据。
- **写入只有一条路径**：增长分析交出 `experience-update` → 编排层校验（`verified` 至少 3 个样本、证据存在）→ 写入对应包的文件，并在状态库记一笔。
- 本人让"记住"的偏好，通过 `sma memory add <包> --note …` 写入，标为本人确认。
- 客户端（Codex、Claude）自己的全局记忆不读也不写（[04](04-engines.md) 第 4 节）。

## 5. 状态库

SQLite，放在工作区的 `.sma/state.db`。大文件只存路径和哈希。

| 表 | 主要字段 |
|---|---|
| `runs` | id、流程 id 和版本、任务原话、模式、状态、开始和结束时间 |
| `steps` | 运行 id、步骤 id、包和版本、状态、尝试次数、引擎、型号、耗时、用量 |
| `attempts` | 步骤、序号、尝试目录、退出状态、校验结果、错误 |
| `artifacts` | id、类型、Schema 版本、路径、sha256、产出步骤、版本 |
| `checks` | 对象、名称、结果、证据哈希、说明 |
| `confirmations` | 对象（例如定位文件）、文件哈希、本人原话、时间 |
| `jobs` | 提供方、幂等键、外部任务 ID、状态、回包路径、提交时间 |
| `deliveries` | 交付目录、文件清单和哈希、来源运行 |
| `evals` | 评测集、用例、包和版本、型号、分数、时间 |

确认和检查都绑定内容哈希；对象改了，旧的确认和检查自动失效（沿用 AI-SMA 任务工具的做法）。

## 6. 从 AI-SMA 导入

`sma account import <AI-SMA 账号项目>`（M4）：

| AI-SMA | SMA |
|---|---|
| `agent/规范/定位与策略.md` | `strategy/positioning.md`；定位状态不迁移，导入后需要本人重新确认一次 |
| `agent/规范/内容与交付.md`、`账号信息.md` | `strategy/content-rules.md`、`strategy/facts.md` |
| `agent/规范/账号配置.yaml` | `sma.yaml` 的对应段 |
| `agent/账号数据/` | `data/`；爆款库和对标库转成 `library/` |
| `agent/团队/*/经验.md` | `memory/<包 id>.md` |
| `交付/`、`发布内容/`、`固定资产/` | 原样保留 |
| 任务记录和日志 | 不迁移，原项目保留作历史 |

导入只读原项目，不改原项目的任何文件。
