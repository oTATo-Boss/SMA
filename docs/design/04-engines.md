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
| `images` | 需要看的图片，例如审片抽帧 |
| `timeout_s` | 超时 |
| `env` | 要传入的环境变量名（只传名字，值由进程环境提供）；M3 接生成服务时加 |

| `StepResult` 字段 | 说明 |
|---|---|
| `status` | `ok`、`engine_error`、`auth_error`（登录失效，编排层直接阻塞）、`timeout`、`cancelled` |
| `output` | 解析后的 JSON；没有或解析失败为空 |
| `events_path` | 引擎事件流，原样保存 |
| `usage` | 引擎报告的用量（有则记） |
| `duration_s`、`engine_version`、`model_reported` | 运行信息 |

## 3. 步骤目录

每次尝试有自己的目录：

```text
runs/<运行>/steps/<步骤>/attempt-<n>/
├── AGENTS.md        生成的：策略摘要、角色指令、知识、技能内容、本账号经验
├── CLAUDE.md        只有一行 @AGENTS.md，给 Claude 用
├── inputs/          本步骤的输入：上游产物（JSON）和定位等工作区文件的快照，评审类已盲化
├── schema.json      本步骤的输出 Schema（严格模式）
├── prompt.md        任务正文（修复重试时附上次的校验问题）
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
CODEX_HOME=<SMA 的 Codex 主目录> \    # 只有指向用户登录文件的软链接：不带入全局 AGENTS.md 和全局记忆
codex exec \
  --ephemeral \                       # 不留会话文件：不进 Codex 历史，也不会被拿去生成记忆
  --ignore-user-config \              # 不读 config.toml：个人插件、个人 MCP、个人设置都不带入
  --skip-git-repo-check \
  --json \                            # 事件流，保存为 events.jsonl
  --output-schema <schema.json> \     # 严格模式的 Schema，约束最终回复
  -o <attempt>/output.json \
  -C <attempt> \
  -s read-only | workspace-write \
  -m <型号> \
  -c model_reasoning_effort=<强度> \
  -c skills.include_instructions=false \         # 不注入本机全部技能的清单；需要的技能由 SMA 写进 AGENTS.md
  -c web_search=disabled | cached | live \        # 按包的 network 声明
  -c 'mcp_servers.<名字>.command=…' -c 'mcp_servers.<名字>.args=[…]' \
  -c 'mcp_servers.<名字>.enabled_tools=[…]' \
  -c 'mcp_servers.<名字>.default_tools_approval_mode="approve"' \  # 非交互运行必须设，否则工具调用被拒
  -i <图片> … \                        # 需要看图的步骤
  "<任务>"
```

- **SMA 的 Codex 主目录**：放在 SMA 的用户配置目录里，只有一个指向用户 `auth.json` 的软链接，不复制凭据；`sma doctor` 检查链接和登录。实测证明这样能挡住全局 `AGENTS.md` 和全局记忆，写隔离不变（[实测](../spikes/codex-engine.md)）。
- **网络**：沙箱里默认断网；网页搜索是托管工具，按包的 `network` 决定：`none` → `disabled`，`web` → `cached`（需要实时结果时 `live`），`tools-only` → `disabled`，只用挂载的 MCP 工具。
- **超时**：提供方连不上时 Codex 会一直重连而不退出，适配层自己计时，并把连续的"等待网络"事件判为失败。
- **只开声明的能力**：Codex 默认开着 ChatGPT 连接器、插件、浏览器和电脑操作、子 Agent、生图、技能搜索等，适配层每次用 `-c features.<名字>=false` 全部关掉（实测 T10）。
- **进程**：引擎在单独的进程组里运行；取消、超时或引擎退出后，结束整个进程组，引擎起的命令和 MCP 服务不会留在后台。SMA 收到 Ctrl-C、kill 或终端关闭时，先结束引擎再退出。
- **登录失效**：错误像登录问题时，再用 `codex login status` 确认，确实没登录才阻塞（`needs-login`），否则按普通错误重试。
- **看到的内容**：从事件流取出搜索结果、打开的网页和命令输出，交给编排层核对引用的链接（实测 T11）。

登录方式：

| 方式 | 怎么用 | 说明 |
|---|---|---|
| 本机 Codex 登录 | 默认；`sma doctor` 用 `codex login status` 检查 | 用户的 ChatGPT 账号额度 |
| 自定义 API | `-c model_provider=<id>`、`-c 'model_providers.<id>.base_url=…'`、`-c 'model_providers.<id>.env_key=<变量名>'` | 实测有效；Key 只放在环境变量里 |

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

不依赖引擎自动发现技能（实测会把本机全部技能的说明注入上下文，约 2 万 token）。由 SMA 按包的 `skills` 声明，在本机技能根目录里按名字找到 `SKILL.md`，把内容写进尝试目录的 `AGENTS.md`；必需技能找不到时，步骤不运行并报告缺什么。技能包里引用的脚本和参考资料，以 `SKILL.md` 所在目录的绝对路径告诉引擎，只读。

单步上下文（M1 真实运行）：创意总监一步注入角色指令、创意方法和 3 个技能，整步累计 9–13 万输入 token；市场研究一步要做几十次网页搜索，每次都把上下文再发一遍，累计 190–490 万输入 token，九成命中缓存。技能按需选择，研究步骤的用量是 M2 优化的重点（研究工具直接返回结构化数据，减少来回搜索）。

## 8. 模型路由

确定一个步骤用哪个型号，按这个顺序找，先找到的为准：

1. 流程定义里对这一步的指定（M2）；
2. 账号配置 `models.agents.<包>`；
3. 包的 `model.tier`，按账号配置的 `models.tiers` 换成具体型号；
4. 用户级默认：`~/.config/sma/config.yaml` 的 `models.tiers`，所有账号共用；
5. 都没有时用引擎自己的默认型号。

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

## 10. 实测结论

T1–T9 已在 M1 开始时实测，结论和数据见 [Codex 引擎实测](../spikes/codex-engine.md)。要点：

- 提供方、MCP 服务、网页搜索、技能注入都能用命令行参数按次控制，不依赖用户配置；
- MCP 工具必须设 `default_tools_approval_mode="approve"`；
- Schema 必须是严格模式；
- 工作目录的 `AGENTS.md` 自动加载，不需要信任项目；
- 3 路并行没有限流。
