def test_feishu_review_settings_from_env(monkeypatch):
    from server.app.core import config

    monkeypatch.setenv("GEO_PUBLIC_BASE_URL", "https://geo.example.com")
    monkeypatch.setenv("GEO_FEISHU_REVIEW_CARD_ENABLED", "true")
    monkeypatch.setenv("GEO_FEISHU_REVIEW_CHAT_ID", "oc_abc")
    monkeypatch.setenv("GEO_FEISHU_H5_ENABLED", "true")
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.feishu_public_base_url == "https://geo.example.com"
    assert s.feishu_review_card_enabled is True
    assert s.feishu_review_chat_id == "oc_abc"
    assert s.feishu_h5_enabled is True
    config.get_settings.cache_clear()


def test_feishu_review_settings_defaults(monkeypatch):
    from server.app.core import config

    for k in ("GEO_PUBLIC_BASE_URL", "GEO_FEISHU_REVIEW_CARD_ENABLED",
              "GEO_FEISHU_REVIEW_CHAT_ID", "GEO_FEISHU_H5_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEO_JWT_SECRET", "x")
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.feishu_public_base_url is None
    assert s.feishu_review_card_enabled is False
    assert s.feishu_h5_enabled is False
    config.get_settings.cache_clear()
