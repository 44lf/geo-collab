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
