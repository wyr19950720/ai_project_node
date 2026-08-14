# server-py/app/utils/errors.py
# 统一错误处理：错误分类、用户友好提示
import json

from fastapi import Request
from fastapi.responses import JSONResponse

from app.utils.logger import logger


class AppError(Exception):
    def __init__(self, message, code="UNKNOWN", status_code=500, retryable=False, user_message=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.user_message = user_message or "服务暂时不可用，请稍后重试"


def classify_error(err: Exception) -> AppError:
    if isinstance(err, AppError):
        return err

    status = getattr(err, "status_code", None) or getattr(err, "status", None)
    message = str(err)

    if status == 429:
        return AppError(message, code="RATE_LIMIT", status_code=429, retryable=True,
                         user_message="请求太频繁，请稍后重试")
    if status in (401, 403):
        return AppError(message, code="AUTH_ERROR", status_code=500, retryable=False,
                         user_message="服务配置错误，请联系管理员")
    if (status is not None and status >= 500) or "ECONNRESET" in message:
        return AppError(message, code="SERVICE_ERROR", status_code=503, retryable=True,
                         user_message="服务暂时不可用，请稍后重试")
    if "timeout" in message.lower():
        return AppError(message, code="TIMEOUT", status_code=504, retryable=True,
                         user_message="响应超时，请重试")
    return AppError(message, code="UNKNOWN", retryable=False)


async def app_error_handler(request: Request, err: Exception):
    app_err = classify_error(err)
    logger.error("unhandled error", {
        "code": app_err.code, "msg": app_err.message,
        "path": request.url.path, "traceId": getattr(request.state, "trace_id", None),
    })
    return JSONResponse(
        status_code=app_err.status_code,
        content={"error": {"code": app_err.code, "message": app_err.user_message, "retryable": app_err.retryable}},
    )


def sse_error_event(err: Exception) -> str:
    app_err = classify_error(err)
    data = json.dumps({"message": app_err.user_message, "retryable": app_err.retryable}, ensure_ascii=False)
    return f"event: error\ndata: {data}\n\n"
