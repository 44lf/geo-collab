"""提示词模板 platform 字段 + 过滤语义。

platform 是与 scope 正交的新维度：已知取值 {"xiaohongshu","toutiao","wechat_mp"}，
null=通用。过滤语义：传 platform → 命中该 platform 或通用（platform IS NULL）；
不传 platform → 不做该维度过滤。
"""

import pytest

from server.tests.utils import build_test_app

pytestmark = pytest.mark.mysql


def _make(app, **kw):
    from server.app.modules.prompt_templates.models import PromptTemplate

    with app.session_factory() as db:
        t = PromptTemplate(
            name=kw.get("name", "t"),
            content="c",
            scope="generation",
            is_system=True,
            platform=kw.get("platform"),
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return t.id


def test_platform_filter_returns_target_and_generic(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.prompt_templates.service import list_prompt_templates

        _make(app, name="xhs", platform="xiaohongshu")
        _make(app, name="generic", platform=None)
        _make(app, name="toutiao", platform="toutiao")

        with app.session_factory() as db:
            rows = list_prompt_templates(db, scope="generation", platform="xiaohongshu")
            names = {r.name for r in rows}
            assert "xhs" in names and "generic" in names  # 专属 + 通用
            assert "toutiao" not in names  # 别的平台专属不返回

            # 不传 platform → 全部
            allrows = list_prompt_templates(db, scope="generation")
            assert {"xhs", "generic", "toutiao"} <= {r.name for r in allrows}
    finally:
        app.cleanup()


def test_validate_platform(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.prompt_templates.service import _validate_platform
        from server.app.shared.errors import ValidationError

        _validate_platform(None)  # 通用，OK
        _validate_platform("xiaohongshu")  # OK
        with pytest.raises(ValidationError):
            _validate_platform("bogus")
    finally:
        app.cleanup()


def test_create_and_update_platform_round_trip(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.prompt_templates.service import (
            create_prompt_template,
            get_prompt_template,
            update_prompt_template,
        )

        with app.session_factory() as db:
            template = create_prompt_template(
                db,
                name="t1",
                content="c1",
                scope="generation",
                platform="toutiao",
            )
            db.commit()
            template_id = template.id

        # 重新取出，确认 create 时 platform 落库
        with app.session_factory() as db:
            fetched = get_prompt_template(db, template_id)
            assert fetched is not None
            assert fetched.platform == "toutiao"

        # update 传具体平台 → 设置为该平台
        with app.session_factory() as db:
            fetched = get_prompt_template(db, template_id)
            assert fetched is not None
            update_prompt_template(
                db,
                fetched,
                name="t1-renamed",
                content="c1-updated",
                platform="wechat_mp",
            )
            db.commit()

        with app.session_factory() as db:
            fetched = get_prompt_template(db, template_id)
            assert fetched is not None
            assert fetched.platform == "wechat_mp"

        # update 传空字符串 → 切回通用（None）
        with app.session_factory() as db:
            fetched = get_prompt_template(db, template_id)
            assert fetched is not None
            update_prompt_template(
                db,
                fetched,
                name="t1-renamed",
                content="c1-updated",
                platform="",
            )
            db.commit()

        with app.session_factory() as db:
            fetched = get_prompt_template(db, template_id)
            assert fetched is not None
            assert fetched.platform is None

        # create 传空字符串 → 与 update 对齐，直接归一化为通用（None）
        with app.session_factory() as db:
            template2 = create_prompt_template(
                db,
                name="t2",
                content="c2",
                scope="generation",
                platform="",
            )
            db.commit()
            template2_id = template2.id

        with app.session_factory() as db:
            fetched2 = get_prompt_template(db, template2_id)
            assert fetched2 is not None
            assert fetched2.platform is None
    finally:
        app.cleanup()
