"""TapTap 分类搜索(裸 HTTP 直连,2026-07-13 抓包确认)。

接口: GET https://www.taptap.cn/webapiv2/app-tag/v1/by-tag?X-UA=<UA>&tag=<分类名>&sort=<SORT>&from=<OFFSET>&limit=<N>
- tag 直接传分类中文名(URL 编码),不像百度需要 tagId 映射表。CATEGORIES 里的 27 个(取自
  gate/v3/categories?type=more,2026-07-13 抓取)只是"精选"分类,不是全部合法 tag —— 实测很多不在
  这 27 个里的词(如"经营""RPG""角色扮演""模拟经营")一样能查到语义相符的结果;但不存在的 tag
  (比如百度那边的原词"策略经营"直接搬过来)接口会返回 HTTP 404,不是静默返回空列表,search() 会
  把 404 转成 ValueError,调用方按需自己试其它近义词。
- limit 服务端硬性上限 20,传大于 20 会 400(`limit failed on the 'max' tag`),search() 会在
  page_size > 20 时提前抛 ValueError,不会真的发出这个请求;需要更多结果用 page 翻页,不要加大
  page_size。
- X-UA 是必填的自定义签名参数(放在 query string 里,不是普通 HTTP header),格式:
  V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android&UID=<uuid>&OS=Windows&OSV=10&DT=PC
  实测 UID 可以是任意随机 UUID,不需要绑定真实登录态,也不需要带 Cookie,一样返回 200。
- sort: hits=热门(默认)/updated=最新/score=评分,均已抓包确认(score 是 TapTap 独有,百度没有对应项,
  统一接口的 ORDER_HOT/ORDER_NEW 只映射 hits/updated,score 排序暂不通过统一接口暴露)。
- from/limit 是 offset 分页,不是页码,已验证 from=3,limit=3 不会和 from=0,limit=3 的结果重叠。
- 该接口没有服务端的平台(Android/iOS)过滤参数,不像百度的 platId。平台由本模块按
  identifier(Android 包名非空)/itunes_id(iOS ID 非空)客户端推导,search() 里的 platform
  参数是客户端过滤(先拿 page_size 条,再筛选),不是服务端过滤,可能导致返回条数少于 page_size。
- 返回的 list 视图不含该游戏的完整标签列表,tags 字段用查询用的分类名本身填充,仅作占位。
- 列表视图不含截图/图集字段,screenshot_urls 恒为空列表。
- identifier(Android 包名,如 "com.xd.xdt")list/detail 两个接口都有,直接映射到
  Game.android_package;itunes_id(iOS App ID)目前只用于平台判断,未单独暴露成字段。
- description.text 是自带 HTML(<br/>、<br class="..."/> 等标签)的简介,list/detail 都有,
  detail 的更完整但服务端本身也会截断(结尾出现"...");_clean_description() 剥掉标签后
  映射到 Game.description,不做截断处理。

get_detail(game_id) 按需查询单个游戏详情,补全 search() 拿不到的数据(2026-07-13 抓包确认):
接口: GET https://www.taptap.cn/webapiv2/app/v6/detail?X-UA=<UA>&id=<game_id>
- 同样只需要 X-UA,不需要 Cookie/登录态。
- tags 是真实完整标签列表(取 tags[].value),不再是占位的查询分类名。
- screenshots 是真实截图列表(取 original_url,取不到则退回 url)。
- 平台字段改用 platform_info.supported_platforms(取 [].key),比 search() 里
  identifier/itunes_id 是否非空的客户端推断更权威 —— 已验证有预约(未上线)游戏
  identifier 为空字符串,但 platform_info 正确列出了它计划支持的平台。
  该字段还会给出 search() 里没有的 "pc" 平台。search() 暂不改用这个字段,是因为
  要拿到它必须逐个游戏调用本接口,N+1 请求成本对列表查询来说太贵。
- 响应结构是 data.app.{...},比 search() 那边的 data.list[] 多包一层 "app",取字段时
  容易漏包,已踩过一次坑(第一版实现直接用 data.data 导致全部字段读成 None)。

search_by_name(name) 按游戏名关键词全站搜索(2026-07-14 抓包确认,用户提供的真实 curl):
接口: POST https://www.taptap.cn/webapiv2/search/v6/agg-search?X-UA=<UA>
body(multipart/form-data): kw=<name>&types=mix
- 有 CSRF 校验,直接 POST 会 400 csrf_token_expired。必须先拿任意 webapiv2 GET 请求换到的
  XSRF-TOKEN cookie,再原样回传到 X-XSRF-TOKEN header(_bootstrap_xsrf 干这件事)。
- 响应 data.list[] 是若干 group,group.list[] 里按 type 混排 brand(游戏本身)/moment(帖子)/
  mix_app(同开发商其它游戏)等类型,搜索本身是模糊匹配,同名前缀/系列作品也会混进结果,
  所以只认 type=="brand" 且 brand.app.title 与 name 完全一致的那条。
- brand.app.tags 是真实标签,但是被截断过的预览列表,不是完整标签(2026-07-14 实测:某游戏详情页
  实际有 5 个标签,agg-search 只返回前 3 个)。仍然需要再调一次 get_detail 才能拿到完整标签,
  跟 by-tag 查询那种纯占位标签不是一回事,但也不能直接当完整数据用——registry.recommend() 命中
  search_by_name 结果后会自动补一次 get_detail,不要在别的调用点省略这一步。
- brand.app.supported_platforms 是直接挂在 app 下的([{"key":"android"},...]),跟 get_detail
  里嵌一层 platform_info 的结构不一样,不要混用同一套解析代码。
- brand.stat 只有 hits_total/fans_count/bought_count/reserve_count,没有评论数,
  Game.comment_count 对 search_by_name 的结果恒为 None。
"""

