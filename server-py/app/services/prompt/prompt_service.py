# server-py/app/services/prompt/prompt_service.py
# Prompt 调试：模板管理、A/B 测试评分
import asyncio
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.services.model import create_chat_model

# 评分模型用 temperature=0，结果稳定
_score_model = create_chat_model(temperature=0)

# ── Prompt 模板存储（生产用数据库）────────────────────────────
_template_store: dict[str, dict] = {}
_template_id_seq = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 内置示例模板
_default_templates = [
    {
        "id": "t_default_1",
        "name": "前端助手",
        "systemPrompt": "你是前端开发专家，精通 Vue3、React、TypeScript。回答简洁准确，必要时给代码示例。",
        "description": "通用前端技术问答",
        "tags": ["前端", "技术"],
        "createdAt": _now_iso(),
        "versions": [],
    },
    {
        "id": "t_default_2",
        "name": "代码 Review",
        "systemPrompt": """你是资深代码评审专家。审查代码时，按以下顺序输出：
1. 【总体评价】一句话概括
2. 【问题列表】按严重程度排序，每条格式：[严重/一般/建议] 具体问题
3. 【优化建议】具体的改进代码示例
语气专业，直指问题，不废话。""",
        "description": "代码审查专用",
        "tags": ["代码", "审查"],
        "createdAt": _now_iso(),
        "versions": [],
    },
    {
        "id": "t_default_3",
        "name": "简洁问答",
        "systemPrompt": "用最简洁的语言回答问题，不超过3句话，不用废话开场。",
        "description": "简短精准的回答风格",
        "tags": ["简洁"],
        "createdAt": _now_iso(),
        "versions": [],
    },
]

for _t in _default_templates:
    _template_store[_t["id"]] = _t


# ── 模板 CRUD ─────────────────────────────────────────────────
def list_templates() -> list[dict]:
    return sorted(_template_store.values(), key=lambda t: t["createdAt"], reverse=True)


def get_template(template_id: str) -> dict | None:
    return _template_store.get(template_id)


def save_template(name: str, system_prompt: str, description: str = "", tags: list[str] | None = None,
                   existing_id: str | None = None) -> dict:
    global _template_id_seq
    tags = tags or []
    template_id = existing_id or f"t_{int(time.time() * 1000)}_{_template_id_seq}"
    _template_id_seq += 1
    existing = _template_store.get(template_id)

    versions = [*(existing.get("versions", []) if existing else []), {
        "version": len(existing.get("versions", [])) + 1 if existing else 1,
        "systemPrompt": existing["systemPrompt"] if existing else system_prompt,
        "savedAt": _now_iso(),
    }][-10:]

    template = {
        "id": template_id,
        "name": name,
        "systemPrompt": system_prompt,
        "description": description,
        "tags": tags,
        "createdAt": existing["createdAt"] if existing else _now_iso(),
        "updatedAt": _now_iso(),
        "versions": versions,
    }

    _template_store[template_id] = template
    return template


def delete_template(template_id: str):
    if template_id.startswith("t_default_"):
        raise ValueError("内置模板不能删除")
    if template_id not in _template_store:
        raise ValueError("模板不存在")
    del _template_store[template_id]


# ── A/B 测试：AI 自动评分 ──────────────────────────────────────
class _Evaluation(BaseModel):
    relevance: int = Field(ge=1, le=5, description="回答与问题的相关性")
    accuracy: int = Field(ge=1, le=5, description="内容的准确性")
    clarity: int = Field(ge=1, le=5, description="表达的清晰度")
    conciseness: int = Field(ge=1, le=5, description="是否简洁，不啰嗦")
    overall: int = Field(ge=1, le=5, description="综合评分")


class _Comparison(BaseModel):
    winner: Literal["A", "B", "tie"]
    reason: str = Field(description="对比理由，30字以内")


async def score_ab_test(question: str, answer_a: str, answer_b: str) -> dict:
    eval_model = _score_model.with_structured_output(_Evaluation)

    eval_a, eval_b = await asyncio.gather(
        eval_model.ainvoke([
            {"role": "system", "content": "你是 AI 回答质量评估专家，客观评分，不偏袒任何一方。"},
            {"role": "user", "content": f"问题：{question}\n\n回答：{answer_a}"},
        ]),
        eval_model.ainvoke([
            {"role": "system", "content": "你是 AI 回答质量评估专家，客观评分，不偏袒任何一方。"},
            {"role": "user", "content": f"问题：{question}\n\n回答：{answer_b}"},
        ]),
    )

    compare_model = _score_model.with_structured_output(_Comparison)
    comparison: _Comparison = await compare_model.ainvoke([
        {"role": "system", "content": "比较两个回答，选出更好的那个。评分相差0.5分以内视为平局。"},
        {"role": "user", "content": f"""
问题：{question}

回答A：{answer_a}
A的评分：相关性{eval_a.relevance} 准确性{eval_a.accuracy} 清晰度{eval_a.clarity} 简洁性{eval_a.conciseness} 综合{eval_a.overall}

回答B：{answer_b}
B的评分：相关性{eval_b.relevance} 准确性{eval_b.accuracy} 清晰度{eval_b.clarity} 简洁性{eval_b.conciseness} 综合{eval_b.overall}

哪个回答更好？"""},
    ])

    return {
        "scoreA": eval_a.model_dump(),
        "scoreB": eval_b.model_dump(),
        "winner": comparison.winner,
        "reason": comparison.reason,
    }
