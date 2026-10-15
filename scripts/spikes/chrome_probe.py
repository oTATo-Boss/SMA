"""实测 0a：在账号专用的 Chrome 里打开一个页面，记下页面自己发出的数据请求和页面状态的结构。

只读、单页、慢速；输出写到指定目录，供设计研究工具的平台适配器参考。
用法：python chrome_probe.py <账号工作区> <网址> <输出目录> [等待秒数]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from sma.research.browser import endpoint

STATE_VARS = ["__INITIAL_STATE__", "__RENDER_DATA__", "__INITIAL_SSR_STATE__", "__pinia", "_ROUTER_DATA", "__NEXT_DATA__"]


def shape(value, depth: int = 0, max_depth: int = 4):
    """只保留结构：键名、类型、列表长度，不保留内容。"""
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth + 1, max_depth) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [len(value), shape(value[0], depth + 1, max_depth)] if value else [0]
    return type(value).__name__


def main() -> None:
    root, url, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    wait_s = float(sys.argv[4]) if len(sys.argv) > 4 else 6
    out.mkdir(parents=True, exist_ok=True)
    cdp = endpoint(root)
    if not cdp:
        raise SystemExit("专用 Chrome 没有运行：先 sma browser open")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp)
        page = browser.contexts[0].new_page()
        responses = []
        page.on("response", lambda r: responses.append(r) if r.request.resource_type in {"xhr", "fetch"} else None)
        page.goto(url, wait_until="commit", timeout=45000)
        page.wait_for_timeout(int(wait_s * 1000))
        record = {"url": url, "final_url": page.url, "title": page.title(), "requests": [], "state": {}}
        for r in responses:
            item = {"url": r.url.split("?")[0], "status": r.status}
            try:
                body = r.json()
                item["shape"] = shape(body)
                (out / f"resp-{len(record['requests']):02d}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            except Exception:
                item["shape"] = None
            record["requests"].append(item)
        for name in STATE_VARS:
            try:
                value = page.evaluate(f"() => {{ const v = window['{name}']; return v === undefined ? null : JSON.parse(JSON.stringify(v, (k, x) => (typeof x === 'object' && x !== null && x.__v_isRef) ? x._value : x)); }}")
            except Exception as exc:
                value = f"读取失败：{type(exc).__name__}"
            if value is not None:
                record["state"][name] = shape(value, max_depth=5)
                (out / f"state-{name}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        (out / "probe.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        page.close()
        browser.close()   # 只断开，不关浏览器
    print(json.dumps({"final_url": record["final_url"].split("?")[0], "title": record["title"],
                      "requests": [(i["url"], i["status"], bool(i["shape"])) for i in record["requests"]],
                      "state": list(record["state"])}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