import html
import http.cookiejar
import json
import re
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from ..types import ORDER_HOT, ORDER_NEW, PLATFORM_ANDROID, PLATFORM_IOS, PLATFORM_PC, Game

FETCH_MODE = "http_direct"

_API = "https://www.taptap.cn/webapiv2/app-tag/v1/by-tag"
_API_DETAIL = "https://www.taptap.cn/webapiv2/app/v6/detail"
_API_SEARCH = "https://www.taptap.cn/webapiv2/search/v6/agg-search"
_HEADERS = {
    "Referer": "https://www.taptap.cn/categories/all",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

# 分类名(tag 参数直接用中文名,不需要 ID),取自 gate/v3/categories?type=more(2026-07-13 抓取)
CATEGORIES = {
    name: name
    for name in [
        "卡牌",
        "射击",
        "二次元",
        "Roguelike",
        "解谜",
        "文字",
        "音游",
        "女性向",
        "养成",
        "沙盒",
        "开放世界",
        "MMORPG",
        "武侠",
        "国风",
        "竞速",
        "益智",
        "Steam移植",
        "UP主推荐",
        "生存",
        "MOBA",
        "放置",
        "塔防",
        "像素",
        "治愈",
        "末日",
        "格斗",
        "魔性",
    ]
}

_ORDER_MAP = {ORDER_HOT: "hits", ORDER_NEW: "updated"}


_TAG_RE = re.compile(r"<[^>]+>")


def _clean_description(description):
    text = (description or {}).get("text")
    if not text:
        return None
    text = html.unescape(_TAG_RE.sub("", text))
    return text.strip() or None


def _x_ua():
    return (
        f"V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android"
        f"&UID={uuid.uuid4()}&OS=Windows&OSV=10&DT=PC"
    )


def search(category, *, platform=None, order=ORDER_HOT, page=1, page_size=20):
    if page_size > 20:
        raise ValueError(
            f"TapTap 单页 limit 服务端上限 20,收到 page_size={page_size}(用 page 翻页,不要加大 page_size)"
        )
    tag = CATEGORIES.get(category, category)
    params = {
        "X-UA": _x_ua(),
        "tag": tag,
        "sort": _ORDER_MAP.get(order, "hits"),
        "from": (page - 1) * page_size,
        "limit": page_size,
    }
    url = f"{_API}?{urlencode(params)}"
    req = Request(url, headers=_HEADERS)
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 404:
            raise ValueError(
                f"TapTap 分类不存在: {tag!r}(不在 CATEGORIES 精选列表里的词也可能有效,但这个 404 了,换个词试试)"
            ) from e
        raise
    raw_list = (data.get("data") or {}).get("list") or []
    games = [_to_game(g, tag) for g in raw_list]
    if platform is not None:
        games = [g for g in games if platform in g.platforms]
    return games


def _to_game(g, tag):
    platforms = []
    if g.get("identifier"):
        platforms.append(PLATFORM_ANDROID)
    if g.get("itunes_id"):
        platforms.append(PLATFORM_IOS)
    score = (g.get("stat") or {}).get("rating", {}).get("score")
    icon = g.get("icon") or {}
    return Game(
        source="taptap",
        game_id=str(g.get("id")),
        name=g.get("title"),
        score=float(score) if score else None,
        tags=[tag],
        platforms=platforms,
        comment_count=(g.get("stat") or {}).get("review_count"),
        icon_url=icon.get("original_url") or icon.get("url"),
        screenshot_urls=[],
        android_package=g.get("identifier") or None,
        description=_clean_description(g.get("description")),
        raw=g,
    )


_PLATFORM_KEY_MAP = {"android": PLATFORM_ANDROID, "ios": PLATFORM_IOS, "pc": PLATFORM_PC}


def get_detail(game_id):
    params = {"X-UA": _x_ua(), "id": game_id}
    url = f"{_API_DETAIL}?{urlencode(params)}"
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    g = (data.get("data") or {}).get("app") or {}
    tags = [t.get("value") for t in (g.get("tags") or []) if t.get("value")]
    screenshots = [
        s.get("original_url") or s.get("url")
        for s in (g.get("screenshots") or [])
        if s.get("original_url") or s.get("url")
    ]
    supported = (g.get("platform_info") or {}).get("supported_platforms") or []
    platforms = [
        _PLATFORM_KEY_MAP.get(p.get("key"), p.get("key")) for p in supported if p.get("key")
    ]
    score = (g.get("stat") or {}).get("rating", {}).get("score")
    icon = g.get("icon") or {}
    return Game(
        source="taptap",
        game_id=str(g.get("id")),
        name=g.get("title"),
        score=float(score) if score else None,
        tags=tags,
        platforms=platforms,
        comment_count=(g.get("stat") or {}).get("review_count"),
        icon_url=icon.get("original_url") or icon.get("url"),
        screenshot_urls=screenshots,
        android_package=g.get("identifier") or None,
        description=_clean_description(g.get("description")),
        raw=g,
    )


def _bootstrap_xsrf(opener):
    """agg-search 有 CSRF 校验,直接 POST 会 400 csrf_token_expired(2026-07-14 抓包确认)。
    任意 webapiv2 GET 请求的响应都会 Set-Cookie 一个 XSRF-TOKEN,必须先换到这个 cookie,
    再原样回传到 POST 请求的 X-XSRF-TOKEN header 才能过校验。这里借用已验证稳定的 by-tag
    接口(tag 随便传一个存在的分类,只是为了换票,不代表业务含义),page_size=1 把请求做到最小。
    """
    params = {"X-UA": _x_ua(), "tag": "卡牌", "sort": "hits", "from": 0, "limit": 1}
    req = Request(f"{_API}?{urlencode(params)}", headers=_HEADERS)
    with opener.open(req, timeout=10):
        pass


def _multipart_encode(fields):
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        )
    parts.append(f"--{boundary}--\r\n")
    return boundary, "".join(parts).encode("utf-8")


