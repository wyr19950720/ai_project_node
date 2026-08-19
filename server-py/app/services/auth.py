# server-py/app/services/auth.py
# 密码哈希 + JWT 签发/校验 + 当前用户依赖
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..config import config
from ..database import get_db
from ..models import User

bearer_scheme = HTTPBearer(auto_error=False)

# HttpOnly Cookie 名称（与前端约定一致）
AUTH_COOKIE_NAME = "workmind_token"


def set_auth_cookie(response: Response, token: str) -> None:
    """登录成功后把 token 写入 HttpOnly Cookie，JS 无法读取"""
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,          # JS 不可读，防 XSS 窃取
        samesite="lax",         # 防 CSRF（跨站 POST 不带 Cookie）
        secure=config.app.env == "production",  # 生产环境强制 HTTPS
        max_age=config.jwt.expires_days * 86400,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """退出登录时清除 Cookie"""
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


def _decode_token(token: str) -> str:
    """解析 JWT，返回 user_id；无效则抛 401"""
    try:
        payload = jwt.decode(token, config.jwt.secret, algorithms=[config.jwt.algorithm])
        return payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的登录凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=config.jwt.expires_days),
    }
    return jwt.encode(payload, config.jwt.secret, algorithm=config.jwt.algorithm)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI 依赖：从 Authorization: Bearer <token> 或 HttpOnly Cookie 解析当前用户，失败抛 401"""
    # 优先 Bearer header（兼容 Swagger / 第三方调用），其次 HttpOnly Cookie
    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(AUTH_COOKIE_NAME)

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = _decode_token(token)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
