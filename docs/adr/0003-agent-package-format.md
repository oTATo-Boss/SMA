# ADR-0003 Agent 包格式

- 状态：已采纳（2026-10-15）
- 相关：[01 Agent 包规范](../design/01-agent-package.md)

## 背景

业内没有统一的 Agent 包标准：技能有 Agent Skills 规范，工具有 MCP，各框架各自定义 Agent。SMA 的角色要能独立开发、测试、升级，并能在不同引擎上运行。

## 决定

每个角色一个包：`agent.yaml`（运行配置）、`instructions.md`（指令）、合同、知识、私有技能、评测集。共享技能按名字引用，不复制。包可以导出成 Claude Code subagent 和 Codex 自定义 agent，给交互式使用。

## 代价与影响

- 一份源定义，多处使用；评测集和合同让每次改动都能比较。
- 导出到交互客户端时会失去部分硬约束，只作为辅助用法。

## 考虑过的其他方案

- 直接用 Claude Code 的 subagent 格式：和 Codex 不通用，也没有合同和评测集的位置。
- 直接用 Claude Code 插件格式：插件内的 subagent 不支持在自身定义里设置 MCP、钩子和权限模式。
