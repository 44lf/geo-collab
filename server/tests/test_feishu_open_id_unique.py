import pytest
from sqlalchemy.exc import IntegrityError

from server.tests.utils import build_test_app


@pytest.mark.mysql
def test_two_users_cannot_share_feishu_open_id(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.system.models import User

        with test_app.session_factory() as db:
            u1 = User(username="fa", role="operator", is_active=True, must_change_password=False)
            u1.set_password("pw-123456")
            u1.feishu_open_id = "ou_dup"
            db.add(u1)
            db.commit()

            u2 = User(username="fb", role="operator", is_active=True, must_change_password=False)
            u2.set_password("pw-123456")
            u2.feishu_open_id = "ou_dup"
            db.add(u2)
            with pytest.raises(IntegrityError):
                db.commit()

    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_multiple_users_may_have_null_feishu_open_id(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.system.models import User

        with test_app.session_factory() as db:
            for name in ("na", "nb"):
                u = User(username=name, role="operator", is_active=True, must_change_password=False)
                u.set_password("pw-123456")
                db.add(u)
            db.commit()  # 两个 NULL 不冲突
    finally:
        test_app.cleanup()
