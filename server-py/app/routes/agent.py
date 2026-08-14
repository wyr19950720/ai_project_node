# server-py/app/routes/agent.py
# Agent 路由：流式执行任务，实时推送每一步状态
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.middleware import rate_limiter, security_check
from app.services.agent.agent import get_tool_list, run_agent
from app.utils.logger import logger
from app.utils.sse import sse_stream

router = APIRouter()


@router.post("/run", dependencies=[Depends(rate_limiter)])
async def run(body: dict):
    task = (body.get("task") or "").strip()
    security_check(task)

    if not task:
        raise HTTPException(status_code=400, detail={"error": {"message": "任务不能为空"}})
    if len(task) > 2000:
        raise HTTPException(status_code=400, detail={"error": {"message": "任务描述过长，请简洁描述"}})

    async def generator():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_event(event_type, data):
            await queue.put((event_type, data))

        yield "start", {"task": task, "timestamp": datetime.now(timezone.utc).isoformat()}

        # run_agent 内部总会以 'done' 或 'error' 事件结束
        run_task = asyncio.create_task(run_agent(task, on_event))

        while True:
            event_type, data = await queue.get()
            yield event_type, data
            if event_type in ("done", "error"):
                await run_task
                break

    return sse_stream(generator)


@router.get("/tools")
async def tools():
    return {"tools": get_tool_list()}


@router.get("/examples")
async def examples():
    return {
        "examples": [
            {"title": "技术调研", "task": "对比 Vue3 和 React 2024年的最新状态，分别查询它们的最新版本和主要特性，生成一份技术选型报告", "icon": "🔍"},
            {"title": "费用计算", "task": "我出差3天，酒店每晚580元，机票往返1200元，餐费每天150元，帮我计算总报销金额，并查询一下公司差旅报销标准", "icon": "💰"},
            {"title": "工期计算", "task": "项目计划从2024年3月1日开始，需要45个工作日完成，帮我计算预计完成日期，并生成一份项目时间轴摘要", "icon": "📅"},
            {"title": "知识查询", "task": "从知识库查询公司的年假政策，计算一下我今年还剩多少年假（假设今年已用6天，总共15天），并发送结果通知给HR", "icon": "📚"},
        ],
    }
