# server-py/app/routes/knowledge.py
# 知识库路由：文档管理（上传/列表/删除）+ RAG 问答（流式）
import os
import random
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.middleware import rate_limiter
from app.services.rag.ingest import delete_document, get_doc_registry, ingest_document
from app.services.rag.query import rag_query_stream
from app.utils.logger import logger
from app.utils.sse import sse_stream

router = APIRouter()
# "./uploads"是一个相对路径字符串，表示「当前工作目录下的 uploads 文件夹」：，而不是代码文件所在位置。所以：
# 如果你在 server-py/ 目录下启动服务（如 uvicorn app.main:app），上传目录会是 server-py/uploads
# 如果你从项目根目录 ai_project_node/ 启动，则会是 ai_project_node/uploads
UPLOAD_DIR = "./uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _gen_filename(original_name: str) -> str:
    ext = os.path.splitext(original_name)[1]
    rand = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=4))
    return f"{int(time.time() * 1000)}_{rand}{ext}"


@router.post("/documents", summary="上传知识库文档", dependencies=[Depends(rate_limiter)])
async def upload_document(
    file: UploadFile | None = File(default=None),
    content: str | None = Form(default=None),
    title: str | None = Form(default=None),
    category: str | None = Form(default="通用"),
):
    try:
        if file:
            ext = os.path.splitext(file.filename or "")[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail={
                    "error": {"message": f"不支持的文件格式 {ext}，只支持 {', '.join(ALLOWED_EXTENSIONS)}"}})

            data = await file.read()
            if len(data) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail={"error": {"message": "文件超过 10MB 限制"}})

            saved_name = _gen_filename(file.filename or "upload")
            saved_path = os.path.join(UPLOAD_DIR, saved_name)
            with open(saved_path, "wb") as f:
                f.write(data)

            doc_title = title or os.path.splitext(file.filename or "")[0]
            doc_meta = await ingest_document(
                file_path=saved_path, file_name=file.filename or saved_name,
                title=doc_title, category=category or "通用",
            )
        elif content:
            tmp_path = os.path.join(UPLOAD_DIR, f"tmp_{int(time.time() * 1000)}.txt")
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)

            doc_meta = await ingest_document(
                file_path=tmp_path, file_name=(title or "文本内容") + ".txt",
                title=title or "未命名文档", category=category or "通用",
            )
        else:
            raise HTTPException(status_code=400, detail={"error": {"message": "请上传文件或提供文本内容"}})

        return {"success": True, "document": doc_meta}
    except HTTPException:
        raise
    except Exception as err:
        logger.error("knowledge: ingest error", {"error": str(err)})
        raise HTTPException(status_code=500, detail={"error": {"message": str(err) or "文档处理失败"}})


@router.get("/documents")
async def list_documents(category: str | None = None):
    docs = get_doc_registry()
    if category:
        docs = [d for d in docs if d["category"] == category]
    return {"documents": docs}


@router.delete("/documents/{doc_id}")
async def remove_document(doc_id: str):
    try:
        await delete_document(doc_id)
        return {"success": True}
    except ValueError as err:
        raise HTTPException(status_code=404, detail={"error": {"message": str(err)}})


@router.post("/query/stream", dependencies=[Depends(rate_limiter)])
async def query_stream(body: dict):
    question = (body.get("question") or "").strip()
    category = body.get("category")

    if not question:
        raise HTTPException(status_code=400, detail={"error": {"message": "问题不能为空"}})

    async def generator():
        yield "status", {"message": "正在检索相关文档..."}

        sources, stream_answer = await rag_query_stream(question, category)

        yield "sources", {"sources": sources}

        if not sources:
            yield "token", {"token": "知识库中未找到相关内容，请尝试上传相关文档后再提问。"}
            yield "done", {}
            return

        yield "status", {"message": "正在生成回答..."}

        async for token in stream_answer():
            yield "token", {"token": token}

        yield "done", {}
        logger.info("knowledge: query done", {"question": question[:40], "sources": len(sources)})

    return sse_stream(generator)


@router.get("/categories")
async def categories():
    docs = get_doc_registry()
    seen = []
    for d in docs:
        if d["category"] not in seen:
            seen.append(d["category"])
    return {"categories": [{"value": "", "label": "全部文档"}, *[{"value": c, "label": c} for c in seen]]}
