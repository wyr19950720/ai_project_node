# server-py/app/routes/operations.py
# 用户操作记录：汇总 会话 / 消息 / ERP 申请 三张业务表，按北京时间展示
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

from app.database import SessionLocal
from app.models import ChatMessage, ChatSession, ErpApplication, User
from app.services.auth import get_current_user

router = APIRouter()

# 操作类型 -> 中文标签
TYPE_LABELS = {
    "session_create": "新建会话",
    "chat_human": "用户提问",
    "chat_ai": "AI 回复",
    "erp_expense": "报销申请",
    "erp_leave": "请假申请",
}

# 数据库时间戳为 UTC（datetime.utcnow），统一转北京时间（UTC+8）展示
def _bjt(dt: datetime) -> str:
    if dt is None:
        return ""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def _collect_all(db, days: int) -> list[dict]:
    """从三张表收集全部操作记录，每条含 _dt（原始 UTC datetime，用于过滤/排序）"""
    items: list[dict] = []
    cutoff = datetime.utcnow() - timedelta(days=days) if days and days > 0 else None

    # 1) 会话创建
    for s, uname, nname in (
        db.query(ChatSession, User.username, User.nickname)
        .join(User, User.id == ChatSession.user_id)
        .all()
    ):
        if cutoff and s.created_at and s.created_at < cutoff:
            continue
        items.append({
            "_dt": s.created_at,
            "time": _bjt(s.created_at),
            "type": "session_create",
            "user": uname,
            "nickname": nname or uname,
            "content": (s.title or "新对话")[:200],
            "ref": {"sessionId": s.id},
        })

    # 2) 聊天消息
    for m, title, uname, nname in (
        db.query(ChatMessage, ChatSession.title, User.username, User.nickname)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .join(User, User.id == ChatSession.user_id)
        .all()
    ):
        if cutoff and m.created_at and m.created_at < cutoff:
            continue
        items.append({
            "_dt": m.created_at,
            "time": _bjt(m.created_at),
            "type": "chat_human" if m.role == "human" else "chat_ai",
            "user": uname,
            "nickname": nname or uname,
            "content": (m.content or "")[:200],
            "ref": {"sessionId": m.session_id, "sessionTitle": title},
        })

    # 3) ERP 申请
    for a, uname, nname in (
        db.query(ErpApplication, User.username, User.nickname)
        .outerjoin(User, User.id == ErpApplication.user_id)
        .all()
    ):
        if cutoff and a.created_at and a.created_at < cutoff:
            continue
        try:
            form_data = json.loads(a.form_data) if a.form_data else {}
        except Exception:
            form_data = {}
        summary = (
            form_data.get("reason")
            or form_data.get("project")
            or f"金额 {form_data.get('totalAmount')}"
            or ""
        )
        status_label = {"pending": "审批中", "approved": "已通过", "rejected": "已驳回"}.get(a.status, a.status)
        items.append({
            "_dt": a.created_at,
            "time": _bjt(a.created_at),
            "type": f"erp_{a.form_type}",
            "user": uname or "未知",
            "nickname": nname or uname or "未知",
            "content": f"{summary}（{status_label}）" if summary else status_label,
            "ref": {"appId": a.id, "status": a.status},
        })

    return items


@router.get("/records")
def list_operations(
    type: str = Query("all", description="操作类型，all 为全部"),
    days: int = Query(0, description="近 N 天，0 为全部"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        items = _collect_all(db, days)
    finally:
        db.close()

    # 类型过滤
    if type and type != "all":
        items = [i for i in items if i["type"] == type]

    # 按时间倒序（None 排最后）
    items.sort(key=lambda x: x["_dt"] or datetime.min, reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    # 统计各类型数量
    stats: dict[str, int] = {}
    for i in items:
        stats[i["type"]] = stats.get(i["type"], 0) + 1

    # 去除内部 _dt 字段
    for i in page_items:
        i.pop("_dt", None)

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "stats": stats,
        "typeLabels": TYPE_LABELS,
    }
