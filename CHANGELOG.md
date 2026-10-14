# 变更记录

## 0.1.0.dev0（M1 进行中）

- 设计文档草案 v0.1 和 9 篇 ADR。
- M1 第 0 步：Codex 引擎实测（`docs/spikes/codex-engine.md`）。
- M1 第 1–8 步：交接合同和四层校验、账号工作区和状态库、Agent 包加载和检查、Codex 引擎适配、编排运行器（并行、修复重试、阻塞、续跑、取消）、命令行、包级评测；首批包 market-intel、creative-director，流程 ideate（M1 版）。
- M1 验收：虚构测试账号用 Codex 跑通 `ideate`（2 路调研 + 3 路创意，约 13 分钟，产物全部过校验）；中途 Ctrl-C 后续跑只重跑没完成的步骤；隔离核对通过；creative-director 评测 4/4。
- 真实运行和独立审查之后的修复：关掉 Codex 默认开着的连接器、插件、浏览器等能力；引用的链接必须真的看到过；取消时结束整个进程组；运行加锁、爆款库加锁；错误文字脱敏；链接去重保留作品参数。market-intel、creative-director 升到 0.2.0。
