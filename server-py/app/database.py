# server-py/app/database.py
# SQLAlchemy 数据库连接与会话管理（MySQL）
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import config

engine = create_engine(
    config.db.url,
    pool_pre_ping=True,   # 取连接前先探测，避免 MySQL 8 空闲超时后拿到失效连接
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """FastAPI 依赖：请求级数据库会话，请求结束自动关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
