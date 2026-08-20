# server-py/app/services/auth.py
# 密码哈希 + 双 token 签发/校验 + 当前用户依赖
#   - access token：短命 JWT（默认 8h），随请求携带，无状态
#   - refresh token：长命随机串（默认 7d），落库仅存哈希，支持吊销 / 轮换
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..config import config
from ..database import get_db
from ..models import RefreshToken, User

bearer_scheme = HTTPBearer(auto_error=False)

# HttpOnly Cookie 名称（与前端约定一致）
ACCESS_COOKIE_NAME = "workmind_token"
REFRESH_COOKIE_NAME = "workmind_refresh"


def _cookie_kwargs(max_age: int) -> dict:
    """HttpOnly Cookie 公共参数：JS 不可读（防 XSS）、Lax（防 CSRF）、生产强制 HTTPS"""
    return dict(
        httponly=True,
        samesite="lax",
        secure=config.app.env == "production",
        max_age=max_age,
        path="/",
    )


def set_auth_cookie(response: Response, access: str, refresh: str | None = None) -> None:
    """登录/刷新成功后写入双 token HttpOnly Cookie"""
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access,
        **_cookie_kwargs(config.jwt.access_expires_hours * 3600),
    )
    if refresh:
        response.set_cookie(
            key=REFRESH_COOKIE_NAME,
            value=refresh,
            **_cookie_kwargs(config.jwt.refresh_expires_days * 86400),
        )


def clear_auth_cookie(response: Response) -> None:
    """退出登录时清除双 token Cookie"""
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")


def _decode_token(token: str) -> str:
    """解析 access JWT，返回 user_id；无效则抛 401"""
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


# ── access token：短命 JWT ─────────────────────────────────────
def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(hours=config.jwt.access_expires_hours),
    }
    return jwt.encode(payload, config.jwt.secret, algorithm=config.jwt.algorithm)


# ── refresh token：随机串 + 落库哈希（可吊销 / 轮换） ──────────
def _hash_refresh(raw: str) -> str:
    """refresh token 只存 SHA256 哈希：数据库泄露也无法直接伪造"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_refresh_token(db: Session, user_id: str) -> str:
    """生成 refresh token 并落库（仅存哈希）。返回原始随机串——只此一次可见"""
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_hash_refresh(raw),
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=config.jwt.refresh_expires_days),
        )
    )
    db.commit()
    return raw


def _revoke_refresh_token(db: Session, raw: str) -> None:
    """按明文吊销 refresh（转哈希匹配后标记 revoked）"""
    token = db.query(RefreshToken).filter(
        RefreshToken.token_hash == _hash_refresh(raw),
        RefreshToken.revoked == 0,
    ).first()
    if token:
        token.revoked = 1
        db.commit()


def issue_tokens(db: Session, user: User) -> tuple[str, str]:
    """签发一对 token：返回 (access, refresh_raw)"""
    return create_access_token(user.id), _new_refresh_token(db, user.id)


def rotate_refresh_token(db: Session, raw: str, user_id: str) -> str:
    """refresh 轮换：吊销旧 token 后签发新 token（防重放：旧 token 立即失效）"""
    _revoke_refresh_token(db, raw)
    return _new_refresh_token(db, user_id)


def revoke_refresh_token(db: Session, raw: str | None) -> None:
    """登出时吊销 refresh token（raw 可为空）"""
    if raw:
        _revoke_refresh_token(db, raw)


def verify_refresh_token(db: Session, raw: str) -> User:
    """校验 refresh token：存在 + 未吊销 + 未过期，返回对应用户；失败抛 401"""
    token = db.query(RefreshToken).filter(
        RefreshToken.token_hash == _hash_refresh(raw),
        RefreshToken.revoked == 0,
    ).first()
    if token is None or token.expires_at < datetime.utcnow():
        if token:
            token.revoked = 1  # 过期记录顺手标记，避免残留
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get(User, token.user_id)
    if user is None:
        token.revoked = 1
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI 依赖：从 Authorization: Bearer <access> 或 HttpOnly Cookie 解析当前用户，失败抛 401"""
    # 优先 Bearer header（兼容 Swagger / 第三方调用），其次 HttpOnly Cookie
    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(ACCESS_COOKIE_NAME)

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
