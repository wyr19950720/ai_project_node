# server-py/app/routes/chat.py
# 对话路由：流式对话 + 缓存 + 会话管理 + 画像
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.middleware import rate_limiter, security_check
from app.services.cache import cache
from app.services.chat.memory import (
    clear_history, extract_and_update_profile, get_history, get_profile,
    list_sessions, profile_to_context, trim_history,
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
    userId: str | None = "anonymous"

# Depends() 表示调用这个接口前先执行 rate_limiter 限流
@router.post("/stream", summary="流式对话接口" , dependencies=[Depends(rate_limiter)])
async def chat_stream(body: ChatStreamRequest):
    security_check(body.message)

    session_id = body.sessionId or "default"
    role = body.role or "default"
    user_id = body.userId or "anonymous"
    message = body.message

    async def generator():
        try:
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

            history.append(HumanMessage(content=message))
            history.append(AIMessage(content=full_reply))
            if len(history) > 20:
                del history[:2]

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
async def sessions():
    return {"sessions": list_sessions()}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    clear_history(session_id)
    return {"success": True}


@router.get("/profile/{user_id}")
async def profile(user_id: str):
    return get_profile(user_id)


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
