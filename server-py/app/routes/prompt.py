# server-py/app/routes/prompt.py
# Prompt 调试路由：单次测试(流式) + A/B对比 + 模板管理
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException

from app.middleware import rate_limiter
from app.services.model import create_chat_model
from app.services.prompt.prompt_service import (
    delete_template, get_template, list_templates, save_template, score_ab_test,
)
from app.utils.logger import logger
from app.utils.sse import sse_stream

router = APIRouter()


@router.post("/test/stream", dependencies=[Depends(rate_limiter)])
async def test_stream(body: dict):
    system_prompt = body.get("systemPrompt") or ""
    user_message = (body.get("userMessage") or "").strip()
    temperature = body.get("temperature", 0.7)
    max_tokens = body.get("maxTokens", 1000)

    if not user_message:
        raise HTTPException(status_code=400, detail={"error": {"message": "测试消息不能为空"}})

    async def generator():
        test_model = create_chat_model(temperature=temperature, streaming=True)

        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": body.get("userMessage")})

        yield "start", {"temperature": temperature, "maxTokens": max_tokens}

        full_reply = ""
        input_tokens = 0
        output_tokens = 0
        start_ms = time.time() * 1000

        async for chunk in test_model.astream(messages, max_tokens=max_tokens):
            if chunk.content:
                full_reply += chunk.content
                yield "token", {"token": chunk.content}
            if getattr(chunk, "usage_metadata", None):
                input_tokens = chunk.usage_metadata.get("input_tokens", 0)
                output_tokens = chunk.usage_metadata.get("output_tokens", 0)

        latency_ms = round(time.time() * 1000 - start_ms)

        yield "done", {
            "latencyMs": latency_ms,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
            "costCNY": ((input_tokens / 1e6 * 0.27) + (output_tokens / 1e6 * 1.10)) * 7.2,
        }

        logger.info("prompt test done", {"latencyMs": latency_ms, "inputTokens": input_tokens, "outputTokens": output_tokens})

    return sse_stream(generator)


@router.post("/ab-test", dependencies=[Depends(rate_limiter)])
async def ab_test(body: dict):
    question = (body.get("question") or "").strip()
    system_prompt_a = body.get("systemPromptA")
    system_prompt_b = body.get("systemPromptB")
    temperature = body.get("temperature", 0)
    max_tokens = body.get("maxTokens", 800)

    if not question:
        raise HTTPException(status_code=400, detail={"error": {"message": "测试问题不能为空"}})

    try:
        test_model = create_chat_model(temperature=temperature)

        async def invoke_with(system_prompt):
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": question})
            return await test_model.ainvoke(messages, max_tokens=max_tokens)

        res_a, res_b = await asyncio.gather(invoke_with(system_prompt_a), invoke_with(system_prompt_b))

        answer_a, answer_b = res_a.content, res_b.content
        evaluation = await score_ab_test(question, answer_a, answer_b)

        return {"answerA": answer_a, "answerB": answer_b, "evaluation": evaluation}
    except Exception as err:
        logger.error("ab test error", {"error": str(err)})
        raise HTTPException(status_code=500, detail={"error": {"message": "测试失败，请重试"}})


# ── CRUD：模板管理 ────────────────────────────────────────────

@router.get("/templates")
async def templates():
    return {"templates": list_templates()}


@router.get("/templates/{template_id}")
async def get_one_template(template_id: str):
    t = get_template(template_id)
    if not t:
        raise HTTPException(status_code=404, detail={"error": {"message": "模板不存在"}})
    return t


@router.post("/templates")
async def create_template(body: dict):
    name = (body.get("name") or "").strip()
    system_prompt = (body.get("systemPrompt") or "").strip()
    if not name or not system_prompt:
        raise HTTPException(status_code=400, detail={"error": {"message": "模板名称和内容不能为空"}})
    template = save_template(name, system_prompt, body.get("description", ""), body.get("tags", []))
    return {"success": True, "template": template}


@router.put("/templates/{template_id}")
async def update_template(template_id: str, body: dict):
    template = save_template(
        body.get("name"), body.get("systemPrompt"), body.get("description", ""), body.get("tags", []),
        existing_id=template_id,
    )
    return {"success": True, "template": template}


@router.delete("/templates/{template_id}")
async def remove_template(template_id: str):
    try:
        delete_template(template_id)
        return {"success": True}
    except ValueError as err:
        raise HTTPException(status_code=400, detail={"error": {"message": str(err)}})
