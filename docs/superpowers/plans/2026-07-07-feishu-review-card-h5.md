# 飞书审核卡片 + 端内 H5 免登（首登自绑）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每篇文章生成自评后，往飞书群发一张交互卡片（标题/ID/自评分/选题 + 「查看文章」链接指向已上线的 `/article/:id`），审核人飞书端内打开看全文，首次手动登录一次 GEO、之后按飞书身份自动免登。

**Architecture:** 出站发卡走自建应用 bot（`im/v1/messages` 交互卡）；全文展示复用已上线的永久链接页（零前端新页面）；免登复用现有 GEO 账号体系（`User.feishu_open_id` + 现有 JWT），首次未命中→手动登录→后台回填 open_id。现有 `/api/auth/login` 逻辑不改，仅抽 `set_access_cookie` 共用。

**Tech Stack:** FastAPI + SQLAlchemy/Alembic(MySQL) + pydantic-settings；飞书 OpenAPI（`authen/v2/oauth/token`、`im/v1/messages`）；FastMCP tool；React 19 + Vite + 飞书 H5 JSSDK。

关联设计：`docs/superpowers/specs/2026-07-07-feishu-review-card-h5-design.md`。

## Global Constraints

- **MySQL only**，无 SQLite 兼容；service 层抛命名异常（`ClientError`/`ConflictError`/`ValidationError`），**不抛裸 `ValueError`**。
- 后端 lint：ruff 选 E/F/I/B/UP，line-length=100，忽略 E501/B008；mypy 宽松。改后 `ruff check server/` + `ruff format server/` + `mypy server/app` 必绿。
- 测试 schema 由 `Base.metadata.create_all()` 从 **model** 建（`server/tests/utils.py:112`）——DB 级约束必须同时写进 model，不能只写迁移。
- 测试 DB 名须含 `test`；用 `build_test_app(monkeypatch)` 的测试须 `finally: test_app.cleanup()`；改环境后 `get_settings.cache_clear()`。
- MCP tool 一律 `async def` + 阻塞调用经 `anyio.to_thread.run_sync` 丢线程池（同步 tool 自调用会死锁）。
- **MCP 工具总数唯一真值** = `server/app/modules/mcp_catalog/connect_router.py:MCP_TOOLS_COUNT`，加 tool 必须同步改（当前 21）。
- MCP 端点未捕获异常走 `core/mcp_errors.mcp_exception_response(exc, context=...)`，不裸抛。
- 免登 `open_id` **按飞书应用隔离**：解析与手填必须同一自建应用。
- 发卡 / 免登默认**开关关闭**（`feishu_review_card_enabled` / `feishu_h5_enabled` 默认 False），不配则静默不发、不介入。
- loop bundle 内文件（`templates/skills/**`）改动必须 bump `server/app/modules/loop_skills/version.py` 并按 posix 串排序 + LF 重取 sha，真值以 CI 为准。
- 最新迁移 head = `0055`；本计划新增迁移 down_revision = `"0055"`。
- 迁移前先 `conda activate geo_xzpt`；测试用 `GEO_TEST_DATABASE_URL=mysql+pymysql://...geo_test pytest ... -q`（本机 conda 里 `conda activate` 在工具 shell 不生效，pytest 用 `env python` 全路径或已激活环境）。

---

### Task 1: `feishu_open_id` 唯一约束（model + 迁移 0056）

**Files:**
- Modify: `server/app/modules/system/models.py:30`
- Create: `server/alembic/versions/0056_feishu_open_id_unique.py`
- Test: `server/tests/test_feishu_open_id_unique.py`

**Interfaces:**
- Produces: `User.feishu_open_id` 带唯一索引 `uq_users_feishu_open_id`（MySQL 唯一索引允许多个 NULL）。

- [ ] **Step 1: 写失败测试**

`server/tests/test_feishu_open_id_unique.py`：
```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3307/geo_test pytest server/tests/test_feishu_open_id_unique.py -q`
Expected: FAIL（`test_two_users...` 没抛 IntegrityError，因当前无唯一约束）

- [ ] **Step 3: model 加唯一约束**

`server/app/modules/system/models.py:30`：
```python
    feishu_open_id: Mapped[str | None] = mapped_column(
        String(200), unique=True, nullable=True
    )
```

- [ ] **Step 4: 写迁移 0056**

`server/alembic/versions/0056_feishu_open_id_unique.py`：
```python
"""users.feishu_open_id 唯一索引（首登自绑防串号）

修订 ID: 0056
上一修订: 0055
创建日期: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("uq_users_feishu_open_id", "users", ["feishu_open_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_feishu_open_id", table_name="users")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `GEO_TEST_DATABASE_URL=...geo_test pytest server/tests/test_feishu_open_id_unique.py -q`
Expected: PASS（2 passed）
> 注：测试走 create_all，靠 model 的 `unique=True` 生效；迁移 0056 供已有生产库。

- [ ] **Step 6: Commit**

```bash
git add server/app/modules/system/models.py server/alembic/versions/0056_feishu_open_id_unique.py server/tests/test_feishu_open_id_unique.py
git commit -m "feat(feishu): users.feishu_open_id 唯一约束（model + 迁移 0056）"
```

---

### Task 2: 新增 4 个配置项

**Files:**
- Modify: `server/app/core/config.py:95`（`feishu_app_secret` 之后）
- Test: `server/tests/test_feishu_config.py`

**Interfaces:**
- Produces: `settings.feishu_public_base_url` / `feishu_review_card_enabled` / `feishu_review_chat_id` / `feishu_h5_enabled`。

- [ ] **Step 1: 写失败测试**

`server/tests/test_feishu_config.py`：
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest server/tests/test_feishu_config.py -q`
Expected: FAIL（`AttributeError`，Settings 无这些字段）

