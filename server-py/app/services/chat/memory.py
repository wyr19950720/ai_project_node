# server-py/app/services/chat/memory.py
# 会话记忆管理（MySQL 持久化）：短期记忆（当前对话历史）+ 用户画像（跨会话）
import json
import re

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ChatMessage, ChatSession, UserProfile
from app.services.model import chat_model


# ── Token 估算（不调 API，本地估算）────────────────────────────
def _est_tokens(text: str = "") -> int:
    cn = len(re.findall(r"[一-鿿]", text or ""))
    return int(cn * 0.6 + (len(text or "") - cn) * 0.25 + 0.999999)


# ── 会话历史管理（MySQL）────────────────────────────────────────
def _db() -> Session:
    """返回一个新的数据库会话（调用方负责 close）"""
    return SessionLocal()


def ensure_session(session_id: str, user_id: str, title: str = "新对话") -> None:
    """确保会话存在且属于当前用户；存在但属于他人则抛 403"""
    db = _db()
    try:
        sess = db.get(ChatSession, session_id)
        if sess is None:
            db.add(ChatSession(id=session_id, user_id=user_id, title=(title[:120] or "新对话")))
            db.commit()
        elif sess.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
    finally:
        db.close()


def get_history(session_id: str) -> list:
    """返回该会话历史（LangChain message 对象列表，按时间正序）"""
    db = _db()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
            .all()
        )
        return [
            HumanMessage(content=r.content) if r.role == "human" else AIMessage(content=r.content)
            for r in rows
        ]
    finally:
        db.close()


def add_message(session_id: str, role: str, content: str) -> None:
    db = _db()
    try:
        db.add(ChatMessage(session_id=session_id, role=role, content=content))
        db.commit()
    finally:
        db.close()


def message_count(session_id: str) -> int:
    db = _db()
    try:
        return db.query(ChatMessage).filter(ChatMessage.session_id == session_id).count()
    finally:
        db.close()


def trim_db_history(session_id: str, keep: int = 20) -> None:
    """超出保留条数时，删除最旧的超量消息（保持上下文窗口）"""
    db = _db()
    try:
        over = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).count() - keep
        if over > 0:
            old_ids = (
                db.query(ChatMessage.id)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id)
                .limit(over)
                .all()
            )
            db.query(ChatMessage).filter(ChatMessage.id.in_([i[0] for i in old_ids])).delete(
                synchronize_session=False
            )
            db.commit()
    finally:
        db.close()


def clear_history(session_id: str) -> None:
    """清空会话消息（保留会话本身）"""
    db = _db()
    try:
        db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
        db.commit()
    finally:
        db.close()


def delete_session(session_id: str, user_id: str) -> None:
    """删除会话（含消息），校验归属"""
    db = _db()
    try:
        sess = db.get(ChatSession, session_id)
        if sess is None:
            return
        if sess.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权删除该会话")
        db.delete(sess)
        db.commit()
    finally:
        db.close()


def list_sessions(user_id: str) -> list[dict]:
    db = _db()
    try:
        rows = (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user_id)
            .order_by(ChatSession.updated_at.desc())
            .all()
        )
        return [
            {
                "id": s.id,
                "title": s.title,
                "messageCount": db.query(ChatMessage)
                .filter(ChatMessage.session_id == s.id)
                .count(),
            }
            for s in rows
        ]
    finally:
        db.close()


