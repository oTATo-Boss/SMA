# creative-critic · 创意评审

盲评创意候选，交 `idea-review`。候选由编排层的 `blind` 步骤合并、去掉方法标签、重新编号，评审看不到出处。之后的 `select` 步骤解开盲化，`review_loop` 检查决定往哪走：pass 交给导演；revise 返工选中的候选再评；reject 带着意见重出一轮；最多 2 轮。

- 输入：定位快照、盲化后的候选、机会简报、爆款条目
- 输出：`idea-review`
- 权限：只读，不联网
- 独立性：和 creative-director 用不同的模型家族（优先 Claude）；只有 Codex 时退回同家族，依据卡注明
- 过线阈值：`agent.yaml` 的 `params.thresholds`

## 怎么测

```bash
uv run sma package lint creative-critic
uv run sma eval agent creative-critic
```