- [ ] **Step 3: config.py 追加字段**

`server/app/core/config.py`，`feishu_app_secret`（:95）之后插入：
```python
    # 飞书审核卡片 + 端内 H5 免登（B 档）
    feishu_public_base_url: str | None = None  # GEO_PUBLIC_BASE_URL 评审链接根（无尾斜杠）
    feishu_review_card_enabled: bool = False  # GEO_FEISHU_REVIEW_CARD_ENABLED 发卡总开关
    feishu_review_chat_id: str | None = None  # GEO_FEISHU_REVIEW_CHAT_ID 目标群 chat_id
    feishu_h5_enabled: bool = False  # GEO_FEISHU_H5_ENABLED H5 免登开关（复用 app_id/secret）
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest server/tests/test_feishu_config.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/core/config.py server/tests/test_feishu_config.py
git commit -m "feat(feishu): 新增 review-card / h5 免登 4 个配置项"
```

---

### Task 3: 抽 `set_access_cookie` helper（办法 A，登录行为不变）

**Files:**
- Modify: `server/app/core/security.py`（新增 helper）
- Modify: `server/app/modules/system/auth_router.py:116-126`（login 改调 helper）
- Test: `server/tests/test_set_access_cookie.py`

**Interfaces:**
- Produces: `set_access_cookie(response: fastapi.Response, token: str) -> None`（httponly / samesite=lax / path=/ / max_age=GEO_JWT_EXPIRE_HOURS*3600 / secure=settings.secure_cookie）。

- [ ] **Step 1: 写失败测试**

`server/tests/test_set_access_cookie.py`：
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest server/tests/test_set_access_cookie.py::test_set_access_cookie_helper_exists -q`
Expected: FAIL（`ImportError: cannot import name 'set_access_cookie'`）

- [ ] **Step 3: security.py 加 helper**

`server/app/core/security.py`（`create_access_token` 之后）：
```python
def set_access_cookie(response, token: str) -> None:
    """把 access_token 写成 httpOnly cookie。login 与飞书免登共用，保证标志一致。"""
    from server.app.core.config import get_settings

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=_get_jwt_expire_hours() * 3600,
        secure=get_settings().secure_cookie,
    )
```

- [ ] **Step 4: login 改调 helper**

`server/app/modules/system/auth_router.py`：import 追加 `set_access_cookie`，把 :116-126 的
```python
    token = create_access_token(user.id, user.role)
    max_age = int(os.environ.get("GEO_JWT_EXPIRE_HOURS", "8")) * 3600
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=max_age,
        secure=get_settings().secure_cookie,
    )
```
替换为：
```python
    token = create_access_token(user.id, user.role)
    set_access_cookie(response, token)
```
（`import os` / `get_settings` 若变成无引用，按 ruff 提示清理。）

- [ ] **Step 5: 运行确认通过**

Run: `pytest server/tests/test_set_access_cookie.py -q`
Expected: PASS（2 passed）；再跑既有登录回归 `pytest server/tests/test_auth*.py -q` 保持绿。

- [ ] **Step 6: Commit**

```bash
git add server/app/core/security.py server/app/modules/system/auth_router.py server/tests/test_set_access_cookie.py
git commit -m "refactor(auth): 抽 set_access_cookie 供 login 与飞书免登共用（行为不变）"
```

---

### Task 4: `feishu_api` 通用调用器

**Files:**
- Modify: `server/app/shared/feishu_bitable.py`（新增 `feishu_api`）
- Test: `server/tests/test_feishu_api_helper.py`

**Interfaces:**
- Consumes: `get_tenant_access_token`、`_http_json`、`_TOKEN_INVALID_CODES`、`_FEISHU_BASE`（同模块）。
- Produces: `feishu_api(method: str, path: str, *, body: dict | None = None) -> dict`（注入 Bearer，token 失效重试一次；path 以 `/` 开头，拼 `_FEISHU_BASE`）。

- [ ] **Step 1: 写失败测试**

`server/tests/test_feishu_api_helper.py`：
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest server/tests/test_feishu_api_helper.py -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'feishu_api'`）

- [ ] **Step 3: 实现 feishu_api**

