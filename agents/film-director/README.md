# film-director · 导演

把选定的创意（`selected-idea`）变成制作方案（`production-plan`）和镜头表（`shot-list`）。M2 只出方案，不生成素材；素材制作和后期是 M3。

- 输入：定位快照、账号的平台和交付规格（`account`）、固定资产清单（`assets`）、选定的创意
- 输出：`production-plan`、`shot-list`
- 权限：只读，不联网
- 技能（都可选）：direct-video、ai-video-prompt、huashu-art-motion
- 规则：镜头时长加起来等于总时长；镜头表引用的资产都在制作方案里；固定资产路径必须存在

## 怎么测

```bash
uv run sma package lint film-director
uv run sma eval agent film-director
```
