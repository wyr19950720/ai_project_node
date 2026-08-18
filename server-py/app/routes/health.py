# server-py/app/routes/health.py
import time

from fastapi import APIRouter

from app.services.cache import cache

router = APIRouter()

_start_time = time.time()


@router.get("/live", summary="服务健康检查",)
async def live():
    return {"status": "ok", "uptime": int(time.time() - _start_time)}


@router.get("/")
async def health():
    return {
        "status": "healthy",
        "uptime": int(time.time() - _start_time),
        "cache": cache.get_stats(),
        "version": "1.0.0",
    }