`server/app/shared/feishu_bitable.py` 末尾追加：
```python
def feishu_api(method: str, path: str, *, body: dict | None = None) -> dict:
    """通用飞书 OpenAPI 调用：注入 Bearer tenant_access_token，token 失效自动刷新重试一次。

    path 以 '/' 开头（如 '/im/v1/messages'），拼到 _FEISHU_BASE。返回解析后的 JSON dict。
    """
    token = get_tenant_access_token()
    url = f"{_FEISHU_BASE}{path}"
    resp = _http_json(method, url, headers={"Authorization": f"Bearer {token}"}, body=body)
    if resp.get("code") in _TOKEN_INVALID_CODES:
        token = get_tenant_access_token(force=True)
        resp = _http_json(method, url, headers={"Authorization": f"Bearer {token}"}, body=body)
    return resp
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest server/tests/test_feishu_api_helper.py -q`
Expected: PASS（1 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/shared/feishu_bitable.py server/tests/test_feishu_api_helper.py
git commit -m "feat(feishu): feishu_api 通用调用器（Bearer 注入 + token 失效重试）"
```

---

### Task 5: `build_review_card` 纯函数

**Files:**
- Create: `server/app/shared/feishu_card.py`
- Test: `server/tests/test_feishu_card.py`

**Interfaces:**
- Produces: `build_review_card(*, article_id: int, title: str, question: str, score: int | None, decision: str | None, review_url: str) -> dict`（卡片 2.0 JSON；无 I/O）。本期不含「审核通过」按钮。

- [ ] **Step 1: 写失败测试**

`server/tests/test_feishu_card.py`：
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest server/tests/test_feishu_card.py -q`
Expected: FAIL（`ModuleNotFoundError: server.app.shared.feishu_card`）

- [ ] **Step 3: 实现 feishu_card.py（builder）**

`server/app/shared/feishu_card.py`：
```python
"""飞书审核卡片（交互卡 2.0）构造 + 发送。

与 webhook-only 的 feishu.py 分开：这里走自建应用 bot（im/v1/messages），
用于「一篇一卡」的待审通知。build_* 为纯函数可单测；send_* 吞异常返回 None。
"""

from __future__ import annotations

import json
import logging

from server.app.core.config import get_settings

_logger = logging.getLogger(__name__)

# lark_md 需转义的字符
_LARK_MD_SPECIAL = ("\\", "*", "_", "`", "~", "[", "]")


