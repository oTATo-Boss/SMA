"""账号工作区：一个账号一个文件夹，只放这个账号的配置和数据（设计 06）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from sma.hashing import canonical_json, sha256_file
from sma.state.db import StateDB
from sma.timeutil import now
from sma.workspace.config import AccountConfig, AccountInfo

CONFIG_FILE = "sma.yaml"
DIRS = ["strategy", "library/viral", "memory", "data", "runs", "交付", "发布内容", "固定资产", ".sma"]
POSITIONING = "strategy/positioning.md"
STRATEGY_TEMPLATES = {
    "strategy/positioning.md": "# 定位与策略\n\n（待填写：这个账号是谁、给谁看、做什么内容、为什么值得关注、怎么变现。本人确认后运行 `sma strategy confirm`。）\n",
    "strategy/content-rules.md": "# 内容与交付规则\n\n（待填写：品牌与角色、声音、制作路线、封面与文案、分发检查。）\n",
    "strategy/facts.md": "# 账号事实\n\n（待填写：本人、品牌、产品和账号的事实。）\n",
}
LIBRARY_ID = re.compile(r"[a-z]+-\d{8}-\d{2,}")
PLATFORM_CODES = {"小红书": "xhs", "抖音": "dy", "快手": "ks", "b站": "bili", "bilibili": "bili", "视频号": "wx", "微博": "wb",
                  "youtube": "yt", "tiktok": "tt", "instagram": "ig", "x": "x", "twitter": "x"}


class WorkspaceError(Exception):
    pass


def platform_code(platform: str) -> str:
    return PLATFORM_CODES.get(platform.strip().lower(), "web")


def normalize_url(url: str) -> str:
    """去重用：协议、主机、路径，去掉查询参数和末尾斜杠（各平台的追踪参数不影响是不是同一条）。"""
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        config_path = self.root / CONFIG_FILE
        if not config_path.is_file():
            raise WorkspaceError(f"这里不是 SMA 账号工作区（没有 {CONFIG_FILE}）：{self.root}")
        self.config = AccountConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")) or {})
        self.db = StateDB(self.root / ".sma" / "state.db")

    # ---- 建立与查找 ----
    @classmethod
    def init(cls, root: Path, name: str, platforms: list[str] | None = None) -> Workspace:
        root = root.expanduser().resolve()
        if root.exists():
            if (root / CONFIG_FILE).exists():
                raise WorkspaceError(f"已经是账号工作区：{root}")
            visible = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))
            if visible:
                raise WorkspaceError(f"文件夹里已有文件：{'、'.join(visible[:5])}。先确认没开错文件夹，或换一个空文件夹")
        for d in DIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        for rel, text in STRATEGY_TEMPLATES.items():
            path = root / rel
            if not path.exists():
                path.write_text(text, encoding="utf-8")
        benchmarks = root / "library/benchmarks.json"
        if not benchmarks.exists():
            benchmarks.write_text("[]\n", encoding="utf-8")
        config = AccountConfig(account=AccountInfo(name=name, platforms=platforms or []))
        (root / CONFIG_FILE).write_text(yaml.safe_dump(config.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")
        return cls(root)

    @classmethod
    def find(cls, start: Path) -> Workspace:
        """从 start 往上找最近的工作区。"""
        here = start.expanduser().resolve()
        for candidate in [here, *here.parents]:
            if (candidate / CONFIG_FILE).is_file():
                return cls(candidate)
        raise WorkspaceError(f"{here} 及其上级目录里没有 SMA 账号工作区；用 --account 指定，或先运行 sma account init")

    @property
    def name(self) -> str:
        return self.config.account.name

    # ---- 定位确认（唯一的全局关口） ----
    def confirm_positioning(self, note: str) -> str:
        path = self.root / POSITIONING
        if not path.is_file():
            raise WorkspaceError(f"没有定位文件：{POSITIONING}")
        if not note.strip():
            raise WorkspaceError("确认要写本人原话")
        digest = sha256_file(path)
        self.db.add_confirmation(POSITIONING, digest, note.strip())
        return digest

    def positioning_confirmed(self) -> bool:
        record = self.db.latest_confirmation(POSITIONING)
        path = self.root / POSITIONING
        return bool(record and path.is_file() and sha256_file(path) == record["sha256"])

    # ---- 爆款库 ----
    def library_dir(self) -> Path:
        return self.root / "library" / "viral"

    @staticmethod
    def is_library_id(entry_id: str) -> bool:
        return LIBRARY_ID.fullmatch(entry_id) is not None

    def has_library(self, entry_id: str) -> bool:
        return self.is_library_id(entry_id) and (self.library_dir() / f"{entry_id}.json").is_file()

    def library_entries(self) -> list[dict]:
        entries = []
        for path in sorted(self.library_dir().glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("id", path.stem)
            entries.append(data)
        return entries

    def add_library_entries(self, references: list[dict], source: str) -> dict[str, str]:
        """把爆款条目收进爆款库，按链接去重；返回 {条目代号: 爆款库 id}（已有的返回原 id）。"""
        known = {normalize_url(e["url"]): e["id"] for e in self.library_entries() if e.get("url")}
        mapping: dict[str, str] = {}
        day = now().strftime("%Y%m%d")
        for ref in references:
            url = normalize_url(ref["url"])
            if url in known:
                mapping[ref["key"]] = known[url]
                continue
            prefix = f"{platform_code(ref['platform'])}-{day}-"
            used = [int(p.stem.rsplit("-", 1)[1]) for p in self.library_dir().glob(f"{prefix}*.json")]
            entry_id = f"{prefix}{(max(used) + 1 if used else 1):02d}"
            entry = {"id": entry_id, "source": source, **{k: v for k, v in ref.items() if k != "key"}}
            (self.library_dir() / f"{entry_id}.json").write_text(canonical_json(entry), encoding="utf-8")
            known[url] = entry_id
            mapping[ref["key"]] = entry_id
        return mapping

    # ---- 数据和运行目录 ----
    def has_data(self, relative_path: str) -> bool:
        path = (self.root / "data" / relative_path).resolve()
        return path.is_relative_to(self.root / "data") and path.exists()

    def run_dir(self, run_id: str) -> Path:
        return self.root / "runs" / run_id