def get_session_messages(session_id: str, user_id: str) -> list[dict]:
    """返回某会话的全部消息（正序），校验会话归属；不存在返回空列表"""
    db = _db()
    try:
        sess = db.get(ChatSession, session_id)
        if sess is None:
            return []
        if sess.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
            .all()
        )
        return [
            {
                "id": f"msg_{r.id}",
                "role": "user" if r.role == "human" else "assistant",
                "content": r.content,
                "time": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


# Token 感知截取：从最新消息往前，塞满为止
def trim_history(history: list, max_tokens: int = 2000) -> list:
    result = []
    total = 0
    for msg in reversed(history):
        t = _est_tokens(getattr(msg, "content", "") or "")
        if total + t > max_tokens:
            break
        result.insert(0, msg)
        total += t
    return result


# ── 用户画像（跨会话记忆，MySQL 持久化）─────────────────────────
def get_profile(user_id: str) -> dict:
    db = _db()
    try:
        p = db.get(UserProfile, user_id)
        if p is None:
            return {}
        stack = []
        if p.primary_stack:
            try:
                stack = json.loads(p.primary_stack)
            except json.JSONDecodeError:
                stack = []
        return {
            "name": p.name or "",
            "dept": p.dept or "",
            "techLevel": p.tech_level or "",
            "primaryStack": stack,
            "currentGoal": p.current_goal or "",
            "prefersShort": bool(p.prefers_short),
            "prefersCode": bool(p.prefers_code),
        }
    finally:
        db.close()


def save_profile(user_id: str, profile: dict) -> None:
    db = _db()
    try:
        p = db.get(UserProfile, user_id)
        if p is None:
            p = UserProfile(user_id=user_id)
            db.add(p)
        p.name = profile.get("name", "") or ""
        p.dept = profile.get("dept", "") or ""
        p.tech_level = profile.get("techLevel", "") or ""
        p.primary_stack = json.dumps(profile.get("primaryStack", []), ensure_ascii=False)
        p.current_goal = profile.get("currentGoal", "") or ""
        p.prefers_short = 1 if profile.get("prefersShort") else 0
        p.prefers_code = 1 if profile.get("prefersCode") else 0
        db.commit()
    finally:
        db.close()


def profile_to_context(profile: dict) -> str:
    if not profile:
        return ""

    parts = []
    if profile.get("name"):
        parts.append(f"用户姓名：{profile['name']}")
    if profile.get("dept"):
        parts.append(f"部门：{profile['dept']}")
    if profile.get("techLevel"):
        parts.append(f"技术水平：{profile['techLevel']}")
    if profile.get("primaryStack"):
        parts.append(f"技术栈：{', '.join(profile['primaryStack'])}")
    if profile.get("currentGoal"):
        parts.append(f"当前目标：{profile['currentGoal']}")
    if profile.get("prefersShort"):
        parts.append("偏好简短回答")
    if profile.get("prefersCode"):
        parts.append("偏好带代码示例的回答")

    if not parts:
        return ""
    bullet = "\n".join(f"- {p}" for p in parts)
    return f"\n\n用户背景：\n{bullet}"


class _ProfileExtraction(BaseModel):
    hasInfo: bool = Field(description="是否提取到新信息")
    name: str | None = Field(default=None)
    dept: str | None = Field(default=None)
    techLevel: str | None = Field(default=None, description="初级/中级/高级/架构师")
    primaryStack: list[str] | None = Field(default=None)
    currentGoal: str | None = Field(default=None)
    prefersShort: bool | None = Field(default=None)
    prefersCode: bool | None = Field(default=None)


# 从对话中异步提取用户信息，更新画像
async def extract_and_update_profile(user_id: str, user_msg: str, ai_reply: str):
    try:
        current = get_profile(user_id)
        extract_model = chat_model.with_structured_output(_ProfileExtraction)

        result: _ProfileExtraction = await extract_model.ainvoke([
            {
                "role": "system",
                "content": (
                    "从对话中提取用户信息，只填写有明确依据的字段。\n"
                    f"当前已知画像：{current}\n"
                    "如果没有新信息，hasInfo 返回 false。"
                ),
            },
            {"role": "user", "content": f"用户说：{user_msg}\nAI回复：{ai_reply[:200]}"},
        ])

        if not result.hasInfo:
            return

        updated = {**current}
        if result.name:
            updated["name"] = result.name
        if result.dept:
            updated["dept"] = result.dept
        if result.techLevel:
            updated["techLevel"] = result.techLevel
        if result.currentGoal:
            updated["currentGoal"] = result.currentGoal
        if result.prefersShort is not None:
            updated["prefersShort"] = result.prefersShort
        if result.prefersCode is not None:
            updated["prefersCode"] = result.prefersCode
        if result.primaryStack:
            updated["primaryStack"] = list(dict.fromkeys([*current.get("primaryStack", []), *result.primaryStack]))

        save_profile(user_id, updated)
    except Exception:
        # 画像提取失败不影响主流程，静默处理
        pass
