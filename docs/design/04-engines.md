# 04 引擎适配

- 状态：草案 v0.1
- 相关：[01 Agent 包规范](01-agent-package.md)、[05 工具与策略](05-tools-and-policy.md)、[ADR-0001](../adr/0001-reuse-agent-engines.md)

## 1. 为什么要适配层

SMA 不自己写"调模型、跑工具"的循环，而是把每个步骤交给现成的 Agent 引擎执行：开发期是 Codex CLI，之后加 Claude CLI 和自定义 API。各引擎的参数、登录方式、事件格式都不一样，适配层把这些差异挡在编排层和 Agent 包之外。

## 2. 接口

```python
class Engine(Protocol):
    name: str                                        # "codex"、"claude"、"api"
    def check(self) -> EngineStatus: ...             # 是否安装、版本、登录状态、支持的能力
    def run(self, request: StepRequest) -> StepResult: ...
```

| `StepRequest` 字段 | 说明 |
|---|---|
| `attempt_dir` | 本次尝试的目录，引擎在这里运行 |
| `prompt` | 任务和输入清单（第 3 节） |
| `output_schema` | 输出的 JSON Schema 文件 |
| `model`、`reasoning` | 已经解析好的具体型号和推理强度（第 8 节） |
| `sandbox`、`network` | 来自包的声明 |
| `mcp_servers` | 已解析的 MCP 服务启动命令和工具白名单 |
| `skills` | 要加载的技能名 |
| `images` | 需要看的图片，例如审片抽帧 |
| `timeout_s` | 超时 |
| `env` | 要传入的环境变量名（只传名字，值由进程环境提供） |

| `StepResult` 字段 | 说明 |
|---|---|
| `status` | `ok`、`engine_error`、`timeout`、`cancelled` |
| `output` | 解析后的 JSON；没有或解析失败为空 |
| `events_path` | 引擎事件流，原样保存 |
| `usage` | 引擎报告的用量（有则记） |
| `duration_s`、`engine_version`、`model_reported` | 运行信息 |

## 3. 步骤目录

每次尝试有自己的目录：

```text
runs/<运行>/steps/<步骤>/attempt-<n>/
├── AGENTS.md        生成的：角色指令、策略摘要、要读的技能和知识
├── CLAUDE.md        只有一行 @AGENTS.md，给 Claude 用
├── inputs/          本步骤的输入产物（JSON），评审类已盲化
├── output.json      引擎的最终回复
├── events.jsonl     引擎事件流
└── stderr.log
```

- 引擎的工作目录就是这个目录；`step-write` 的包只能写这里，`read-only` 的包什么都不能写。
- 需要读的账号工作区文件，在任务里给出绝对路径，只读。
- 角色指令写成工作目录里的 `AGENTS.md`，因为 Codex 和 Claude 都会自动加载工作目录的项目指令，而且这不依赖"信任项目"。这一点在 M1 实测确认（第 10 节）。
- 任务正文只写：本人的原话、本步骤的参数、输入文件清单和每个文件一句话的用途、要交的产物类型。

## 4. Codex CLI

```text
codex exec \
  --ephemeral \                       # 不留会话文件：不进 Codex 历史，也不会被拿去生成记忆
  --ignore-user-config \              # 不读用户的 config.toml：全局记忆、个人插件、个人 MCP 都不带入；登录仍然有效
  --skip-git-repo-check \
  --json \                            # 事件流，保存为 events.jsonl
  --output-schema <schema.json> \     # 约束最终回复的结构
  -o <attempt>/output.json \
  -C <attempt> \
  -s read-only | workspace-write \
  -m <型号> \
  -c model_reasoning_effort=<强度> \
  -c 'mcp_servers.<名字>.command=…' \  # 只挂本步骤声明的工具（M1 实测）
  -i <图片> … \                        # 需要看图的步骤
  "<任务>"
```

登录方式：

