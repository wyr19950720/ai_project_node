# server-py/app/services/agent/agent.py
# ReAct Agent：Reason + Act 循环
# 模型自主决定：思考 → 选工具 → 执行 → 观察结果 → 继续思考 → 直到完成
import json
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.services.agent.tools import all_tools
from app.services.model import create_chat_model
from app.utils.logger import logger

# ── Agent System Prompt ────────────────────────────────────────
AGENT_SYSTEM = """你是 WorkMind AI 任务助手，专门处理办公场景的复杂任务。

可用工具：
- web_search：搜索最新技术资讯和信息
- read_doc：从公司知识库检索文档
- calculate：数学计算（金额、工期等）
- get_date：日期查询和计算
- write_report：生成并保存分析报告
- send_notify：发送通知给相关人员

工作原则：
1. 先理解任务的完整需求，想好需要哪些步骤
2. 按最少工具调用完成任务，避免重复查询
3. 获取到足够信息后立刻生成最终回答，不要继续无谓的工具调用
4. 回答要完整、准确，必要时生成报告

注意：
- 每次只调用一个工具，等结果回来再决定下一步
- 最多执行 8 步工具调用，超过后用已有信息给出最佳回答"""


# ── LangGraph Agent 状态 ──────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    steps: int


# 使用 temperature=0 让工具调用更确定
_agent_model = create_chat_model(temperature=0, streaming=True)
_model_with_tools = _agent_model.bind_tools(all_tools)
_tool_node = ToolNode(all_tools)


async def _agent_node(state: AgentState):
    response = await _model_with_tools.ainvoke([SystemMessage(content=AGENT_SYSTEM), *state["messages"]])
    return {"messages": [response], "steps": state["steps"] + 1}


def _should_continue(state: AgentState):
    last = state["messages"][-1]
    if state["steps"] >= 8:
        logger.warn("agent: max steps reached", {"steps": state["steps"]})
        return "__end__"
    return "tools" if getattr(last, "tool_calls", None) else "__end__"


_graph_builder = (
    StateGraph(AgentState)
    .add_node("agent", _agent_node)
    .add_node("tools", _tool_node)
    .add_edge(START, "agent")
    .add_conditional_edges("agent", _should_continue, {"tools": "tools", "__end__": END})
    .add_edge("tools", "agent")
)
agent_graph = _graph_builder.compile()


_TOOL_LABELS = {
    "web_search": "联网搜索",
    "read_doc": "检索知识库",
    "calculate": "数学计算",
    "get_date": "日期查询",
    "write_report": "生成报告",
    "send_notify": "发送通知",
}


def _get_tool_label(tool_name: str) -> str:
    return _TOOL_LABELS.get(tool_name, tool_name)


async def run_agent(task: str, on_event):
    """
    流式执行 Agent，推送每一步的状态
    on_event(type, data) 的 type：
      'tool_call'   → 模型决定调用某个工具
      'tool_result' → 工具执行完成
      'token'       → 最终回答的流式 token
      'done'        → 全部完成
      'error'       → 出错
    """
    logger.info("agent: start", {"task": task[:60]})

    try:
        step_count = 0

        async for event in agent_graph.astream_events(
            {"messages": [HumanMessage(content=task)], "steps": 0},
            version="v2",
        ):
            event_type = event["event"]
            name = event["name"]
            data = event.get("data", {})

            if event_type == "on_tool_start":
                step_count += 1
                await on_event("tool_call", {
                    "step": step_count,
                    "toolName": name,
                    "args": data.get("input"),
                    "label": _get_tool_label(name),
                })

            if event_type == "on_tool_end":
                output = data.get("output")
                result = getattr(output, "content", output)
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except (json.JSONDecodeError, TypeError):
                        pass
                await on_event("tool_result", {
                    "toolName": name,
                    "result": result,
                    "resultText": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
                })

            if event_type == "on_chat_model_stream" and name == "ChatOpenAI":
                chunk = data.get("chunk")
                content = getattr(chunk, "content", None) if chunk else None
                tool_call_chunks = getattr(chunk, "tool_call_chunks", None) if chunk else None
                if content and not tool_call_chunks:
                    await on_event("token", {"token": content})

        await on_event("done", {"steps": step_count})
        logger.info("agent: done", {"steps": step_count})
    except Exception as err:
        logger.error("agent: error", {"error": str(err)})
        await on_event("error", {"message": str(err) or "Agent 执行出错"})


def get_tool_list() -> list[dict]:
    return [{"name": t.name, "label": _get_tool_label(t.name), "description": t.description} for t in all_tools]
