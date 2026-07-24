"""应用宝(sj.qq.com)网页内嵌 JSON 解析源。榜单发现模式：list_page 批量 + detail 补全。

字段路径 2026-07-23 实地抠包确认（本机办公网 + 生产 ECS 47.115.134.13 均 200 可爬）：
- 列表/详情页均含 <script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">
  内嵌完整 JSON（注意标签带 crossorigin 属性，正则别写死 type="application/json">）。
- 游戏卡片节点特征：含非空 pkg_name + name/app_name。列表页首屏 21~40 款，评分/标签近 100% 命中。
- 详情页游戏主数据：dynamicCardResponse.data.components 里 data.name=='GameDetail' 的 component，
  其 data.itemData[0] 为主游戏；itemData 中含 comments 的元素给精选评论。
  ⚠️ 别错拿 pageProps.context.YYBAppInfo —— 那是"应用宝"下载器自己（name=应用宝、
  average_rating=4.26、pkg=com.tencent.android.qqdownloader），不是当前游戏。
- description 在列表页几乎恒空（仅焦点游戏有），要靠 detail() 补。
"""

import json
import re

from .model import Game

SOURCE = "yingyongbao"
_BASE = "https://sj.qq.com"
_SELF_PKG = "com.tencent.android.qqdownloader"  # 应用宝下载器自身，需剔除
_NEXT_RE = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# 榜单发现种子页：4 榜单 + 大分类 + 标签（tag_alias 与详情页 tags_st 自洽，可继续扩充）
LIST_PATHS = [
    "/hot-game-list",
    "/new-game-list",
    "/reserve-game-list",
    "/recommend-list",
    "/game/rpg",
    "/game/avg",
    "/game/xiuxianyizhi",
    "/game/chuangxinpinlei",
    "/tag/moba",
    "/tag/roguelike",
    "/tag/openworld",
    "/tag/sandbox",
    "/tag/rpg",
    "/tag/racing",
    "/tag/fighting",
    "/tag/chess",
]


def _page_props(text):
    m = _NEXT_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    return (data.get("props") or {}).get("pageProps") or {}


def _collect_cards(o, acc):
    """递归收集含非空 pkg_name + name 的游戏卡片 dict。"""
    if isinstance(o, dict):
        pkg = o.get("pkg_name")
        if isinstance(pkg, str) and pkg and (o.get("name") or o.get("app_name")):
            acc.append(o)
        for v in o.values():
            _collect_cards(v, acc)
    elif isinstance(o, list):
        for v in o:
            _collect_cards(v, acc)


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _tags_from(node):
    """优先 tags_st 结构化 tag_name，回落 tags 逗号串。"""
    st = node.get("tags_st")
    if st:
        try:
            arr = json.loads(st) if isinstance(st, str) else st
            names = [t.get("tag_name") for t in arr if t.get("tag_name")]
            if names:
                return names
        except Exception:
            pass
    raw = node.get("tags")
    if isinstance(raw, str) and raw:
        return [t for t in raw.split(",") if t]
    return []


def _shots_from(node):
    s = node.get("audited_snapshots")
    if isinstance(s, str) and s:
        return [u for u in s.split(",") if u.startswith("http")]
    return []


def _platforms_from(node):
    plats = ["android"]  # 应用宝主体是安卓包
    tags = node.get("tags") or ""
    if "iOS" in tags:
        plats.append("ios")
    return plats


def _card_to_game(node):
    return Game(
        source=SOURCE,
        source_id=node.get("pkg_name"),
        name=node.get("name") or node.get("app_name"),
        score=_num(node.get("average_rating")),
        tags=_tags_from(node),
        platforms=_platforms_from(node),
        icon_url=node.get("icon") or None,
        screenshot_urls=_shots_from(node),
        description=(node.get("description") or None),
        category=node.get("cate_name_new") or node.get("cate_name") or None,
        source_url=f"{_BASE}/appdetail/{node.get('pkg_name')}",
    )


def list_page(client, path):
    """抓一个榜单/分类/标签页 → (games:list[Game], status)。首屏，未翻页。"""
    status, text = client.get(_BASE + path)
    if status != 200 or not text:
        return [], status
    pp = _page_props(text)
    if pp is None:
        return [], status
    acc = []
    _collect_cards(pp, acc)
    games = [_card_to_game(n) for n in acc if n.get("pkg_name") != _SELF_PKG]
    return games, status


def _find_game_detail_item(pp):
    """从详情页 pageProps 定位游戏主数据与含 comments 的元素（按 data.name 语义锚点，不写死 index）。"""
    dyn = (pp.get("dynamicCardResponse") or {}).get("data") or {}
    for comp in dyn.get("components") or []:
        data = comp.get("data") or {}
        if data.get("name") == "GameDetail":
            items = data.get("itemData") or []
            main = items[0] if items else None
            comments_item = next((it for it in items if it.get("comments")), None)
            return main, comments_item
    return None, None


def _comments_from(item):
    out = []
    for c in (item or {}).get("comments") or []:
        content = (c.get("comment") or {}).get("content")
        if content:
            out.append({"user": (c.get("user") or {}).get("nickName"), "content": content})
    return out[:10]


def detail(client, pkg):
    """/appdetail/<pkg> 详情页 → 补全 description/完整截图/精选评论的 Game（拿不到返回 None）。"""
    status, text = client.get(f"{_BASE}/appdetail/{pkg}")
    if status != 200 or not text:
        return None
    pp = _page_props(text)
    if pp is None:
        return None
    main, comments_item = _find_game_detail_item(pp)
    if not main:
        return None
    g = _card_to_game(main)
    g.detail_fetched = True
    g.highlight_comments = _comments_from(comments_item)
    return g
