# server-py/app/routes/chat.py
# 对话路由：流式对话 + 缓存 + 会话管理 + 画像
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.middleware import rate_limiter, security_check
from app.models import User
from app.services.auth import get_current_user
from app.services.cache import cache
from app.services.chat.memory import (
    add_message, delete_session as delete_session_db,
    ensure_session, extract_and_update_profile, get_history, get_profile,
    get_session_messages, list_sessions, message_count, profile_to_context,
    trim_db_history, trim_history,
)
from app.services.model import chat_model
from app.utils.logger import logger
from app.utils.sse import sse_stream

router = APIRouter()

# 内置角色预设
ROLES = {
    "default": "你是 WorkMind AI，一个智能办公助手，回答简洁专业。",
    "tech": "你是资深技术顾问，精通 Vue3、React、Node.js 等前端技术栈。回答要有代码示例，说明清楚原理。",
    "hr": "你是 HR 助理，熟悉劳动法规、公司政策、绩效管理、招聘流程。回答要有温度，兼顾政策合规和员工关怀。",
    "legal": "你是法务助理，熟悉合同法、知识产权、劳动合同。回答要严谨，必要时建议咨询专业律师。",
}


class ChatStreamRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    sessionId: str | None = "default"
    systemPrompt: str | None = Field(default=None, max_length=2000)
    role: str | None = "default"
    userId: str | None = None  # 已废弃：会话归属以 JWT 登录用户为准

# Depends() 表示调用这个接口前先执行 rate_limiter 限流
@router.post("/stream", summary="流式对话接口", dependencies=[Depends(rate_limiter)])
async def chat_stream(body: ChatStreamRequest, user: User = Depends(get_current_user)):
    security_check(body.message)

    session_id = body.sessionId or "default"
    role = body.role or "default"
    user_id = user.id
    message = body.message

    async def generator():
        try:
            # 确保会话存在且属于当前用户（不存在则自动创建，标题取消息前 20 字）
            ensure_session(session_id, user_id, title=message[:20])

            base_system = ROLES.get(role, ROLES["default"])
            profile = get_profile(user_id)
            profile_ctx = profile_to_context(profile)
            system_prompt = base_system + profile_ctx

            cached = cache.get(system_prompt, message)
            if cached:
                logger.info("cache hit", {"sessionId": session_id, "msg": message[:30]})
                yield "cache_hit", {}
                content = cached["content"]
                for i in range(0, len(content), 3):
                    yield "token", {"token": content[i:i + 3]}
                    await asyncio.sleep(0.006)
                yield "done", {"fromCache": True}
                return

            history = get_history(session_id)
            trimmed = trim_history(history, 2000)

            messages = [SystemMessage(content=system_prompt), *trimmed, HumanMessage(content=message)]

            yield "start", {"sessionId": session_id}

            full_reply = ""
            input_tokens = 0
            output_tokens = 0

            async for chunk in chat_model.astream(messages):
                if chunk.content:
                    full_reply += chunk.content
                    yield "token", {"token": chunk.content}
                if getattr(chunk, "usage_metadata", None):
                    input_tokens = chunk.usage_metadata.get("input_tokens", 0)
                    output_tokens = chunk.usage_metadata.get("output_tokens", 0)

            add_message(session_id, "human", message)
            add_message(session_id, "ai", full_reply)
            # 限制单会话消息数，超出删最旧（等价于原内存版 len>20 裁剪）
            if message_count(session_id) > 20:
                trim_db_history(session_id, keep=20)

            cache.set(system_prompt, message, full_reply, input_tokens + output_tokens)

            asyncio.create_task(_safe_extract_profile(user_id, message, full_reply))

            yield "done", {"fromCache": False, "inputTokens": input_tokens, "outputTokens": output_tokens}
            logger.info("chat done", {
                "sessionId": session_id, "inputTokens": input_tokens,
                "outputTokens": output_tokens, "replyLen": len(full_reply),
            })
        except Exception as err:
            logger.error("chat error", {"error": str(err)})
            raise

    return sse_stream(generator)


async def _safe_extract_profile(user_id: str, message: str, reply: str):
    try:
        await extract_and_update_profile(user_id, message, reply)
    except Exception:
        pass


@router.get("/sessions")
async def sessions(user: User = Depends(get_current_user)):
    return {"sessions": list_sessions(user.id)}


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str, user: User = Depends(get_current_user)
):
    """查看某会话的消息记录（仅本人可看）"""
    return {"messages": get_session_messages(session_id, user.id)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: User = Depends(get_current_user)):
    delete_session_db(session_id, user.id)
    return {"success": True}


@router.get("/profile")
async def profile(user: User = Depends(get_current_user)):
    return get_profile(user.id)


@router.get("/roles")
async def roles():
    return {
        "roles": [
            {"id": "default", "label": "通用助手", "icon": "🤖", "desc": "日常问答、通用任务"},
            {"id": "tech", "label": "技术顾问", "icon": "💻", "desc": "代码、架构、技术方案"},
            {"id": "hr", "label": "HR 助理", "icon": "📋", "desc": "人事政策、绩效、招聘"},
            {"id": "legal", "label": "法务助理", "icon": "⚖️", "desc": "合同、合规、法律问题"},
        ],
    }
