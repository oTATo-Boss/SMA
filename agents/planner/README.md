# planner · 规划

`ideate` 流程的第一步：读懂一句话任务，交 `run-plan`。编排层的 `plan_intent` 检查决定往哪走：内容任务继续；调整方向的任务不进创意流程，把复述和问题交给本人（阻塞原因 `direction-change`）；其他任务给出建议（`not-content-task`）。

- 输入：定位快照（工作区 `strategy/`）、最近几次运行（`recent-runs`）、步骤参数 `choices`
- 输出：`run-plan`
- 权限：只读，不联网
- 规划失败时流程不中断，研究方向和创意方法用流程定义里的默认值

## 怎么测

```bash
uv run sma package lint planner
uv run sma eval agent planner
```
