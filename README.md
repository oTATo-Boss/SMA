# SMA

SMA（Social Media Automation）是内容创作垂直领域的 agent harness：一句话下任务，一组专业 Agent 基于市场证据完成调研、创意、盲评、制作、审片和交付，中途不需要人确认。

**状态：M1 骨架。** 一句话 → 2 路市场调研 → 合并入库 → 3 路创意候选，已能用 Codex 端到端跑通；创意评审、导演、制作和审片在后续里程碑（见[总体设计](docs/design/00-overview.md)第 12 节）。

## 快速开始

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)、已登录的 [Codex CLI](https://github.com/openai/codex)（`codex login`）。

```bash
git clone https://github.com/oTATo-Boss/SMA.git && cd SMA
uv tool install --editable .              # 把 sma 命令装到 PATH
sma doctor                                # 检查 Codex 登录、Agent 包、技能
sma account init ~/内容/@我的账号 -p 抖音 -p 小红书
cd ~/内容/@我的账号                        # 先写好 strategy/positioning.md
sma strategy confirm --note "就按这个定位来"
sma run "今天做一条视频"
```

| 命令 | 用途 |
|---|---|
| `sma run "<一句话>"` | 按流程自主跑完，中途不确认；只在定位没确认、登录失效等情况下停下，并说明要你做什么 |
| `sma runs list`／`show <运行> [--step <步骤>]` | 每一步的状态、耗时、用量、产物和校验问题 |
| `sma runs resume <运行>` | 续跑：已成功的步骤按哈希复用，其余重跑 |
| `sma strategy confirm --note "<原话>"` | 记录本人对定位的确认；定位文件改了，确认自动失效 |
| `sma package lint`、`sma eval agent <包>` | 检查 Agent 包；跑包的评测集 |

模型：在账号的 `sma.yaml` 里写 `models.tiers.strong.codex: <型号>`，或写在 `~/.config/sma/config.yaml` 给所有账号共用；不写就用 Codex 的默认型号。

## 结构

- `agents/`：Agent 包（角色指令、运行配置、知识、评测集），见 [Agent 包规范](docs/design/01-agent-package.md)
- `workflows/`：流程定义；`schemas/`：产物的 JSON Schema
- `src/sma/`：编排、引擎适配、合同校验、状态库、命令行
- 账号工作区只放账号自己的数据（定位、爆款库、运行记录），不复制任何系统文件

## 文档

- [总体设计](docs/design/00-overview.md)：先读这一份
- 分项规范：[Agent 包](docs/design/01-agent-package.md)、[交接合同](docs/design/02-contracts.md)、[流程与编排](docs/design/03-workflow.md)、[引擎适配](docs/design/04-engines.md)、[工具与策略](docs/design/05-tools-and-policy.md)、[工作区与数据](docs/design/06-workspace-and-data.md)、[追踪与评测](docs/design/07-observability-and-evals.md)
- [决策记录](docs/adr/README.md)、[Codex 引擎实测](docs/spikes/codex-engine.md)、[外部技能登记](docs/skills.md)、[变更记录](CHANGELOG.md)

## 开发

```bash
uv run pytest        # 单元测试全部用假引擎，不调用模型
uv run ruff check .
```

## 和 AI-SMA 的关系

前身 [AI-SMA](https://github.com/oTATo-Boss/AI-SMA) 是一套运行在 Claude Code、Codex 里的 Markdown 规则模板。SMA 从零重写为独立程序：编排、合同、状态、策略和评测由 SMA 自己实现，Codex、Claude 作为执行引擎复用。AI-SMA 的规则、角色、交接格式和测试用例迁移成 SMA 的策略、Agent 包、Schema 和评测。

## 许可

暂未选定；在选定之前，默认保留所有权利。
