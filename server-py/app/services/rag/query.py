# server-py/app/services/rag/query.py
# RAG 查询：检索相关文档 + 生成有来源标注的回答
from langchain_core.prompts import ChatPromptTemplate

from app.services.model import chat_model
from app.services.rag.ingest import get_vector_store
from app.utils.logger import logger

# ── 相似度阈值：低于此值的文档不纳入参考 ─────────────────────
SIMILARITY_THRESHOLD = 0.3


async def retrieve_docs(question: str, category: str | None = None, k: int = 4) -> list[dict]:
    vs = await get_vector_store()

    filter_fn = (lambda meta: meta.get("category") == category) if category else None

    results = await vs.similarity_search_with_score(question, k, filter_fn)
    relevant = [(doc, score) for doc, score in results if score > SIMILARITY_THRESHOLD]

    logger.info("rag: retrieved docs", {
        "question": question[:40],
        "total": len(results),
        "relevant": len(relevant),
        "topScore": f"{results[0][1]:.3f}" if results else None,
    })

    return [
        {
            "content": doc["pageContent"],
            "score": round(score, 3),
            "title": doc["metadata"].get("title", "未知来源"),
            "docId": doc["metadata"].get("docId"),
            "category": doc["metadata"].get("category"),
            "preview": doc["pageContent"][:80].replace("\n", " ") + "...",
        }
        for doc, score in relevant
    ]


RAG_SYSTEM = """你是 WorkMind AI 知识库助手。

规则：
1. 只根据下方提供的参考文档回答问题，不使用文档之外的知识
2. 如果文档中没有相关内容，明确说"知识库中未找到相关内容"
3. 回答要准确、简洁，必要时列出要点
4. 在回答末尾用 【来源：文档名】 标注使用了哪些文档"""


def _build_context(docs: list[dict]) -> str:
    return "\n\n---\n\n".join(f"[参考{i + 1}] 来源：{d['title']}\n{d['content']}" for i, d in enumerate(docs))


async def rag_query(question: str, category: str | None = None) -> dict:
    docs = await retrieve_docs(question, category)

    if not docs:
        return {
            "answer": "知识库中未找到与该问题相关的内容。请尝试换一种提问方式，或上传相关文档后再试。",
            "sources": [],
        }

    context = _build_context(docs)
    prompt = ChatPromptTemplate.from_messages([
        ("system", RAG_SYSTEM),
        ("human", "参考文档：\n{context}\n\n问题：{question}"),
    ])
    chain = prompt | chat_model
    result = await chain.ainvoke({"context": context, "question": question})

    return {"answer": result.content, "sources": docs}


async def rag_query_stream(question: str, category: str | None = None):
    """返回 (sources, stream_answer) — stream_answer 是异步生成器函数"""
    docs = await retrieve_docs(question, category)

    async def stream_answer():
        if not docs:
            yield "知识库中未找到与该问题相关的内容。\n请尝试换一种提问方式，或上传相关文档后再试。"
            return

        context = _build_context(docs)
        prompt = ChatPromptTemplate.from_messages([
            ("system", RAG_SYSTEM),
            ("human", "参考文档：\n{context}\n\n问题：{question}"),
        ])
        chain = prompt | chat_model

        async for chunk in chain.astream({"context": context, "question": question}):
            if chunk.content:
                yield chunk.content

    return docs, stream_answer