def _to_game_from_brand(app):
    tags = [t.get("value") for t in (app.get("tags") or []) if t.get("value")]
    supported = app.get("supported_platforms") or []
    platforms = [
        _PLATFORM_KEY_MAP.get(p.get("key"), p.get("key")) for p in supported if p.get("key")
    ]
    score = app.get("score")
    icon = app.get("icon") or {}
    return Game(
        source="taptap",
        game_id=str(app.get("id")),
        name=app.get("title"),
        score=float(score) if score else None,
        tags=tags,
        platforms=platforms,
        comment_count=None,  # brand.stat 只有 hits_total/fans_count/bought_count/reserve_count,没有评论数
        icon_url=icon.get("original_url") or icon.get("url"),
        screenshot_urls=[],
        android_package=app.get("identifier") or None,
        description=_clean_description(app.get("description")),
        raw=app,
    )


def search_by_name(name):
    """按游戏名关键词全站搜索(不是分类查询),用于"给定一个具体游戏,找到它本身"的场景。

    接口: POST https://www.taptap.cn/webapiv2/search/v6/agg-search?X-UA=<UA>
    body(multipart/form-data): kw=<name>&types=mix
    2026-07-14 抓包确认(用户提供的真实 curl,凭空猜的 search-all/general-search 等路径都是 404,
    不要在没有新抓包验证的情况下改动这个 URL/请求方式)。需要先调 _bootstrap_xsrf 换 CSRF token。

    返回结果 data.list[] 是若干个 group,每个 group.list[] 里的条目按 type 区分(brand=游戏本身,
    moment=攻略/帖子,mix_app=同开发商其它游戏,等等)。只看 type=="brand" 的条目,且要求
    brand.app.title 与 name 完全一致才当作命中——搜索是模糊匹配,同名前缀/系列作品也会混进来,
    不做精确比对会返错游戏。brand.app.tags 是真实标签,但是被截断过的预览列表,不是完整标签
    (实测某游戏详情页有 5 个标签,这里只返回 3 个)——需要完整标签的调用方(如
    registry.recommend())必须自己再补一次 get_detail,本函数不会自动补。

    找不到精确同名结果时返回 None(调用方可以据此判断 name 传的其实是个分类名,退回分类查询)。
    """
    cj = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cj))
    _bootstrap_xsrf(opener)
    xsrf = next((c.value for c in cj if c.name == "XSRF-TOKEN"), None)
    if not xsrf:
        raise RuntimeError("TapTap 没有换到 XSRF-TOKEN cookie,agg-search 大概率会被 CSRF 校验拒绝")

    boundary, body = _multipart_encode({"kw": name, "types": "mix"})
    url = f"{_API_SEARCH}?{urlencode({'X-UA': _x_ua()})}"
    headers = {
        **_HEADERS,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Referer": f"https://www.taptap.cn/search/{quote(name)}",
        "Origin": "https://www.taptap.cn",
        "X-XSRF-TOKEN": xsrf,
    }
    req = Request(url, data=body, headers=headers, method="POST")
    with opener.open(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    for group in (data.get("data") or {}).get("list") or []:
        for item in group.get("list") or []:
            if item.get("type") != "brand":
                continue
            app = (item.get("brand") or {}).get("app") or {}
            if app.get("title") == name:
                return _to_game_from_brand(app)
    return None
