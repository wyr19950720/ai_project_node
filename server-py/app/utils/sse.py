# server-py/app/utils/sse.py
# SSE 事件格式化 + 流式响应封装
import json

from fastapi.responses import StreamingResponse

from app.utils.errors import sse_error_event
from app.utils.logger import logger

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_event(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_stream(generator_fn):
    """
    包装一个 async generator 函数（产出 (event, data) 元组），
    统一处理异常 → error 事件，返回 StreamingResponse。
    """
    async def wrapped():
        try:
            async for event, data in generator_fn():
                yield sse_event(event, data)
        except Exception as err:
            logger.error("sse stream error", {"error": str(err)})
            yield sse_error_event(err)

    return StreamingResponse(wrapped(), media_type="text/event-stream", headers=SSE_HEADERS)
