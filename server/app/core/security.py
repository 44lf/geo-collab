import os
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from jose import jwt

from server.app.models.user import User

JWT_ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    return os.environ["GEO_JWT_SECRET"]


def _get_jwt_expire_hours() -> int:
    return int(os.environ.get("GEO_JWT_EXPIRE_HOURS", "8"))


def create_access_token(user_id: int, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=_get_jwt_expire_hours())
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


async def get_current_user(request: Request) -> User:
    from sqlalchemy.orm import Session

    from server.app.db.session import SessionLocal

    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    db: Session = SessionLocal()
    try:
        user = db.get(User, int(payload["sub"]))
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account disabled")
        if user.must_change_password:
            raise HTTPException(status_code=403, detail="Password change required")
        return user
    finally:
        db.close()


async def require_local_token(request: Request) -> None:
    token = os.environ.get("GEO_LOCAL_API_TOKEN")
    if not token:
        return

    received = request.headers.get("X-Geo-Token")
    if not received:
        raise HTTPException(status_code=401, detail="Missing X-Geo-Token header")

    if not hmac.compare_digest(token, received):
        raise HTTPException(status_code=401, detail="Invalid token")
