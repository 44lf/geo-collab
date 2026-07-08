from server.app.shared.feishu_card import build_review_card, build_review_link


def test_build_review_link_applink_when_app_id():
    # 有 app_id → 飞书网页应用 AppLink（web_app/open），飞书端内以「网页应用」身份打开，
    # 注入 window.h5sdk → H5 免登链路才生效。域名不进链接（取自后台主页 URL），只带 path。
    link = build_review_link(
        article_id=1646,
        base_url="https://geo.example.com",
        app_id="cli_abc123",
    )
    assert link == (
        "https://applink.feishu.cn/client/web_app/open?appId=cli_abc123&path=/article/1646"
    )


def test_build_review_link_fallback_when_no_app_id():
    # 无 app_id → 回落裸永久链接（普通浏览器手动登录），保持未配飞书应用时可用。
    link = build_review_link(
        article_id=1646,
        base_url="https://geo.example.com/",
        app_id=None,
    )
    assert link == "https://geo.example.com/article/1646"


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
    content = card["elements"][0]["text"]["content"]
    # 特殊 lark_md 字符被转义（不裸出未转义的 * _ `）
    # 注意：用原始 content 字段断言，而非 str(card) —— dict 的 __repr__ 会对内部字符串再 repr()
    # 一次，把 content 里已转义的单反斜杠又打印成双反斜杠，导致 str(card) 上的断言看错字符数。
    assert "a\\*b\\_c\\`d" in content
    assert "a*b_c`d" not in content


def test_send_review_card_posts_interactive(monkeypatch):
    from server.app.core import config
    from server.app.shared import feishu_card as fc

    monkeypatch.setenv("GEO_FEISHU_REVIEW_CARD_ENABLED", "true")
    monkeypatch.setenv("GEO_FEISHU_REVIEW_CHAT_ID", "oc_abc")
    monkeypatch.setenv("GEO_JWT_SECRET", "x")
    config.get_settings.cache_clear()

    captured = {}

    def fake_feishu_api(method, path, *, body=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"code": 0, "data": {"message_id": "om_123"}}

    monkeypatch.setattr(fc, "feishu_api", fake_feishu_api, raising=False)
    # feishu_api 在 feishu_card 内从 feishu_bitable import，需按实际引用路径打桩
    monkeypatch.setattr("server.app.shared.feishu_card.feishu_api", fake_feishu_api, raising=False)

    mid = fc.send_review_card(
        chat_id="oc_abc",
        article_id=1,
        title="t",
        question="q",
        score=80,
        decision="approved",
        review_url="https://x/article/1",
    )
    assert mid == "om_123"
    assert captured["path"].startswith("/im/v1/messages")
    assert "chat_id" in captured["path"]  # receive_id_type=chat_id
    assert captured["body"]["msg_type"] == "interactive"
    assert isinstance(captured["body"]["content"], str)  # content 必须是 JSON 字符串
    config.get_settings.cache_clear()


def test_send_review_card_disabled_returns_none(monkeypatch):
    from server.app.core import config
    from server.app.shared import feishu_card as fc

    monkeypatch.setenv("GEO_FEISHU_REVIEW_CARD_ENABLED", "false")
    monkeypatch.setenv("GEO_JWT_SECRET", "x")
    config.get_settings.cache_clear()
    assert (
        fc.send_review_card(
            chat_id="oc_abc",
            article_id=1,
            title="t",
            question="q",
            score=None,
            decision=None,
            review_url="https://x/article/1",
        )
        is None
    )
    config.get_settings.cache_clear()
