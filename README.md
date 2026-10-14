# SMA

SMA（Social Media Automation）是内容创作垂直领域的 agent harness：一句话下任务，一组专业 Agent 基于市场证据完成调研、创意、盲评、制作、审片和交付，中途不需要人确认。

**状态：设计阶段。** 现在只有设计文档（草案 v0.1），还没有可运行的代码。

## 文档

- [总体设计](docs/design/00-overview.md)：先读这一份
- 分项规范：[Agent 包](docs/design/01-agent-package.md)、[交接合同](docs/design/02-contracts.md)、[流程与编排](docs/design/03-workflow.md)、[引擎适配](docs/design/04-engines.md)、[工具与策略](docs/design/05-tools-and-policy.md)、[工作区与数据](docs/design/06-workspace-and-data.md)、[追踪与评测](docs/design/07-observability-and-evals.md)
- [决策记录](docs/adr/README.md)

## 和 AI-SMA 的关系

前身 [AI-SMA](https://github.com/oTATo-Boss/AI-SMA) 是一套运行在 Claude Code、Codex 里的 Markdown 规则模板。SMA 从零重写为独立程序：编排、合同、状态、策略和评测由 SMA 自己实现，Codex、Claude 作为执行引擎复用。AI-SMA 的规则、角色、交接格式和测试用例会迁移成 SMA 的策略、Agent 包、Schema 和评测。

## 许可

暂未选定；在选定之前，默认保留所有权利。
