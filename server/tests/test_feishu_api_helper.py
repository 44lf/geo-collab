def test_feishu_api_injects_bearer_and_retries_on_invalid_token(monkeypatch):
    from server.app.shared import feishu_bitable as fb

    calls = []
    tokens = iter(["tok1", "tok2"])

    monkeypatch.setattr(fb, "get_tenant_access_token", lambda force=False: next(tokens))

    def fake_http_json(method, url, *, headers=None, body=None, timeout=15):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
        # 第一次返回 token 失效码，触发刷新重试；第二次成功
        if len(calls) == 1:
            return {"code": 99991663, "msg": "invalid token"}
        return {"code": 0, "data": {"ok": 1}}

    monkeypatch.setattr(fb, "_http_json", fake_http_json)

    out = fb.feishu_api("POST", "/im/v1/messages", body={"x": 1})
    assert out == {"code": 0, "data": {"ok": 1}}
    assert len(calls) == 2
    assert calls[0]["headers"]["Authorization"] == "Bearer tok1"
    assert calls[1]["headers"]["Authorization"] == "Bearer tok2"
    assert calls[0]["url"].endswith("/im/v1/messages")
