import pytest

from server.tests.utils import build_test_app


@pytest.mark.mysql
def test_login_sets_access_cookie_flags(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        # build_test_app 已建 admin(testadmin/testadmin)
        client = test_app.client
        client.cookies.clear()
        r = client.post("/api/auth/login", json={"username": "testadmin", "password": "testadmin"})
        assert r.status_code == 200, r.text
        set_cookie = r.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/" in set_cookie
        assert "samesite=lax" in set_cookie.lower()
    finally:
        test_app.cleanup()


def test_set_access_cookie_helper_exists(monkeypatch):
    from fastapi import Response

    from server.app.core.security import set_access_cookie

    monkeypatch.setenv("GEO_JWT_SECRET", "x")
    resp = Response()
    set_access_cookie(resp, "tok123")
    raw = resp.raw_headers
    joined = b";".join(v for k, v in raw if k == b"set-cookie").decode()
    assert "access_token=tok123" in joined
    assert "httponly" in joined.lower()
