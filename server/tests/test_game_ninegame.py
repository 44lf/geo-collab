"""九游(9game.cn)按名搜索源的解析纯函数测试（无 DB、无网络）。

补全腿 `sources/ninegame.py`：只打搜索页、解析首个 sr-poker 富卡片。重点覆盖
- `_parse_card` 字段抽取（含去 high-light span 得干净游戏名）
- `_first_card` 只取首个卡片
- `_num` 边界（None / 非正 / 合法）
- `search_by_name` 的 **matcher 兜底闸**：九游对搜不到的词会返回不相关游戏，
  必须靠 matcher 判 miss 返回 None，绝不误合并（生产实测的兜底陷阱）。
"""

from __future__ import annotations

from server.app.modules.game_library.sources import ninegame

# 一张贴近生产真实结构的 sr-poker 卡片：标题内嵌 high-light span、含分类/简介/评分/安卓下载。
_CARD = """
<div class="sr-poker" data-gameid="12345">
  <div class="sr-img-con"><span class="pic">
    <img src="https://media.9game.cn/icon.png" alt="餐厅养成记">
  </span></div>
  <div class="sr-info">
    <div class="title"><a href="/canting/" target="_blank">餐厅<span class="high-light-f60">养成</span>记</a></div>
    <p class="des">经营游戏</p>
    <p class="text">开一家餐厅，慢慢经营做大做强。</p>
    <div class="score"><span class="oran">8.5</span></div>
    <div class="down-con"><a class="down android" href="#">安卓下载</a></div>
  </div>
</div>
"""


def test_parse_card_extracts_all_fields():
    g = ninegame._parse_card(_CARD)
    assert g is not None
    assert g.source == "ninegame"
    assert g.game_id == "12345"
    # 标题里的 high-light span 必须被剥掉，得到干净游戏名（权威匹配锚点）。
    assert g.name == "餐厅养成记"
    assert g.score == 8.5
    assert g.tags == ["经营游戏"]  # .des 分类落 tags
    assert g.description == "开一家餐厅，慢慢经营做大做强。"
    assert g.icon_url == "https://media.9game.cn/icon.png"
    assert g.platforms == ["android"]  # 只有安卓下载按钮
    assert g.screenshot_urls == []  # 搜索页不给截图


def test_parse_card_strips_highlight_span():
    """关键词命中会被九游包成 <span class="high-light-f60">，解析必须还原完整名。"""
    block = (
        '<div class="sr-poker" data-gameid="7">'
        '<div class="title"><a href="/x/">王者<span class="high-light-f60">荣耀</span></a></div>'
        "</div>"
    )
    g = ninegame._parse_card(block)
    assert g is not None
    assert g.name == "王者荣耀"


def test_parse_card_no_name_returns_none():
    g = ninegame._parse_card('<div class="sr-poker" data-gameid="7"></div>')
    assert g is None


def test_first_card_picks_first_of_many():
    """搜索结果多张卡片时，只截首个 sr-poker（top hit）。"""
    two = (
        '<div class="sr-poker" data-gameid="1"><div class="title"><a href="#">头一个</a></div></div>'
        '<div class="sr-poker" data-gameid="2"><div class="title"><a href="#">第二个</a></div></div>'
    )
    block = ninegame._first_card(two)
    assert block is not None
    g = ninegame._parse_card(block)
    assert g is not None and g.name == "头一个" and g.game_id == "1"


def test_first_card_none_when_absent():
    assert ninegame._first_card("<html>no cards here</html>") is None


def test_num_guards():
    assert ninegame._num(None) is None
    assert ninegame._num("0") is None  # 非正 → None（评分缺省时九游给 0）
    assert ninegame._num("abc") is None
    assert ninegame._num("8.5") == 8.5


def test_search_by_name_returns_on_exact_match(monkeypatch):
    monkeypatch.setattr(ninegame, "_fetch", lambda url, timeout=15: _CARD)
    g = ninegame.search_by_name("餐厅养成记")
    assert g is not None
    assert g.name == "餐厅养成记"
    assert g.source == "ninegame"


def test_search_by_name_matcher_gate_rejects_unrelated(monkeypatch):
    """兜底陷阱：搜不到的词九游返回一个不相关游戏；默认 _exact_match 必须判 miss → None。"""
    unrelated = (
        '<div class="sr-poker" data-gameid="999">'
        '<div class="title"><a href="#">妈妈把我的游戏藏起来了3</a></div></div>'
    )
    monkeypatch.setattr(ninegame, "_fetch", lambda url, timeout=15: unrelated)
    assert ninegame.search_by_name("不存在的游戏xyz123") is None


def test_search_by_name_custom_matcher_injected(monkeypatch):
    """调用方注入的 matcher 生效：放行任意名（生产用归一化 matcher 放宽格式差异）。"""
    monkeypatch.setattr(ninegame, "_fetch", lambda url, timeout=15: _CARD)
    g = ninegame.search_by_name("餐厅养成记（豪华版）", matcher=lambda cand, target: True)
    assert g is not None and g.name == "餐厅养成记"


def test_search_by_name_empty_fetch_returns_none(monkeypatch):
    monkeypatch.setattr(ninegame, "_fetch", lambda url, timeout=15: None)
    assert ninegame.search_by_name("原神") is None
