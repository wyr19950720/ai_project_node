# server-py/app/services/rag/ingest.py
# 文档入库：上传 → 读取文本 → 分片 → 向量化 → 存入内存向量库
import math
import os
import random
import time
from datetime import datetime, timezone

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.model import embeddings
from app.utils.logger import logger

# 单次入库最多 300 个 chunk，防止 embedding API 调用过多
MAX_CHUNKS = 300


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 轻量内存向量库（余弦相似度） ──────────────────────────────
class MemoryVectorStore:
    def __init__(self):
        self.memory_vectors: list[dict] = []  # [{content, embedding, metadata}]

    async def add_documents(self, documents: list[dict]):
        texts = [d["pageContent"] for d in documents]
        # 分批调用 embedding，每批 20 条，避免并发过多
        batch_size = 20
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_docs = documents[i:i + batch_size]
            vectors = await embeddings.aembed_documents(batch)
            for j in range(len(batch)):
                self.memory_vectors.append({
                    "content": batch[j],
                    "embedding": vectors[j],
                    "metadata": batch_docs[j]["metadata"],
                })
            logger.info("rag: embedding progress", {"done": min(i + batch_size, len(texts)), "total": len(texts)})

    async def similarity_search_with_score(self, query: str, k: int = 4, filter_fn=None):
        query_vec = await embeddings.aembed_query(query)

        pool = self.memory_vectors
        if filter_fn is not None:
            pool = [v for v in pool if filter_fn(v["metadata"])]

        scored = [
            ({"pageContent": v["content"], "metadata": v["metadata"]}, _cosine_sim(query_vec, v["embedding"]))
            for v in pool
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    denom = na * nb
    return 0 if denom == 0 else dot / denom


# ── 向量库单例 ─────────────────────────────────────────────────
_vector_store: MemoryVectorStore | None = None


async def get_vector_store() -> MemoryVectorStore:
    global _vector_store
    if _vector_store:
        return _vector_store

    if not embeddings:
        raise ValueError("未配置 ZHIPU_API_KEY，无法使用 RAG 功能")

    _vector_store = MemoryVectorStore()
    logger.info("rag: memory vector store initialized")
    return _vector_store


# ── 文档元数据注册表 ──────────────────────────────────────────
_doc_registry: dict[str, dict] = {}


def get_doc_registry() -> list[dict]:
    return list(_doc_registry.values())


def get_doc(doc_id: str) -> dict | None:
    return _doc_registry.get(doc_id)


# ── 文本提取：根据文件类型读取内容 ───────────────────────────
def _extract_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".txt", ".md"):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            logger.warn("pdf parse failed", {"error": str(e)})
            raise ValueError(f"PDF 解析失败：{e}")

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# ── 核心：文档入库 ─────────────────────────────────────────────
async def ingest_document(file_path: str, file_name: str, title: str | None = None, category: str = "通用") -> dict:
    doc_id = f"doc_{_now_ms()}_{''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=5))}"

    logger.info("rag: ingesting document", {"docId": doc_id, "title": title, "category": category})

    # 1. 提取文本
    raw_text = _extract_text(file_path)
    if not raw_text.strip():
        raise ValueError("文档内容为空，无法处理")

    # 2. 文档分片
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )

    final_title = title or file_name
    texts = splitter.split_text(raw_text)
    metadata = {"docId": doc_id, "title": final_title, "category": category, "fileName": file_name, "uploadedAt": _now_iso()}
    chunks = [{"pageContent": t, "metadata": dict(metadata)} for t in texts]

    if len(chunks) > MAX_CHUNKS:
        logger.warn("rag: too many chunks, truncating", {"original": len(chunks), "truncated": MAX_CHUNKS})
        chunks = chunks[:MAX_CHUNKS]

    logger.info("rag: document split", {"docId": doc_id, "chunks": len(chunks)})

    # 3. 向量化并存入内存向量库
    vs = await get_vector_store()
    await vs.add_documents(chunks)

    # 4. 注册文档元数据
    doc_meta = {
        "id": doc_id,
        "title": final_title,
        "fileName": file_name,
        "category": category,
        "chunks": len(chunks),
        "chars": len(raw_text),
        "uploadedAt": _now_iso(),
        "preview": raw_text[:120].replace("\n", " ") + "...",
    }
    _doc_registry[doc_id] = doc_meta

    # 5. 清理临时文件
    try:
        os.unlink(file_path)
    except OSError:
        pass

    logger.info("rag: ingest complete", {"docId": doc_id, "chunks": len(chunks)})
    return doc_meta


# ── 删除文档 ──────────────────────────────────────────────────
async def delete_document(doc_id: str):
    doc = _doc_registry.get(doc_id)
    if not doc:
        raise ValueError("文档不存在")

    if _vector_store:
        _vector_store.memory_vectors = [
            v for v in _vector_store.memory_vectors if v["metadata"].get("docId") != doc_id
        ]

    del _doc_registry[doc_id]
    logger.info("rag: document deleted", {"docId": doc_id})
