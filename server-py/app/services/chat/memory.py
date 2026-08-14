# server-py/app/services/chat/memory.py
# 会话记忆管理：短期记忆（当前对话历史）+ 用户画像（跨会话）
import re

from pydantic import BaseModel, Field

from app.services.model import chat_model


# ── Token 估算（不调 API，本地估算）────────────────────────────
def _est_tokens(text: str = "") -> int:
    cn = len(re.findall(r"[一-鿿]", text or ""))
    return int(cn * 0.6 + (len(text or "") - cn) * 0.25 + 0.999999)


# ── 会话历史管理 ────────────────────────────────────────────────
# 生产环境换 Redis，这里用 dict 演示
_session_store: dict[str, list] = {}


def get_history(session_id: str) -> list:
    if session_id not in _session_store:
        _session_store[session_id] = []
    return _session_store[session_id]


def clear_history(session_id: str):
    _session_store.pop(session_id, None)


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


# ── 用户画像（跨会话记忆）──────────────────────────────────────
_profile_store: dict[str, dict] = {}  # userId → profile dict


def get_profile(user_id: str) -> dict:
    return _profile_store.get(user_id, {})


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

        _profile_store[user_id] = updated
    except Exception:
        # 画像提取失败不影响主流程，静默处理
        pass


# 返回所有会话列表（前端展示用）
def list_sessions() -> list[dict]:
    return [{"id": sid, "messageCount": len(msgs)} for sid, msgs in _session_store.items()]
