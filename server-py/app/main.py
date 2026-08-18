# server-py/app/main.py
# 服务端入口：注册中间件、路由、启动服务
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import config, validate_config
from app.middleware import RequestLoggerMiddleware
from app.routes.agent import router as agent_router
from app.routes.chat import router as chat_router
from app.routes.erp import router as erp_router
from app.routes.health import router as health_router
from app.routes.knowledge import router as knowledge_router
from app.routes.monitor import router as monitor_router
from app.routes.prompt import router as prompt_router
from app.routes.workflow import router as workflow_router
from app.utils.errors import AppError, app_error_handler
from app.utils.logger import logger

# 启动前校验配置
validate_config()

app = FastAPI(title="WorkMind Server (FastAPI)", description="基于 LangChain + LangGraph 的大模型应用开发项目实战")


# ── 基础中间件 ─────────────────────────────────────────────────
# helmet 等价：基础安全响应头（CSP 关闭，与 Express 版一致）
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-DNS-Prefetch-Control"] = "off"
        response.headers["X-Download-Options"] = "noopen"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware)  # compression
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.app.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggerMiddleware)


# ── 错误处理 ───────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    message = first.get("msg", "请求参数不合法")
    return JSONResponse(status_code=400, content={"error": {"message": message}})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # detail 已经是路由里构造好的 {"error": {...}} 结构，直接作为响应体返回
    if isinstance(exc.detail, dict):
        content = exc.detail
    elif exc.status_code == 404:
        content = {"error": {"message": "接口不存在"}}
    else:
        content = {"error": {"message": str(exc.detail)}}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(AppError)
async def app_error_exception_handler(request: Request, exc: AppError):
    return await app_error_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return await app_error_handler(request, exc)


# ── 路由注册 ───────────────────────────────────────────────────
app.include_router(health_router, prefix="/health")
app.include_router(chat_router, prefix="/api/chat")
app.include_router(knowledge_router, prefix="/api/knowledge")
app.include_router(agent_router, prefix="/api/agent")
app.include_router(workflow_router, prefix="/api/workflow")
app.include_router(erp_router, prefix="/api/erp")
app.include_router(prompt_router, prefix="/api/prompt")
app.include_router(monitor_router, prefix="/api/monitor")


@app.on_event("startup")
async def on_startup():
    # 启动时自动加载固定知识库目录（server-py/knowledge_files/）
    try:
        from app.services.rag.ingest import ingest_fixed_files

        docs = await ingest_fixed_files()
        if docs:
            # logger.info(...)：给机器看的（带时间、INFO 级别前缀）-日志归档、监控、排障
            # print(...)：给人看的，带 emoji 的开发启动横幅 — 纯终端输出
            logger.info("fixed knowledge loaded", {"count": len(docs)})
            print(f"   📚 已加载固定知识库 {len(docs)} 个文档")
    except Exception as err:
        logger.warn("固定知识库加载失败", {"error": str(err)})

    logger.info("server started", {"port": config.app.port, "env": config.app.env})
    print("\n🚀 WorkMind Server (FastAPI) 已启动")
    print(f"   地址: http://localhost:{config.app.port}")
    print(f"   健康检查: http://localhost:{config.app.port}/health\n")