| 方式 | 怎么用 | 说明 |
|---|---|---|
| 本机 Codex 登录 | 默认；`sma doctor` 用 `codex login status` 检查 | 用户的 ChatGPT 账号额度 |
| 自定义 API | 用 `-c model_provider=<id>`、`-c 'model_providers.<id>.base_url=…'`、`-c 'model_providers.<id>.env_key=<变量名>'` 传入 | Key 只放在环境变量里；因为不读用户配置，提供方必须由命令行参数给出（M1 实测） |

以后需要长会话或界面嵌入时，改用 Codex 官方的 `app-server` 协议（有 TypeScript 绑定和 JSON Schema），不改编排层。

## 5. Claude CLI

```text
claude -p \
  --output-format stream-json \       # 事件流
  --json-schema <schema.json> \       # 结构化输出
  --model <型号> \
  --mcp-config <mcp.json> --strict-mcp-config \   # 只用声明的工具
  --disallowedTools Agent \           # 步骤内不再派子 Agent
  --permission-mode <模式> \
  "<任务>"
```

- 登录：Claude CLI 自己的登录，或 `ANTHROPIC_API_KEY`。
- 角色指令靠工作目录里的 `CLAUDE.md`（`@AGENTS.md`）加载；也可以用 `--append-system-prompt-file`。
- 每次尝试的工作目录都不同，Claude 按目录存的自动记忆不会延续到下一次。
- 用户级设置会不会带入、怎样只加载需要的设置，M4 接入时实测。

## 6. 自定义 API

给不需要工具的步骤用，例如评审打分、改写、摘要：调用 OpenAI 兼容的接口，用 JSON Schema 约束输出。主要价值是**异家族评审**：评审可以走网关上的另一家模型，和出方案的模型分开。地址和 Key 都来自环境变量。

## 7. 技能加载

| 引擎 | 方式 | 待确认 |
|---|---|---|
| Codex | 从技能根目录自动发现；`AGENTS.md` 里写明本步骤要完整读哪些技能 | 不读用户配置时技能还能否被发现 |
| Claude | 用 `--agents` 定义临时角色并预加载技能，或指定插件目录 | 哪种方式和 `-p` 配合最稳 |
| 两者都不行时 | 把必需技能的 `SKILL.md` 内容直接放进 `AGENTS.md` | 上下文长度 |

## 8. 模型路由

确定一个步骤用哪个型号，按这个顺序找，先找到的为准：

1. 流程定义里对这一步的指定；
2. 账号配置 `models.agents.<包>`；
3. 包的 `model.family` 和 `model.tier`；
4. 全局默认：每个档位在每个引擎上对应的具体型号。

```yaml
models:
  tiers:
    strong:   { codex: <型号>, claude: <型号>, api: <网关型号> }
    standard: { codex: <型号>, claude: <型号>, api: <网关型号> }
  agents:
    creative-critic: { engine: api, family: anthropic }
```

**独立性规则：** 包声明了 `independence.differ_from` 时，它的模型家族必须和那些包不同。只有一家可用时，退而求其次用同家族的另一个型号，并在依据卡里注明"同家族评审"。

## 9. 用量与限流

- 从事件流里取用量（有则记），每次运行汇总；按用户决定，不设预算上限，但用量随时可查。
- 每个引擎有自己的并发上限；遇到限流按退避重试。

## 10. M1 要实测的点

| 编号 | 问题 |
|---|---|
| T1 | `--ignore-user-config` 下，用命令行参数配置自定义提供方是否可用 |
| T2 | 用 `-c mcp_servers.*` 按次挂载 MCP 服务是否可用，工具白名单怎么设 |
| T3 | `exec` 模式会不会自动加载工作目录里的 `AGENTS.md`，是否需要信任项目 |
| T4 | 不读用户配置时，技能能否被发现、能否按名字读取 |
| T5 | `--output-schema` 对复杂 Schema 的支持和失败时的表现 |
| T6 | 沙箱的写限制、默认是否禁网、MCP 工具能否联网 |
| T7 | 事件流里有哪些字段，用量在哪里 |
| T8 | `-i` 传多张图的上限 |
| T9 | 多个 `codex exec` 并行时的限流表现 |
