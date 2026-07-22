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
