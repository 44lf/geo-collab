import pytest

from server.tests.utils import build_test_app


@pytest.mark.mysql
def test_h5_login_disabled(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.core import config

        monkeypatch.setenv("GEO_FEISHU_H5_ENABLED", "false")
        config.get_settings.cache_clear()
        test_app.client.cookies.clear()
        r = test_app.client.post("/api/feishu/h5-login", json={"code": "c"})
        assert r.status_code == 200
        assert r.json()["authenticated"] is False
        assert r.json()["reason"] == "disabled"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_h5_login_unbound(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.core import config
        from server.app.modules.feishu import router as feishu_router

        monkeypatch.setenv("GEO_FEISHU_H5_ENABLED", "true")
        config.get_settings.cache_clear()
        monkeypatch.setattr(feishu_router, "resolve_open_id", lambda code: "ou_unbound")
        test_app.client.cookies.clear()
        r = test_app.client.post("/api/feishu/h5-login", json={"code": "c"})
        assert r.json()["authenticated"] is False
        assert r.json()["reason"] == "unbound"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_h5_login_bound_sets_cookie(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.core import config
        from server.app.modules.feishu import router as feishu_router
        from server.app.modules.system.models import User

        monkeypatch.setenv("GEO_FEISHU_H5_ENABLED", "true")
        config.get_settings.cache_clear()
        with test_app.session_factory() as db:
            u = db.query(User).filter(User.username == "testadmin").first()
            u.feishu_open_id = "ou_admin"
            db.commit()
        monkeypatch.setattr(feishu_router, "resolve_open_id", lambda code: "ou_admin")
        test_app.client.cookies.clear()
        r = test_app.client.post("/api/feishu/h5-login", json={"code": "c"})
        assert r.json()["authenticated"] is True
        assert "access_token=" in r.headers.get("set-cookie", "")
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_h5_bind_fills_open_id_when_authed(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.core import config
        from server.app.modules.feishu import router as feishu_router
        from server.app.modules.system.models import User

        monkeypatch.setenv("GEO_FEISHU_H5_ENABLED", "true")
        config.get_settings.cache_clear()
        monkeypatch.setattr(feishu_router, "resolve_open_id", lambda code: "ou_bind")
        # test_app.client 默认带 admin cookie
        r = test_app.client.post("/api/feishu/h5-bind", json={"code": "c"})
        assert r.status_code == 200, r.text
        assert r.json()["bound"] is True
        with test_app.session_factory() as db:
            u = db.query(User).filter(User.username == "testadmin").first()
            assert u.feishu_open_id == "ou_bind"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_h5_bind_requires_login(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.core import config

        monkeypatch.setenv("GEO_FEISHU_H5_ENABLED", "true")
        config.get_settings.cache_clear()
        test_app.client.cookies.clear()
        r = test_app.client.post("/api/feishu/h5-bind", json={"code": "c"})
        assert r.status_code == 401
    finally:
        test_app.cleanup()
