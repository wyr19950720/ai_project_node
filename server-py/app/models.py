# server-py/app/models.py
# 用户 / 会话 / 消息 / 画像 数据模型
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from .database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    username = Column(String(64), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String(64), primary_key=True)  # 前端生成的 session_xxx
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    title = Column(String(128), default="新对话")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="sessions")
    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("chat_sessions.id"), index=True, nullable=False)
    role = Column(String(16), nullable=False)  # human / ai
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("ChatSession", back_populates="messages")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id = Column(String(36), ForeignKey("users.id"), primary_key=True)
    name = Column(String(64), default="")
    dept = Column(String(64), default="")
    tech_level = Column(String(32), default="")
    current_goal = Column(Text, default="")
    primary_stack = Column(Text, default="")  # JSON 字符串
    prefers_short = Column(Integer, default=0)
    prefers_code = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="profile")


class ErpApplication(Base):
    """ERP 申请记录（报销/请假）：持久化审批表单、过程消息与最终结果"""

    __tablename__ = "erp_applications"

    id = Column(String(32), primary_key=True)  # APPxxxxxxxx
    user_id = Column(String(36), index=True)  # 归属用户，按用户隔离记录
    form_type = Column(String(16), nullable=False)  # expense / leave
    form_data = Column(Text, nullable=False)  # JSON
    status = Column(String(16), default="pending")  # pending / approved / rejected
    approvers = Column(Text, default="")  # JSON：审批流程角色列表
    messages = Column(Text, default="")  # JSON：审批对话消息
    result = Column(Text, default="")  # JSON：最终结果
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
