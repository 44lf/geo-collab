"""飞书 H5 免登路由：h5-login（公开）+ h5-bind（需登录）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
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
def h5_login(
    payload: CodePayload, response: Response, db: Session = Depends(get_db)
) -> H5LoginResponse:
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
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        bind_open_id(db, user, open_id)
        db.commit()
    except ConflictError:
        db.rollback()
        return H5BindResponse(bound=False, reason="conflict")
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise mcp_exception_response(exc, context="h5_bind") from exc
    return H5BindResponse(bound=True)
