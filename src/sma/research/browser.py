"""账号专用的 Chrome（设计 05 第 6 节）。

资料目录在账号工作区的 .sma/browser/，登录信息只在这里；窗口可见，平台账号由本人自己登录，
SMA 不输入密码、不扫码、不处理验证码。SMA 只通过本机调试端口做只读操作，不碰本人日常用的 Chrome。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]
PLATFORMS = {
    "xhs": ("小红书", "https://www.xiaohongshu.com/explore"),
    "dy": ("抖音", "https://www.douyin.com/"),
    "bili": ("B站", "https://www.bilibili.com/"),
}


class BrowserError(Exception):
    pass


def chrome_binary() -> str | None:
    env = os.environ.get("SMA_CHROME_BIN")
    if env:
        return env
    found = next((p for p in CHROME_CANDIDATES if Path(p).is_file()), None)
    return found or shutil.which("google-chrome") or shutil.which("chromium")


def profile_dir(root: Path) -> Path:
    return root / ".sma" / "browser"


def endpoint(root: Path) -> str | None:
    """正在运行的专用 Chrome 的调试地址；没在运行返回 None。"""
    port_file = profile_dir(root) / "DevToolsActivePort"
    try:
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        url = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{url}/json/version", timeout=2):
            return url
    except (OSError, ValueError, IndexError):
        return None


def _new_tab(url: str, page: str) -> None:
    request = urllib.request.Request(f"{url}/json/new?{urllib.parse.quote(page, safe=':/?=&')}", method="PUT")
    with urllib.request.urlopen(request, timeout=5):
        pass


def open_browser(root: Path, pages: list[str] | None = None, wait_s: float = 20.0) -> str:
    """打开（或复用）这个账号的专用 Chrome，返回调试地址。"""
    pages = pages or []
    url = endpoint(root)
    if url:
        for page in pages:
            _new_tab(url, page)
        return url
    binary = chrome_binary()
    if not binary:
        raise BrowserError("找不到 Chrome：装好 Google Chrome，或用环境变量 SMA_CHROME_BIN 指定")
    profile = profile_dir(root)
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "DevToolsActivePort").unlink(missing_ok=True)
    args = [binary, f"--user-data-dir={profile}", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--no-first-run", "--no-default-browser-check", *pages]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        url = endpoint(root)
        if url:
            return url
        time.sleep(0.3)
    raise BrowserError(f"Chrome 没有在 {wait_s:.0f} 秒内启动好")
