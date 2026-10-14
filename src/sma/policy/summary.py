"""策略摘要（设计 05 第 3 节）：由代码统一生成，写在每一步 AGENTS.md 的开头。"""

from __future__ import annotations

from sma.packages.model import AgentPackage

NETWORK = {
    "none": "不能联网，也没有网页搜索。",
    "tools-only": "不能直接联网，只能用本步骤挂载的工具。",
    "web": "可以用网页搜索查公开资料；不要登录任何网站。",
}


def policy_summary(spec: AgentPackage) -> str:
    write = "只能在当前目录里写文件，用于草稿和中间结果。" if spec.sandbox == "step-write" else "不能写任何文件。"
    lines = [
        "## 规则（SMA 统一下发，必须遵守）",
        "",
        f"- {write}",
        "- 不发布、不评论、不私信、不点赞、不关注，不和任何人互动。",
        "- 只读取任务里列出的文件和 inputs/ 目录；不要读取其他账号或其他项目的内容，也不要采用对话之外的任何记忆。",
        f"- {NETWORK[spec.network]}",
        "- 最终回复只输出一个 JSON，结构由系统给出的 Schema 约束；JSON 之外不要写别的内容。",
        "- 不编造数据、链接和引用；看不到、查不到的写进对应的缺口字段。",
    ]
    return "\n".join(lines)
