from server.app.shared.feishu_card import build_review_card


def test_build_review_card_structure():
    card = build_review_card(
        article_id=1646,
        title="2026 合成类游戏推荐",
        question="有没有好玩的合成类游戏",
        score=88,
        decision="approved",
        review_url="https://geo.example.com/article/1646",
    )
    assert isinstance(card, dict)
    # header 含文章 id
    assert "1646" in str(card.get("header", {}))
    # 有一个 url 按钮指向 review_url
    dumped = str(card)
    assert "https://geo.example.com/article/1646" in dumped
    assert "查看文章" in dumped
    # 本期不含审批按钮
    assert "审核通过" not in dumped


def test_build_review_card_escapes_lark_md():
    card = build_review_card(
        article_id=1,
        title="a*b_c`d",
        question="q",
        score=None,
        decision=None,
        review_url="https://x/article/1",
    )
    dumped = str(card)
    # 特殊 lark_md 字符被转义（不裸出未转义的 * _ `）
    assert "a\\*b\\_c\\`d" in dumped or "a*b_c`d" not in dumped
