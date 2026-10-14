"""链接规范化：爆款条目去重、核对引用都靠它。

只去掉追踪参数（utm_、spm、分享来源、xsec_ 等），保留标识作品的参数（YouTube 的 v、抖音的 modal_id、B 站的 p），
否则不同作品会被当成同一条。
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit

TRACKING_KEYS = {"from", "source", "ref", "feature", "si", "pp", "t", "timestamp", "vd_source", "unique_k", "is_from_webapp",
                 "sender_device", "web_id", "previous_page", "enter_from", "igsh", "igshid", "mibextid", "fbclid", "gclid",
                 "scene", "app_platform", "apptime", "xhsshare", "author_share", "wechatshare"}
TRACKING_PREFIXES = ("utm_", "share_", "xsec_", "spm")


def identity_params(query: str) -> list[tuple[str, str]]:
    pairs = parse_qsl(query, keep_blank_values=False)
    return sorted((k, v) for k, v in pairs if k.lower() not in TRACKING_KEYS and not k.lower().startswith(TRACKING_PREFIXES))


def normalize_url(url: str) -> str:
    """协议和主机小写，去掉末尾斜杠和追踪参数，剩下的参数排序。"""
    parts = urlsplit(url.strip())
    base = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"
    query = urlencode(identity_params(parts.query))
    return f"{base}?{query}" if query else base


def identity_tokens(url: str) -> list[str]:
    """标识作品的片段：路径最后一段和标识参数的值（至少 6 个字符才算）。"""
    parts = urlsplit(url)
    segment = next((s for s in reversed(parts.path.split("/")) if s), "")
    tokens = [segment] if len(segment) >= 6 else []
    return tokens + [v for _, v in identity_params(parts.query) if len(v) >= 6]


def unseen_urls(urls: list[str], corpus: str) -> list[str]:
    """不在 corpus（输入文件和引擎看到的内容）里的链接。

    允许写法上的差别：规范化以后出现过，或者域名和全部标识片段都出现过，都算看到过——
    例如在作者的作品列表里看到作品编号，再写成作品页链接。
    """
    missing = []
    for url in urls:
        canonical = normalize_url(url)
        domain = urlsplit(url).netloc.lower().removeprefix("www.")
        tokens = identity_tokens(url)
        if url in corpus or canonical in corpus or canonical.split("://", 1)[-1] in corpus:
            continue
        if tokens and domain in corpus and all(t in corpus for t in tokens):
            continue
        missing.append(url)
    return missing
