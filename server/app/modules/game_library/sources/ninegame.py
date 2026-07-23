"""九游(9game.cn)·按名搜索源（裸 HTTP + HTML 解析，2026-07-23 生产 ECS 实测 200）。

Plan B 补全腿：只实现 `search_by_name`（与 baidu 源同签名），并入现有巡检循环
（`ingest_service._collect_from_all_sources` 按 source_order 逐源调用）。**只打搜索页、
绝不碰详情页**——详情页 `/<slug>/` 被阿里 WAF captcha 拦，搜索页富卡片已够补全字段。

接口: GET https://www.9game.cn/search/?keyword=<name>  → HTML，首个 `<div class="sr-poker">`
富卡片即 top hit。字段路径 2026-07-23 对 原神/王者荣耀/蛋仔派对 实抠确认：
- data-gameid=<id>            → source_game_id
- .title a 内文(去 high-light span) → name（权威匹配锚点）
- .sr-img-con .pic img@src    → icon_url（media.9game.cn CDN）
- .des                        → 分类（如 "休闲游戏"），落 tags
- .text                       → description（补全核心价值，搜索页即给全文）
- .score .oran               → score（可能缺，缺则 None）
- .down android / .down ios  → platforms（`down no` = 该平台不可下 = 不计入）

**兜底陷阱**：九游对搜不到的词会返回一个不相关游戏（实测 "不存在的游戏xyz123" →
"妈妈把我的游戏藏起来了3"）。因此**必须**用调用方注入的 matcher 校验 top 卡片名与 name
归一化相等，不匹配即返回 None（判 miss），绝不误合并。
"""

from __future__ import annotations

import gzip
import html
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

from ..types import PLATFORM_ANDROID, PLATFORM_IOS, SOURCE_NINEGAME, Game

FETCH_MODE = "http_direct"

_BASE = "https://www.9game.cn"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


def _exact_match(candidate, target):
    return candidate == target


def _fetch(url: str, timeout: int = 15) -> str | None:
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            return None
        raw = resp.read()
        if "gzip" in (resp.headers.get("Content-Encoding") or ""):
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def _strip_tags(s: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _first_card(text: str) -> str | None:
    """截出首个 sr-poker 卡片块（到下一个 sr-poker 或定长窗口为止）。"""
    i = text.find('class="sr-poker"')
    if i < 0:
        return None
    j = text.find('class="sr-poker"', i + 5)
    return text[i : j if j > 0 else i + 4000]


def _one(block: str, pattern: str) -> str | None:
    m = re.search(pattern, block, re.S)
    return m.group(1) if m else None


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_card(block: str) -> Game | None:
    gid = _one(block, r'data-gameid="(\d+)"')
    title = _one(block, r'<div class="title">\s*<a[^>]*>(.*?)</a>')
    name = _strip_tags(title) if title else (_one(block, r'<img[^>]+alt="([^"]*)"') or "").strip()
    if not name:
        return None
    des = _strip_tags(_one(block, r'<p class="des">(.*?)</p>'))
    desc = _strip_tags(_one(block, r'<p class="text">(.*?)</p>')) or None
    score = _num(_one(block, r'<span class="oran">\s*([\d.]+)\s*</span>'))
    icon = _one(block, r'<img\s+src="([^"]+)"')
    plats: list[str] = []
    if re.search(r'class="down[^"]*\bandroid\b', block):
        plats.append(PLATFORM_ANDROID)
    if re.search(r'class="down[^"]*\bios\b', block):
        plats.append(PLATFORM_IOS)
    return Game(
        source=SOURCE_NINEGAME,
        game_id=str(gid) if gid else name,
        name=name,
        score=score,
        tags=[des] if des else [],
        platforms=plats,
        comment_count=None,
        icon_url=icon or None,
        screenshot_urls=[],
        description=desc,
        raw={"gameid": gid, "category": des},
    )


def search_by_name(name, *, matcher=_exact_match) -> Game | None:
    """按游戏名搜九游，返回首个富卡片解析出的 Game；top 卡片名与 name 不匹配则返回 None。

    matcher(候选名, name) -> bool，调用方注入归一化匹配放宽格式差异。网络/解析异常向上抛，
    由 `_collect_from_all_sources` 记为 error（与 miss 区分，不计入软删）。
    """
    text = _fetch(f"{_BASE}/search/?keyword={quote(name)}")
    if not text:
        return None
    block = _first_card(text)
    if block is None:
        return None
    game = _parse_card(block)
    if game is None:
        return None
    return game if matcher(game.name, name) else None
