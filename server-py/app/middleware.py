# server-py/app/middleware.py
# 通用中间件：请求日志、限流、输入校验、安全检查
import re
import time
import uuid

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


# ── 请求日志 + traceId ─────────────────────────────────────────
class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
        request.state.trace_id = trace_id

        start = time.time()
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id

        elapsed_ms = round((time.time() - start) * 1000)
        level = "ERROR" if response.status_code >= 500 else "WARN" if response.status_code >= 400 else "INFO"
        print(f"[{level}] {request.method} {request.url.path} {response.status_code} {elapsed_ms}ms [{trace_id[:8]}]")
        return response


# ── 简单令牌桶限流 ─────────────────────────────────────────────
class TokenBucket:
    def __init__(self, capacity=30, refill_rate=10):
        self.capacity = capacity
        self.refill_rate = refill_rate  # 每秒补充
        self.tokens = capacity
        self.last_refill = time.time()

    def consume(self) -> bool:
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


_bucket = TokenBucket()


def rate_limiter():
    if not _bucket.consume():
        raise HTTPException(status_code=429, detail={"error": {"code": "RATE_LIMIT", "message": "请求太频繁，请稍后重试"}})


# ── Prompt 注入检测 ────────────────────────────────────────────
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"forget\s+(all\s+)?previous", re.I),
    re.compile(r"忽略(所有)?之前的指令"),
    re.compile(r"你现在是(?!前端|后端|技术|办公)"),
    re.compile(r"新的?系统提示"),
    re.compile(r"act as (?!a helpful)", re.I),
]


def security_check(message: str):
    msg = message or ""
    if any(p.search(msg) for p in INJECTION_PATTERNS):
        print(f"[SECURITY] Prompt 注入尝试: {msg[:100]}")
        raise HTTPException(status_code=400, detail={"error": {"message": "输入内容不符合使用规范"}})
