# server-py/app/routes/workflow.py
# 工作流路由：启动/查询/推进/获取结果
import random
import time

from fastapi import APIRouter, Depends, HTTPException

from app.middleware import rate_limiter
from app.services.workflow.workflows import WORKFLOW_BUILDERS, WORKFLOW_META
from app.utils.logger import logger
from app.utils.sse import sse_stream

router = APIRouter()

# 存活的工作流实例：threadId → { graph, meta, config }
_active_workflows: dict[str, dict] = {}


@router.get("/templates")
async def templates():
    return {"templates": list(WORKFLOW_META.values())}


@router.post("/start/stream", dependencies=[Depends(rate_limiter)])
async def start_stream(body: dict):
    workflow_id = body.get("workflowId")
    input_data = body.get("input") or {}

    if not workflow_id or workflow_id not in WORKFLOW_BUILDERS:
        raise HTTPException(status_code=400, detail={"error": {"message": f"未知工作流：{workflow_id}"}})

    rand = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=4))
    thread_id = f"wf_{int(time.time() * 1000)}_{rand}"
    config = {"configurable": {"thread_id": thread_id}}

    async def generator():
        builder = WORKFLOW_BUILDERS[workflow_id]
        graph = builder()
        meta = WORKFLOW_META[workflow_id]

        _active_workflows[thread_id] = {"graph": graph, "meta": meta, "config": config}

        yield "start", {"threadId": thread_id, "workflowId": workflow_id}
        logger.info("workflow: started", {"workflowId": workflow_id, "threadId": thread_id})

        last_node_name = None

        async for event in graph.astream_events(input_data, config, version="v2"):
            event_type = event["event"]
            name = event["name"]

            if event_type == "on_chain_start" and name not in ("__start__", "LangGraph"):
                node_in_meta = next((n for n in meta["nodes"] if n["id"] == name), None)
                if node_in_meta and name != last_node_name:
                    last_node_name = name
                    yield "node_start", {"nodeId": name, "label": node_in_meta["label"]}

            if event_type == "on_chain_end" and name not in ("__end__", "LangGraph"):
                node_in_meta = next((n for n in meta["nodes"] if n["id"] == name), None)
                if node_in_meta:
                    output = (event.get("data") or {}).get("output")
                    preview = ""
                    if isinstance(output, dict) and output:
                        first_val = next(iter(output.values()))
                        if isinstance(first_val, str) and first_val:
                            preview = first_val[:80] + ("..." if len(first_val) > 80 else "")
                    yield "node_done", {"nodeId": name, "preview": preview}

        state = await graph.aget_state(config)

        if state.next:
            yield "paused", {
                "threadId": thread_id,
                "nextNode": state.next[0],
                "intermediates": _get_intermediates(state.values, workflow_id),
            }
        else:
            result = state.values.get(meta["resultKey"], "")
            yield "completed", {"threadId": thread_id, "result": result}

    return sse_stream(generator)


@router.post("/resume/stream", dependencies=[Depends(rate_limiter)])
async def resume_stream(body: dict):
    thread_id = body.get("threadId")
    feedback = body.get("feedback")

    wf = _active_workflows.get(thread_id)
    if not wf:
        raise HTTPException(status_code=404, detail={"error": {"message": "工作流不存在或已过期，请重新启动"}})

    graph, meta, config = wf["graph"], wf["meta"], wf["config"]

    async def generator():
        if feedback and feedback.strip():
            await graph.aupdate_state(config, {"humanFeedback": feedback})

        logger.info("workflow: resumed", {"threadId": thread_id, "hasFeedback": bool(feedback)})
        yield "resumed", {"threadId": thread_id}

        last_node = None
        async for event in graph.astream_events(None, config, version="v2"):
            event_type = event["event"]
            name = event["name"]

            if event_type == "on_chain_start" and name not in ("__end__", "LangGraph"):
                node_in_meta = next((n for n in meta["nodes"] if n["id"] == name), None)
                if node_in_meta and name != last_node:
                    last_node = name
                    yield "node_start", {"nodeId": name, "label": node_in_meta["label"]}

            if event_type == "on_chat_model_stream":
                chunk = (event.get("data") or {}).get("chunk")
                content = getattr(chunk, "content", None) if chunk else None
                if content:
                    yield "token", {"token": content}

            if event_type == "on_chain_end":
                node_in_meta = next((n for n in meta["nodes"] if n["id"] == name), None)
                if node_in_meta:
                    yield "node_done", {"nodeId": name}

        final_state = await graph.aget_state(config)
        result = final_state.values.get(meta["resultKey"], "")

        yield "completed", {"threadId": thread_id, "result": result}

        _active_workflows.pop(thread_id, None)
        logger.info("workflow: completed", {"threadId": thread_id})

    return sse_stream(generator)


def _get_intermediates(values: dict, workflow_id: str) -> list[dict]:
    maps = {
        "weekly_report": {"highlights": "提炼的亮点", "risks": "风险/阻塞项"},
        "meeting_minutes": {"attendees": "参会人与议题", "conclusions": "会议结论", "actionItems": "Action Items"},
        "email_polish": {"purpose": "意图分析", "issues": "发现的问题"},
        "prd_skeleton": {"features": "功能点", "constraints": "约束条件"},
    }
    field_map = maps.get(workflow_id, {})
    return [{"key": k, "label": label, "value": values[k]} for k, label in field_map.items() if values.get(k)]
