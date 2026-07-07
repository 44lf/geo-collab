import pytest

from server.tests.utils import build_test_app


@pytest.mark.mysql
def test_bind_open_id_idempotent_and_conflict(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.feishu import service
        from server.app.modules.system.models import User
        from server.app.shared.errors import ConflictError

        with test_app.session_factory() as db:
            u1 = User(username="r1", role="operator", is_active=True, must_change_password=False)
            u1.set_password("pw-123456")
            u2 = User(username="r2", role="operator", is_active=True, must_change_password=False)
            u2.set_password("pw-123456")
            db.add_all([u1, u2])
            db.commit()

            service.bind_open_id(db, u1, "ou_x")
            db.commit()
            assert u1.feishu_open_id == "ou_x"

            # 幂等：再绑同一人（已非空）不报错、不改
            service.bind_open_id(db, u1, "ou_other")
            db.commit()
            assert u1.feishu_open_id == "ou_x"

            # 冲突：把 ou_x 绑到另一个人 → ConflictError
            with pytest.raises(ConflictError):
                service.bind_open_id(db, u2, "ou_x")
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_find_user_by_open_id(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.feishu import service
        from server.app.modules.system.models import User

        with test_app.session_factory() as db:
            u = User(username="r3", role="operator", is_active=True, must_change_password=False)
            u.set_password("pw-123456")
            u.feishu_open_id = "ou_find"
            db.add(u)
            db.commit()

            found = service.find_user_by_open_id(db, "ou_find")
            assert found is not None and found.username == "r3"
            assert service.find_user_by_open_id(db, "ou_none") is None
    finally:
        test_app.cleanup()


def test_resolve_open_id_uses_oauth_v2(monkeypatch):
    from server.app.modules.feishu import service

    seq = []

    def fake_http_json(method, url, *, headers=None, body=None, timeout=15):
        seq.append(url)
        if "oauth/token" in url:
            return {"code": 0, "access_token": "u_at"}
        if "user_info" in url:
            return {"code": 0, "data": {"open_id": "ou_resolved"}}
        raise AssertionError(url)

    monkeypatch.setattr(service, "_http_json", fake_http_json, raising=False)
    monkeypatch.setattr("server.app.modules.feishu.service._http_json", fake_http_json, raising=False)
    monkeypatch.setattr(
        "server.app.modules.feishu.service.get_settings",
        lambda: type("S", (), {"feishu_app_id": "a", "feishu_app_secret": "b"})(),
    )
    assert service.resolve_open_id("code123") == "ou_resolved"
