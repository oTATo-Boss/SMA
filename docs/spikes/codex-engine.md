# Codex 引擎实测

- 日期：2026-10-15
- 版本：codex-cli 0.162.0-alpha.17.2，型号 gpt-6.1-sol，ChatGPT 账号登录
- 脚本：`scripts/spikes/codex_spike.py`（测试服务 `scripts/spikes/mcp_echo.py`）
- 对应设计：[04 引擎适配](../design/04-engines.md) 第 10 节 T1–T9

## 结论

| 编号 | 问题 | 结论 | 做法 |
|---|---|---|---|
| T1 | 不读用户配置时，能否用命令行参数配置提供方 | 可以。`-c model_provider=…`、`-c model_providers.<id>.base_url=…` 等会生效 | 连不上提供方时，Codex 不报错退出，而是一直"等待网络"重连；适配层要自己设超时，并把这类事件判为失败 |
| T2 | 能否按次挂载 MCP 服务、限定工具 | 可以。`-c mcp_servers.<id>.command/args` 挂载，`enabled_tools` 白名单生效（工具名显示为 `mcp__<id>__<工具>`） | 必须同时设 `default_tools_approval_mode="approve"`，否则非交互运行时工具调用会被拒绝（`auto` 也不行） |
| T3 | `exec` 会不会自动加载工作目录里的 `AGENTS.md` | 会，而且不需要信任项目 | 角色指令写进尝试目录的 `AGENTS.md` |
| T4 | 不读用户配置时，技能能否被发现 | 能，技能根目录照常扫描，但会把本机全部技能（实测 230–250 个）的说明注入上下文 | 关掉自动技能清单，只把本步骤声明的技能内容写进 `AGENTS.md` |
| T5 | `--output-schema` 的要求 | 嵌套的对象和数组都正常；Schema 不是严格模式时 API 直接拒绝（400：`additionalProperties` 必须为 false） | 所有内容 Schema 按严格模式写，并用 `strict_problems` 静态检查 |
| T6 | 沙箱和网络 | `workspace-write` 只能写工作目录，以及系统临时目录；同级和更远的目录都写不进去。沙箱里默认断网 | 网页搜索是托管工具，不受沙箱断网影响：默认 `cached`，可设 `live` 或 `disabled` |
| T7 | 事件流和用量 | `--json` 输出 `thread.started`、`turn.started`、`item.*`、`turn.completed`；用量在 `turn.completed.usage`（输入、缓存命中、输出、推理） | 适配层从这里取用量 |
| T8 | 多张图片 | `-i` 传两张图，都能正确识别 | 审片抽帧可以直接传图 |
| T9 | 并行 | 3 个 `exec` 同时跑，每个 7–9 秒，没有限流 | 默认并行数 3 |

## 两处设计调整

**1. SMA 使用自己的 Codex 主目录。** `--ignore-user-config` 只跳过 `config.toml`，用户主目录里的全局 `AGENTS.md` 仍会被加载（实测带入了个人的"全局工作约定"）。SMA 给 Codex 设一个独立的 `CODEX_HOME`，里面只有一个指向用户登录文件的软链接（不复制凭据）。实测：登录正常，全局 `AGENTS.md` 和全局记忆都不再带入，写隔离不变。

**2. 技能内容由 SMA 注入。** 在独立主目录里设 `skills.include_instructions=false`，自动技能清单消失，每步输入从约 5 万 token 降到约 2.9 万；本步骤要用的技能，由 SMA 把 `SKILL.md` 写进 `AGENTS.md`。（在用户主目录下这个开关没有效果，原因未查，SMA 也不需要。）

## 实测数据

| 实测 | 设置 | 输入 token | 说明 |
|---|---|---|---|
| A | 用户主目录，默认技能清单 | 55,143 | 全局 `AGENTS.md` 被带入 |
| H | 独立主目录 | 50,283 | 不再带入全局 `AGENTS.md`，技能清单约 230 个 |
| J | 独立主目录，关掉技能清单 | 28,984 | 最小上下文 |
| C3 | 独立主目录，关掉技能清单，挂 1 个 MCP 工具 | 42,131 | 工具调用成功 |

## 遗留

- 系统临时目录（`$TMPDIR`、`/tmp`）可写，账号数据不在那里，暂不处理。
- 用户主目录下 `skills.include_instructions` 不生效的原因没有查。
- 实测共调用模型 16 次，没有遇到限流。
