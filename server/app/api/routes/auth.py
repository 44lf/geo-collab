import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from server.app.core.config import get_settings
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.security import create_access_token, verify_token
from server.app.db.session import get_db
from server.app.models.user import User

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "operator"


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not user.check_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

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
    return {
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }


@router.post("/logout")
def logout(response: Response) -> dict:
    response.set_cookie(
        key="access_token",
        value="",
        httponly=True,
        samesite="lax",
        path="/",
        max_age=0,
        secure=get_settings().secure_cookie,
    )
    return {"detail": "Logged out"}


@router.get("/me")
def me(request: Request) -> dict:
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
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "must_change_password": user.must_change_password,
        }
    finally:
        db.close()


@router.post("/change-password")
def change_password(payload: ChangePasswordRequest, request: Request) -> dict:
    from server.app.db.session import SessionLocal

    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    jwt_payload = verify_token(token)
    if not jwt_payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    db: Session = SessionLocal()
    try:
        user = db.get(User, int(jwt_payload["sub"]))
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if not user.check_password(payload.old_password):
            raise HTTPException(status_code=400, detail="Old password is incorrect")
        user.set_password(payload.new_password)
        user.must_change_password = False
        db.commit()
        return {"detail": "Password changed"}
    finally:
        db.close()


@router.post("/users")
def create_user(payload: CreateUserRequest, request: Request) -> dict:
    from server.app.db.session import SessionLocal

    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    jwt_payload = verify_token(token)
    if not jwt_payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if jwt_payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin required")

    db: Session = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == payload.username).first()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        user = User(
            username=payload.username,
            role=payload.role,
            is_active=True,
            must_change_password=True,
        )
        user.set_password(payload.password)
        db.add(user)
        db.commit()
        db.refresh(user)
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
            "must_change_password": user.must_change_password,
        }
    finally:
        db.close()
