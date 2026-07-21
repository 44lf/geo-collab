"""百度乐玩·分类搜索(裸 HTTP 直连,非浏览器拦截,2026-07-13 抓包确认)。

接口: GET lewan.baidu.com/lewanapi?action=game_filter&typeId=3&tagId=<TAG>&platId=<PLAT>&order=<ORDER>&psize=<N>&pnum=<PAGE>
- typeId 固定 3 (手机游戏,小游戏=4/网页游戏=14 未接入,如需再扩展)
- tagId 见 CATEGORIES (2026-07-13 从 action=game_filter 不带参数的响应里抓取的完整映射,若百度改版新增/改名
  类型,该响应会变,需重新抓取覆盖 CATEGORIES)
- platId(请求过滤参数): 0=全部平台/3=Android/4=IOS
- order: 3=最热排序/2=最新排序(均已抓包确认); 其余排序值未验证,不要假设
- psize 无实测硬上限(试到 8000 条仍能一次性返回 200),但响应时间随条数线性变长
  (约 3ms/条,8000 条要 24s+),批量取数建议靠 pnum 分页多次请求,而不是堆大 psize。
- 该接口经测试可直接裸 HTTP 请求成功(200,带 Referer + X-Requested-With 即可),不需要 hotlist 框架里
  baidu.py 那种 aladdin_rank_games 接口的浏览器反爬绕过。
- 响应字段 gamePlatform 是字符串列表,如 ["IOS","Android"],和请求参数 platId 的编码方式不同,
  gameTags/gameOfficialPic 确认恒为 list,不需要额外的类型防御。
- gameDesc 是纯文本简介(无 HTML 标签,不需要清洗),直接映射到 Game.description。
- 该接口响应里没有 Android 包名/iOS ID 字段(不像 taptap 的 identifier/itunes_id),
  Game.android_package 对 baidu 结果恒为 None。

search_by_name(name) 按游戏名精确搜索(2026-07-14 抓包确认,用户提供的真实 curl):
接口: GET lewan.baidu.com/lewanapi?action=game_query&gameName=<name>
- 抓包 curl 带了完整登录态 Cookie(BDUSS 等),但实测裸请求(不带任何 Cookie,只有
  Referer + X-Requested-With,跟 search() 用同一份 _HEADERS)一样返回 200 正常数据,
  登录态不是必需的。
- 响应结构是搜索框联想列表,字段比 game_filter 少很多:没有 gameScore/gamePlatform/
  commentCount/gameOfficialPic(截图)/gameDesc。类目信息在 gameTypes 里(如
  ["手游","策略经营"]),混杂了非分类项(比如"手游"本身不在 CATEGORIES 里),
  精确匹配时原样存进 Game.tags,调用方(registry.recommend)按需再用 CATEGORIES 过滤出
  真正能用来查分类池子的 tag。
- 找不到与 name 完全同名的结果时返回 None。
"""

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..types import ORDER_HOT, ORDER_NEW, PLATFORM_ANDROID, PLATFORM_IOS, Game

FETCH_MODE = "http_direct"

_API = "https://lewan.baidu.com/lewanapi"
_TYPE_ID = "3"  # 手机游戏
_HEADERS = {
    "Referer": "https://lewan.baidu.com/lewanhome?idfrom=",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0",
}

# typeId=3(手机游戏) 下的类型名 -> tagId,取自 action=game_filter 无参响应
CATEGORIES = {
    "全部类型": "0",
    "策略": "16290",
    "休闲": "16292",
    "策略经营": "56632",
    "其它": "56636",
    "模拟": "70472",
    "经营": "70490",
    "卡通": "88187",
    "动作": "16328",
    "冒险": "16201",
    "RPG": "88083",
    "角色扮演": "5914",
    "都市": "71516",
    "益智": "16204",
    "养成": "33889",
    "射击": "16323",
    "战争": "33272",
    "回合制": "70475",
    "战略": "136251",
    "枪械": "136249",
    "魔幻": "5920",
    "像素": "33222",
    "二次元": "65701",
    "MMORPG": "136250",
    "卡牌": "70481",
    "脑力": "136270",
    "日系": "104836",
    "竞速": "94968",
    "消除": "136267",
    "跑酷": "33867",
    "赛车": "33202",
    "观察": "94969",
    "解谜": "33226",
    "放置": "136253",
    "三国": "5923",
    "玄幻": "74476",
    "棋牌": "16334",
    "生存": "70473",
    "格斗": "33179",
    "怀旧": "94974",
}

_BAIDU_ORDER_HOT = 3  # 最热排序
_BAIDU_ORDER_NEW = 2  # 最新排序
_ORDER_MAP = {ORDER_HOT: _BAIDU_ORDER_HOT, ORDER_NEW: _BAIDU_ORDER_NEW}
_REQUEST_PLATFORM_TO_PLATID = {None: 0, PLATFORM_ANDROID: 3, PLATFORM_IOS: 4}
_RESULT_PLATFORM_NAME_MAP = {"Android": PLATFORM_ANDROID, "IOS": PLATFORM_IOS}


def search(category, *, platform=None, order=ORDER_HOT, page=1, page_size=20):
    tag_id = CATEGORIES.get(category, category)
    params = {
        "action": "game_filter",
        "typeId": _TYPE_ID,
        "tagId": tag_id,
        "platId": _REQUEST_PLATFORM_TO_PLATID.get(platform, 0),
        "order": _ORDER_MAP.get(order, _BAIDU_ORDER_HOT),
        "psize": page_size,
        "pnum": page,
    }
    url = f"{_API}?{urlencode(params)}"
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    game_list = ((data.get("result") or {}).get("data") or {}).get("gameList") or []
    return [_to_game(g) for g in game_list]


def _exact_match(candidate, target):
    return candidate == target


def search_by_name(name, *, matcher=_exact_match):
    """按游戏名精确搜索,用于"给定一个具体游戏,找到它本身"的场景(见模块顶部说明)。

    matcher(候选名, name) -> bool 决定命中口径,默认完全相等;调用方可注入归一化 matcher。
    找不到匹配结果时返回 None。
    """
    params = {"action": "game_query", "gameName": name}
    url = f"{_API}?{urlencode(params)}"
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = ((data.get("result") or {}).get("data")) or []
    for item in items:
        if matcher(item.get("gameName"), name):
            return _to_game_from_query(item)
    return None


def _to_game_from_query(g):
    return Game(
        source="baidu",
        game_id=str(g.get("gameId")),
        name=g.get("gameName"),
        score=None,
        tags=g.get("gameTypes") or [],
        platforms=[],
        comment_count=None,
        icon_url=g.get("gameIcon"),
        screenshot_urls=[],
        description=None,
        raw=g,
    )


def _to_game(g):
    return Game(
        source="baidu",
        game_id=str(g.get("gameId")),
        name=g.get("gameName"),
        score=float(g["gameScore"]) if g.get("gameScore") else None,
        tags=g.get("gameTags") or [],
        platforms=[_RESULT_PLATFORM_NAME_MAP.get(p, p) for p in (g.get("gamePlatform") or [])],
        comment_count=g.get("commentCount"),
        icon_url=g.get("gameIcon"),
        screenshot_urls=g.get("gameOfficialPic") or [],
        description=g.get("gameDesc") or None,
        raw=g,
    )
