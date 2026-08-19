# server-py/app/routes/auth.py
# 用户注册 / 登录 / 当前用户信息
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, UserProfile
from ..services.auth import create_token, get_current_user, hash_password, verify_password

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


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
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
    return {"token": create_token(user.id), "user": _user_out(user)}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username.strip()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": create_token(user.id), "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"user": _user_out(user)}