def _esc(text: str, limit: int = 120) -> str:
    s = text or ""
    for ch in _LARK_MD_SPECIAL:
        s = s.replace(ch, "\\" + ch)
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def build_review_card(
    *,
    article_id: int,
    title: str,
    question: str,
    score: int | None,
    decision: str | None,
    review_url: str,
) -> dict:
    """构造待审交互卡（卡片 2.0）。纯函数、无 I/O。本期不含「审核通过」按钮。"""
    score_line = "自评：—"
    if decision is not None or score is not None:
        score_line = f"自评：{decision or '—'}" + (f" · {score} 分" if score is not None else "")
    lines = [f"**{_esc(title, 80)}**", f"选题：{_esc(question, 80)}", score_line]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"📝 文章待审 #{article_id}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看文章"},
                        "type": "primary",
                        "url": review_url,
                    }
                ],
            },
        ],
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest server/tests/test_feishu_card.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/shared/feishu_card.py server/tests/test_feishu_card.py
git commit -m "feat(feishu): build_review_card 纯函数（交互卡 2.0）"
```

---

### Task 6: `send_review_card`（发卡，吞异常）

**Files:**
- Modify: `server/app/shared/feishu_card.py`
- Test: `server/tests/test_feishu_card.py`（追加）

**Interfaces:**
- Consumes: `feishu_bitable.feishu_api`、`build_review_card`、`settings.feishu_review_card_enabled` / `feishu_review_chat_id`。
- Produces: `send_review_card(*, chat_id, article_id, title, question, score, decision, review_url) -> str | None`（返回 message_id；未开启/无 chat_id/失败 → None）。

- [ ] **Step 1: 写失败测试（追加到 test_feishu_card.py）**

```python
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
        chat_id="oc_abc", article_id=1, title="t", question="q",
        score=80, decision="approved", review_url="https://x/article/1",
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
    assert fc.send_review_card(
        chat_id="oc_abc", article_id=1, title="t", question="q",
        score=None, decision=None, review_url="https://x/article/1",
    ) is None
    config.get_settings.cache_clear()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest server/tests/test_feishu_card.py -q`
Expected: FAIL（`AttributeError: ... has no attribute 'send_review_card'`）

- [ ] **Step 3: 实现 send_review_card**

`server/app/shared/feishu_card.py` 顶部 import 追加 `from server.app.shared.feishu_bitable import feishu_api`，末尾追加：
```python
def send_review_card(
    *,
    chat_id: str,
    article_id: int,
    title: str,
    question: str,
    score: int | None,
    decision: str | None,
    review_url: str,
) -> str | None:
    """发一张待审交互卡到群。未开启 / 无 chat_id / 失败 → None（绝不让发卡拖垮 loop）。"""
    settings = get_settings()
    if not settings.feishu_review_card_enabled or not chat_id:
        return None
    card = build_review_card(
        article_id=article_id, title=title, question=question,
        score=score, decision=decision, review_url=review_url,
    )
    try:
        resp = feishu_api(
            "POST",
            "/im/v1/messages?receive_id_type=chat_id",
            body={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
        )
        if resp.get("code") != 0:
            _logger.warning("send_review_card failed: %s", resp)
            return None
        return (resp.get("data") or {}).get("message_id")
    except Exception:
        _logger.warning("send_review_card raised", exc_info=True)
        return None
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest server/tests/test_feishu_card.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/shared/feishu_card.py server/tests/test_feishu_card.py
git commit -m "feat(feishu): send_review_card 经自建应用 bot 发交互卡（吞异常）"
```

---

### Task 7: 发卡端点 `POST /api/articles/{id}/review-card`

**Files:**
- Modify: `server/app/modules/articles/router.py`（`articles_mcp_router` 追加端点）
- Test: `server/tests/test_review_card_endpoint.py`

**Interfaces:**
- Consumes: `send_review_card`、`require_mcp_token`、`settings.feishu_public_base_url` / `feishu_review_chat_id`。
- Produces: `POST /api/articles/{article_id}/review-card`，body `{title, question?, score?, decision?}`，resp `{sent: bool, message_id: str | None}`。review_url = `{base}/article/{id}`。

- [ ] **Step 1: 写失败测试**

`server/tests/test_review_card_endpoint.py`：
```python
import pytest

from server.tests.utils import build_test_app


def _make_article(test_app) -> int:
    from server.app.modules.articles.models import Article

    with test_app.session_factory() as db:
        a = Article(
            user_id=test_app.admin_id, title="t", author="", content_json={"type": "doc", "content": []},
            content_html="", plain_text="", review_status="pending",
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        return a.id


@pytest.mark.mysql
def test_review_card_requires_mcp_token(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        r = test_app.client.post("/api/articles/1/review-card", json={"title": "t"})
        assert r.status_code == 401
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_review_card_builds_review_url_and_sends(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        monkeypatch.setenv("GEO_FEISHU_REVIEW_CARD_ENABLED", "true")
        monkeypatch.setenv("GEO_FEISHU_REVIEW_CHAT_ID", "oc_abc")
        monkeypatch.setenv("GEO_PUBLIC_BASE_URL", "https://geo.example.com")
        from server.app.core import config

        config.get_settings.cache_clear()

        captured = {}

        def fake_send(**kwargs):
            captured.update(kwargs)
            return "om_1"

        monkeypatch.setattr("server.app.modules.articles.router.send_review_card", fake_send)

        aid = _make_article(test_app)
        r = test_app.client.post(
            f"/api/articles/{aid}/review-card",
            json={"title": "t", "question": "q", "score": 88, "decision": "approved"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"sent": True, "message_id": "om_1"}
        assert captured["review_url"] == f"https://geo.example.com/article/{aid}"
        assert captured["chat_id"] == "oc_abc"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_review_card_404_when_article_missing(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        r = test_app.client.post(
            "/api/articles/999999/review-card",
            json={"title": "t"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 404
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 运行确认失败**

Run: `GEO_TEST_DATABASE_URL=...geo_test pytest server/tests/test_review_card_endpoint.py -q`
Expected: FAIL（404 端点不存在 → 405/404 路由未注册）

- [ ] **Step 3: 实现端点**

`server/app/modules/articles/router.py`：文件顶部 import 追加
`from server.app.shared.feishu_card import send_review_card`
与 `from server.app.core.config import get_settings`（若未 import）。在 `articles_mcp_router` 段追加：
```python
class ReviewCardPayload(BaseModel):
    title: str
    question: str = ""
    score: int | None = None
    decision: str | None = None


class ReviewCardResponse(BaseModel):
    sent: bool
    message_id: str | None = None


@articles_mcp_router.post(
    "/{article_id}/review-card",
    response_model=ReviewCardResponse,
    dependencies=[Depends(require_mcp_token)],
)
def post_review_card(
    article_id: int,
    payload: ReviewCardPayload,
    db: Session = Depends(get_db),
) -> ReviewCardResponse:
    """[MCP] 给一篇文章发一张飞书待审交互卡。开关关 / 无 chat_id → sent=False。"""
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="article not found")
    settings = get_settings()
    base = (settings.feishu_public_base_url or "").rstrip("/")
    review_url = f"{base}/article/{article_id}"
    try:
        mid = send_review_card(
            chat_id=settings.feishu_review_chat_id or "",
            article_id=article_id,
            title=payload.title,
            question=payload.question,
            score=payload.score,
            decision=payload.decision,
            review_url=review_url,
        )
    except Exception as exc:  # 理论上 send_review_card 已吞异常，这里兜底走 MCP 错误规约
        raise mcp_exception_response(exc, context=f"review_card article_id={article_id}") from exc
    return ReviewCardResponse(sent=mid is not None, message_id=mid)
```

- [ ] **Step 4: 运行确认通过**

Run: `GEO_TEST_DATABASE_URL=...geo_test pytest server/tests/test_review_card_endpoint.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/articles/router.py server/tests/test_review_card_endpoint.py
git commit -m "feat(feishu): POST /api/articles/{id}/review-card 发卡端点（MCP token）"
```

---

### Task 8: MCP 工具 `notify_review_card` + 工具数 21→22

**Files:**
- Modify: `server/mcp/tools/action.py`
- Modify: `server/app/modules/mcp_catalog/connect_router.py:27`（`MCP_TOOLS_COUNT`）
- Test: `server/tests/test_mcp_status_count.py`

**Interfaces:**
- Consumes: `_apost`（同模块）。
- Produces: MCP tool `notify_review_card(article_id, title, question="", score=None, decision=None)`。

- [ ] **Step 1: 写失败测试**

`server/tests/test_mcp_status_count.py`：
```python
def test_mcp_tools_count_is_22():
    from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT

    assert MCP_TOOLS_COUNT == 22


def test_notify_review_card_tool_registered():
    import server.mcp.tools.action  # noqa: F401  触发注册
    from server.mcp.server import mcp

    # FastMCP 暴露已注册 tool 名（属性名随版本，二者其一）
    names = getattr(mcp, "_tools", None) or getattr(mcp, "tools", None) or {}
    assert any("notify_review_card" in str(n) for n in (names.keys() if hasattr(names, "keys") else names))
```
> 若 `mcp` 无法直接列出 tool 名（FastMCP 版本差异），把第二个测试降级为 `from server.mcp.tools.action import notify_review_card` 断言可 import 即可。

- [ ] **Step 2: 运行确认失败**

Run: `pytest server/tests/test_mcp_status_count.py::test_mcp_tools_count_is_22 -q`
Expected: FAIL（当前 21）

- [ ] **Step 3: 加 tool + 改计数**

`server/mcp/tools/action.py` 末尾追加：
```python
@mcp.tool()
async def notify_review_card(
    article_id: int,
    title: str,
    question: str = "",
    score: int | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    """Send one interactive review card to the Feishu group for a freshly written article.

    Shows title / ID / self-score / question + a 「查看文章」 link to /article/{id}.
    No-op (sent=false) if GEO_FEISHU_REVIEW_CARD_ENABLED is off or no chat_id configured.

    Args:
        article_id: Target article (must exist).
        title: Article title shown on the card.
        question: 选题 / source question shown on the card.
        score: self-review score 0-100 (optional).
        decision: "approved" / "needs_rewrite" / "rejected" (optional).
    """
    body: dict[str, Any] = {"title": title, "question": question}
    if score is not None:
        body["score"] = score
    if decision is not None:
        body["decision"] = decision
    return await _apost(f"/api/articles/{article_id}/review-card", json=body)
```
`server/app/modules/mcp_catalog/connect_router.py:27`：`MCP_TOOLS_COUNT = 22`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest server/tests/test_mcp_status_count.py -q` + `pytest server/tests/ -q -k "mcp and async"`（守卫 async tool 无死锁隐患）
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/mcp/tools/action.py server/app/modules/mcp_catalog/connect_router.py server/tests/test_mcp_status_count.py
git commit -m "feat(mcp): notify_review_card 工具 + MCP_TOOLS_COUNT 21→22"
```

---

### Task 9: `feishu` 模块 service（resolve / find / bind）

**Files:**
- Create: `server/app/modules/feishu/__init__.py`
- Create: `server/app/modules/feishu/service.py`
- Test: `server/tests/test_feishu_h5_service.py`

**Interfaces:**
- Consumes: `feishu_bitable._http_json` / `_FEISHU_BASE`、`settings.feishu_app_id/secret`、`User`。
- Produces:
  - `resolve_open_id(code: str) -> str`（换 user_access_token → 拉 open_id）
  - `find_user_by_open_id(db, open_id: str) -> User | None`
  - `bind_open_id(db, user: User, open_id: str) -> None`（幂等：user.feishu_open_id 空才写；冲突抛 `ConflictError`）

- [ ] **Step 1: 写失败测试**

`server/tests/test_feishu_h5_service.py`：
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `GEO_TEST_DATABASE_URL=...geo_test pytest server/tests/test_feishu_h5_service.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 service**

`server/app/modules/feishu/__init__.py`：空文件。
`server/app/modules/feishu/service.py`：
```python
"""飞书 H5 免登 service：换 open_id、按 open_id 查用户、首登自绑。

authen v2 版本以官方最新为准（v1 authen/v1/access_token 仍可用）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.modules.system.models import User
from server.app.shared.errors import ClientError, ConflictError
from server.app.shared.feishu_bitable import _FEISHU_BASE, _http_json


def resolve_open_id(code: str) -> str:
    """临时授权码 code → user_access_token → open_id。失败抛 ClientError。"""
    settings = get_settings()
    app_id = settings.feishu_app_id
    app_secret = settings.feishu_app_secret
    if not app_id or not app_secret:
        raise ClientError("未配置 GEO_FEISHU_APP_ID / GEO_FEISHU_APP_SECRET")

    token_resp = _http_json(
        "POST",
        f"{_FEISHU_BASE}/authen/v2/oauth/token",
        headers={"Content-Type": "application/json; charset=utf-8"},
        body={
            "grant_type": "authorization_code",
            "client_id": app_id,
            "client_secret": app_secret,
            "code": code,
        },
    )
    access_token = token_resp.get("access_token")
    if not access_token:
        raise ClientError(f"换取 user_access_token 失败: {token_resp.get('error') or token_resp}")

    info = _http_json(
        "GET",
        f"{_FEISHU_BASE}/authen/v1/user_info",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    open_id = (info.get("data") or {}).get("open_id")
    if not open_id:
        raise ClientError(f"拉取 open_id 失败: {info}")
    return str(open_id)


def find_user_by_open_id(db: Session, open_id: str) -> User | None:
    return db.query(User).filter(User.feishu_open_id == open_id).first()


def bind_open_id(db: Session, user: User, open_id: str) -> None:
    """首登自绑：user.feishu_open_id 为空才写（幂等）；open_id 已属他人 → ConflictError。"""
    if user.feishu_open_id:
        return  # 幂等，已绑不动
    existing = find_user_by_open_id(db, open_id)
    if existing is not None and existing.id != user.id:
        raise ConflictError("该飞书身份已绑定到其它 GEO 账号")
    user.feishu_open_id = open_id
    db.flush()
```

- [ ] **Step 4: 运行确认通过**

Run: `GEO_TEST_DATABASE_URL=...geo_test pytest server/tests/test_feishu_h5_service.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/feishu/__init__.py server/app/modules/feishu/service.py server/tests/test_feishu_h5_service.py
git commit -m "feat(feishu): H5 免登 service（resolve_open_id / find / bind 首登自绑）"
```

---

### Task 10: `feishu` 模块 router（h5-login / h5-bind）+ main 挂载

**Files:**
- Create: `server/app/modules/feishu/router.py`
- Modify: `server/app/main.py`（import + include_router，公开挂载）
- Test: `server/tests/test_feishu_h5_api.py`

**Interfaces:**
- Consumes: `service.resolve_open_id/find_user_by_open_id/bind_open_id`、`create_access_token`、`set_access_cookie`、`get_current_user`、`settings.feishu_h5_enabled`。
- Produces:
  - `POST /api/feishu/h5-login`（公开）body `{code}` → `{authenticated: bool, reason?: str}`（命中则同时种 cookie）
  - `POST /api/feishu/h5-bind`（需登录）body `{code}` → `{bound: bool}`

- [ ] **Step 1: 写失败测试**

`server/tests/test_feishu_h5_api.py`：
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `GEO_TEST_DATABASE_URL=...geo_test pytest server/tests/test_feishu_h5_api.py -q`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 实现 router**

`server/app/modules/feishu/router.py`：
```python
"""飞书 H5 免登路由：h5-login（公开）+ h5-bind（需登录）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.core.mcp_errors import mcp_exception_response
from server.app.core.security import create_access_token, get_current_user, set_access_cookie
from server.app.db.session import get_db
from server.app.modules.feishu.service import bind_open_id, find_user_by_open_id, resolve_open_id
from server.app.modules.system.models import User
from server.app.shared.errors import ConflictError

# 公开路由（无鉴权 dep）：进来时用户尚未登录
h5_public_router = APIRouter()
# 需登录路由
h5_auth_router = APIRouter()


class CodePayload(BaseModel):
    code: str


class H5LoginResponse(BaseModel):
    authenticated: bool
    reason: str | None = None


class H5BindResponse(BaseModel):
    bound: bool
    reason: str | None = None


@h5_public_router.post("/h5-login", response_model=H5LoginResponse)
def h5_login(payload: CodePayload, response: Response, db: Session = Depends(get_db)) -> H5LoginResponse:
    settings = get_settings()
    if not settings.feishu_h5_enabled:
        return H5LoginResponse(authenticated=False, reason="disabled")
    try:
        open_id = resolve_open_id(payload.code)
    except Exception as exc:
        raise mcp_exception_response(exc, context="h5_login") from exc
    user = find_user_by_open_id(db, open_id)
    if user is None:
        return H5LoginResponse(authenticated=False, reason="unbound")
    set_access_cookie(response, create_access_token(user.id, user.role))
    return H5LoginResponse(authenticated=True)


@h5_auth_router.post("/h5-bind", response_model=H5BindResponse)
def h5_bind(
    payload: CodePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> H5BindResponse:
    settings = get_settings()
    if not settings.feishu_h5_enabled:
        return H5BindResponse(bound=False, reason="disabled")
    if current_user.feishu_open_id:
        return H5BindResponse(bound=True)  # 幂等
    try:
        open_id = resolve_open_id(payload.code)
        user = db.get(User, current_user.id)
        bind_open_id(db, user, open_id)
        db.commit()
    except ConflictError:
        db.rollback()
        return H5BindResponse(bound=False, reason="conflict")
    except Exception as exc:
        db.rollback()
        raise mcp_exception_response(exc, context="h5_bind") from exc
    return H5BindResponse(bound=True)
```
> 注：h5-bind 里 `current_user` 是缓存脱钩对象，写库前用 `db.get(User, current_user.id)` 取会话内实例再改。

- [ ] **Step 4: main.py 挂载**

`server/app/main.py`：仿 `stock_files_router`（:360）公开挂法。import 段加
`from server.app.modules.feishu.router import h5_public_router, h5_auth_router`，在 stock_files_router 之后加：
```python
    app.include_router(h5_public_router, prefix="/api/feishu", tags=["feishu"])
    app.include_router(
        h5_auth_router,
        prefix="/api/feishu",
        tags=["feishu"],
        dependencies=[Depends(get_current_user)],
    )
```

- [ ] **Step 5: 运行确认通过**

Run: `GEO_TEST_DATABASE_URL=...geo_test pytest server/tests/test_feishu_h5_api.py -q`
Expected: PASS（5 passed）

- [ ] **Step 6: 全量后端门禁**

Run: `ruff check server/ && ruff format --check server/ && mypy server/app`
Expected: 全绿（有问题就地修）

- [ ] **Step 7: Commit**

```bash
git add server/app/modules/feishu/router.py server/app/main.py server/tests/test_feishu_h5_api.py
git commit -m "feat(feishu): h5-login/h5-bind 路由 + main 挂载（免登+首登自绑）"
```

---

### Task 11: 前端 H5 引导（飞书端内免登，复用 /article/:id）

**Files:**
- Create: `web/src/api/feishu.ts`（h5-login / h5-bind 客户端）
- Create: `web/src/features/content/useFeishuH5Bootstrap.ts`（引导 hook）
- Modify: `web/src/routes.tsx`（`ContentRoute` 或 `/article` 外层挂载引导 hook）
- Test: 无单测框架，门禁 = `pnpm --filter @geo/web typecheck` + `build`

**Interfaces:**
- Consumes: `POST /api/feishu/h5-login`、`POST /api/feishu/h5-bind`、`GET /api/auth/me`（现有）。
- Produces: 飞书端内首登→自绑→二次免登的前端时序。

> **实现期须核对官方 SDK**：飞书 H5 JSSDK 取临时授权码的确切 API（`window.h5sdk.ready` / `tt.requestAccess` / 是否需要 `/api/feishu/jssdk-config` 签名 ticket）以官方文档为准。下面给结构与调用点，SDK 细节实现时确认。

- [ ] **Step 1: API 客户端**

`web/src/api/feishu.ts`：
```ts
import { api } from "./client"; // 若现有 client 导出方式不同，按仓库约定引用

export async function h5Login(code: string): Promise<{ authenticated: boolean; reason?: string }> {
  return api.post("/api/feishu/h5-login", { code });
}

export async function h5Bind(code: string): Promise<{ bound: boolean; reason?: string }> {
  return api.post("/api/feishu/h5-bind", { code });
}
```
> 按 `web/src/api/` 现有客户端封装风格（fetch/axios 包装）对齐，不要新造一套。

- [ ] **Step 2: 引导 hook**

`web/src/features/content/useFeishuH5Bootstrap.ts`：
```ts
import { useEffect, useRef } from "react";
import { h5Bind, h5Login } from "../../api/feishu";
import { getMe } from "../../api/auth"; // 现有 me 接口，按实际命名引用

// 仅在飞书端内（window.h5sdk 存在）启用；取 code 的 SDK 细节以官方文档为准。
async function getFeishuAuthCode(): Promise<string | null> {
  const w = window as any;
  if (!w.h5sdk || !w.tt) return null;
  return new Promise((resolve) => {
    w.h5sdk.ready(() => {
      w.tt.requestAccess({
        // appId / scopeList 等参数以官方 requestAccess 文档为准
        success: (res: any) => resolve(res.code ?? null),
        fail: () => resolve(null),
      });
    });
  });
}

export function useFeishuH5Bootstrap(): void {
  const ranRef = useRef(false);
  useEffect(() => {
    if (ranRef.current) return;
    if (!(window as any).h5sdk) return; // 非飞书端内：不介入，行为不变
    ranRef.current = true;
    void (async () => {
      let me = await getMe().catch(() => null);
      if (!me) {
        const code = await getFeishuAuthCode();
        if (code) {
          const r = await h5Login(code).catch(() => null);
          if (r?.authenticated) {
            me = await getMe().catch(() => null);
          }
          // r.reason === "unbound" → 交给现有未登录重定向走手动登录
        }
      }
      if (me && !me.feishu_open_id) {
        const code = await getFeishuAuthCode();
        if (code) await h5Bind(code).catch(() => null);
      }
    })();
  }, []);
}
```
> `me` 类型需含 `feishu_open_id?`（`auth_router._user_dict` 已下发该字段）。若前端 `Me` 类型没有，补上可选字段。

- [ ] **Step 3: 在 /article 路由启用**

`web/src/routes.tsx` 的 `ContentRoute`（承接 `/article/:articleId`）内调用 `useFeishuH5Bootstrap()`，或在其父层薄封装组件里调用。只加一行 hook 调用，不改现有渲染。

- [ ] **Step 4: index.html 引入飞书 JSSDK（若走 CDN）**

`web/index.html` `<head>` 加飞书 H5 JSSDK script（版本 / URL 以官方为准）。仅飞书端内会激活 `window.h5sdk`；普通浏览器无副作用。

- [ ] **Step 5: 门禁**

Run: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: 通过（无 TS 报错、build 成功）
> 注意 worktree/cwd 漂移坑：用 `pnpm --filter @geo/web ...` 或 `pnpm -C web ...`，跑完 `pwd` 确认在 e:/geo。

- [ ] **Step 6: Commit**

```bash
git add web/src/api/feishu.ts web/src/features/content/useFeishuH5Bootstrap.ts web/src/routes.tsx web/index.html
git commit -m "feat(web): 飞书端内 H5 免登引导（首登自绑，复用 /article/:id）"
```

---

### Task 12: Loop 模板发卡点 + bundle 版本

**Files:**
- Modify: `server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md`
- Modify: `claude-loops/generation-loop.md`
- Modify: `server/app/modules/loop_skills/version.py`（bump 版本 + 记新 sha）
- Test: `server/tests/test_loop_skill_bundle.py`（既有 sha 校验）

**Interfaces:**
- Consumes: MCP tool `notify_review_card`（Task 8）。

- [ ] **Step 1: 在两处 loop 模板加发卡点**

在每轮自评（`submit_review_decision` / verifier 决策）之后、收尾汇总之前，加一句逐篇发卡说明 + 调用示例：
```
每篇自评产出 decision 后（仅 approved / needs_rewrite 发卡、跳过 rejected）：
notify_review_card(article_id=<id>, title=<title>, question=<question>,
                   score=<score_total>, decision=<decision>)
```
两处「可用工具」清单加入 `notify_review_card`。收尾批量汇总文本消息保留不动。

- [ ] **Step 2: bump version.py**

`server/app/modules/loop_skills/version.py`：把版本号 bump（如 `2026-07-07-v1`）。**先不填 sha**，跑构建取真值。

- [ ] **Step 3: 取新 bundle sha（本地先算，CI 定真值）**

Run（本地）：
```bash
python -c "from server.app.modules.loop_skills.service import build_bundle; print(build_bundle().bundle_sha256)"
```
把输出 sha 加进 `version.py` 的 `KNOWN_BUNDLE_SHAS`。
> 跨 OS 坑（`gotcha-loop-bundle-sha-cross-os`）：打包按 posix 串排序 + LF；本机（Windows）算出的 sha 可能与 CI（Linux）不同。**两个都可能要补**——推 PR 后看 CI `backend-test` 里 `test_bundle_sha_is_known` 报的真值，把 CI 真值也加进 `KNOWN_BUNDLE_SHAS`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest server/tests/test_loop_skill_bundle.py -q`
Expected: 本地 PASS（CI 上若 sha 不同，按 Step 3 补 CI 真值再绿）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md claude-loops/generation-loop.md server/app/modules/loop_skills/version.py
git commit -m "feat(loop): 每篇自评后逐篇发飞书审核卡 + bundle 版本 bump"
```

---

### Task 13（可选 / 后续）：卡片嵌封面图

> **本期默认不做，text-only 卡片先上线。** 原因：飞书交互卡的 `img` 元素需要 `img_key`（先 multipart 上传图片到 `im/v1/images` 换取），不能直接塞外链 URL——需要额外的 multipart 上传管道（`_http_json` 只做 JSON）。核心「链接直达全文」不依赖封面，故拆为可选后续任务。做时：
> - `feishu_card.py` 加 `upload_image(local_path) -> img_key`（multipart POST `/im/v1/images`，image_type=message）。
> - `build_review_card` 加可选 `cover_img_key`，非空则在 elements 顶部插 `{"tag":"img","img_key":...}`。
> - 发卡端点从 `article.cover_asset_id` 取封面 Asset 本地路径 → upload_image → 传入。
> - 测试：monkeypatch upload_image，断言卡片含 img 元素。

---

## Self-Review

**1. Spec coverage（对照 spec 各节）：**
- §4.1 config 4 项 → Task 2 ✅
- §4.2 feishu_api → Task 4 ✅
- §4.3 build/send_review_card → Task 5 + 6 ✅
- §4.4 发卡端点 → Task 7 ✅
- §4.5 MCP 工具 + 计数 → Task 8 ✅
- §4.6 loop 模板 + version → Task 12 ✅
- §5 H5 展示（复用永久链接）→ 无新代码，Task 11 前端引导承接 ✅
- §6.1 迁移唯一索引 → Task 1 ✅
- §6.2 service resolve/find/bind → Task 9 ✅
- §6.3 router + main 挂载 → Task 10 ✅
- §6.4 set_access_cookie 办法 A → Task 3 ✅
- §6.5 前端引导 → Task 11 ✅
- §9 C 档预留 → 明确不做，`build_review_card` 无按钮（扩展点在 Task 13 注释与 spec §9）✅
- **封面**：spec §12 验证提到封面 → Task 13 拆为可选后续（img_key 上传管道），核心 text-only 先上，已在计划中显式说明并需向用户交代。

**2. Placeholder scan：** 无 TBD/TODO；前端 Task 11 的 SDK 细节标注为"实现期核对官方文档"，属外部 SDK 合理验证点，非占位符（给了结构与调用点）。

**3. Type consistency：** `send_review_card` / `build_review_card` 参数名跨 Task 5/6/7 一致；`resolve_open_id`/`find_user_by_open_id`/`bind_open_id` 跨 Task 9/10 一致；`set_access_cookie(response, token)` 跨 Task 3/10 一致；`MCP_TOOLS_COUNT=22` 跨 Task 8。

## 落地顺序建议

Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12（Task 13 可选后续）。每个 Task 独立可测、独立 commit。后端 Task（1-10、12）先行，前端 Task 11 依赖 Task 10 的端点。
