# server-py/app/routes/auth.py
# 用户注册 / 登录 / 登出 / 刷新 token / 当前用户信息
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, UserProfile
from ..services.auth import (
    REFRESH_COOKIE_NAME,
    clear_auth_cookie,
    create_access_token,
    get_current_user,
    hash_password,
    issue_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
    set_auth_cookie,
    verify_password,
    verify_refresh_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=64)
    nickname: str = Field(default="", max_length=64)


class LoginIn(BaseModel):
    username: str
    password: str


def _user_out(user: User) -> dict:
    return {"id": user.id, "username": user.username, "nickname": user.nickname or user.username}


def _issue_and_cookie(db: Session, user: User, response: Response) -> None:
    """签发 access + refresh 双 token 并写入 HttpOnly Cookie"""
    access, refresh = issue_tokens(db, user)
    set_auth_cookie(response, access, refresh)


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db), response: Response = None):
    username = body.username.strip()
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=username,
        password_hash=hash_password(body.password),
        nickname=body.nickname.strip(),
    )
    db.add(user)
    db.flush()
    db.add(UserProfile(user_id=user.id))  # 初始化用户画像
    db.commit()
    db.refresh(user)
    _issue_and_cookie(db, user, response)
    return {"user": _user_out(user)}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db), response: Response = None):
    user = db.query(User).filter(User.username == body.username.strip()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _issue_and_cookie(db, user, response)
    return {"user": _user_out(user)}


@router.post("/refresh")
def refresh(request: Request, db: Session = Depends(get_db), response: Response = None):
    """用 refresh token 换发新 access，并轮换 refresh（旧 token 立即吊销，防重放）"""
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    user = verify_refresh_token(db, raw)
    new_refresh = rotate_refresh_token(db, raw, user.id)
    set_auth_cookie(response, create_access_token(user.id), new_refresh)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), response: Response = None):
    """退出登录：吊销服务端 refresh token + 清除双 Cookie"""
    revoke_refresh_token(db, request.cookies.get(REFRESH_COOKIE_NAME))
    clear_auth_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"user": _user_out(user)}
