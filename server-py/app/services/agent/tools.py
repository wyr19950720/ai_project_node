# server-py/app/services/agent/tools.py
# Agent 工具集：6 个工具，每个都有清晰的 name、description、schema
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.utils.logger import logger

# ── 工具1：联网搜索 ────────────────────────────────────────────
# 生产环境对接真实搜索 API（Tavily、SerpAPI、Bing Search）
# 这里用模拟数据演示工具调用流程
_MOCK_RESULTS = {
    "Vue3": "Vue 3.4.21 是目前最新版本，于2024年3月发布。主要改进：defineModel() 正式稳定，响应式系统性能提升约 56%，编译器优化减少生成代码量。",
    "React": "React 18.3 是最新稳定版，引入了并发渲染、useTransition、Suspense 改进。2024年主要关注点是 React Server Components 的稳定化。",
    "Vite": "Vite 5.2 是目前最新版本，使用 Rollup 4 构建，冷启动速度提升 30%，支持 Lightning CSS。推荐用于新项目。",
    "DeepSeek": "DeepSeek-V3 于2024年12月发布，是目前最强的开源 LLM 之一，性能接近 Claude 3.5 Sonnet，中文表现优秀，API 价格仅为 GPT-4o 的1/10。",
    "TypeScript": "TypeScript 5.7 是最新版本，新增 noUncheckedSideEffectImports 选项，改进了声明文件的处理方式。",
    "微前端": "qiankun 2.x 和 wujie 是国内最流行的微前端框架。wujie 基于 WebComponent + iframe，隔离性更好；qiankun 更成熟，社区更大。",
}


class WebSearchArgs(BaseModel):
    query: str = Field(description='搜索关键词，尽量精确，如"Vue3最新版本"而不是"前端框架"')


@tool("web_search", args_schema=WebSearchArgs)
async def search_tool(query: str) -> str:
    """搜索互联网获取最新技术资讯、版本信息、最佳实践。当需要了解某个技术的最新状态或不确定某个信息时使用。"""
    logger.info("tool:search", {"query": query})

    key = next((k for k in _MOCK_RESULTS if k.lower() in query.lower()), None)
    if key:
        return _MOCK_RESULTS[key]
    return f'关于"{query}"的搜索结果：该话题在技术社区有广泛讨论。建议查阅官方文档获取最准确的信息。'


# ── 工具2：读取知识库文档 ──────────────────────────────────────
class ReadDocArgs(BaseModel):
    question: str = Field(description="要查询的问题或关键词")


@tool("read_doc", args_schema=ReadDocArgs)
async def read_doc_tool(question: str) -> str:
    """从公司知识库检索文档内容。用于查询公司内部规定、产品手册、技术文档等。当问题涉及公司内部信息时优先使用。"""
    logger.info("tool:read_doc", {"question": question})

    try:
        from app.services.rag.query import retrieve_docs
        docs = await retrieve_docs(question, k=3)

        if not docs:
            return f'知识库中未找到关于"{question}"的相关内容。'

        return "\n\n".join(f"[文档{i + 1}] {d['title']}：{d['content']}" for i, d in enumerate(docs))
    except Exception:
        return "知识库暂时不可用，请稍后重试。"


# ── 工具3：数学计算 ────────────────────────────────────────────
class CalculateArgs(BaseModel):
    expression: str = Field(description='数学表达式，如 "1500 + 800 * 0.8" 或 "(200 + 350) * 3"')


@tool("calculate", args_schema=CalculateArgs)
async def calculate_tool(expression: str) -> str:
    """执行数学计算，支持加减乘除、括号、百分比。用于需要精确计算数值的场景，比如报销金额合计、工作日计算等。"""
    logger.info("tool:calculate", {"expression": expression})

    import re
    safe_expr = re.sub(r"[^0-9+\-*/().,\s%]", "", expression).strip()
    if not safe_expr:
        return "无效的数学表达式"

    try:
        safe_expr = safe_expr.replace("%", "/100")
        result = eval(safe_expr, {"__builtins__": {}}, {})  # noqa: S307 - 已过滤为纯数字表达式
        return f"计算结果：{expression} = {result}"
    except Exception as e:
        return f"计算失败：{e}"


# ── 工具4：获取日期信息 ─────────────────────────────────────────
class GetDateArgs(BaseModel):
    operation: Literal["today", "diff", "add_days"] = Field(
        description="today=获取今天日期, diff=计算日期差, add_days=日期加减")
    date1: str | None = Field(default=None, description="开始日期，格式 YYYY-MM-DD")
    date2: str | None = Field(default=None, description="结束日期或天数")


_WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


@tool("get_date", args_schema=GetDateArgs)
async def get_date_tool(operation: str, date1: str | None = None, date2: str | None = None) -> str:
    """获取日期信息：查询今天日期、计算两个日期之间的天数和工作日数、日期加减。用于请假天数计算、项目工期估算等。"""
    logger.info("tool:get_date", {"operation": operation, "date1": date1, "date2": date2})

    now = datetime.now()

    if operation == "today":
        return f"今天是 {now.year}年{now.month}月{now.day}日，星期{_WEEKDAY_CN[now.weekday()]}"

    if operation == "diff" and date1 and date2:
        try:
            d1 = datetime.strptime(date1, "%Y-%m-%d")
            d2 = datetime.strptime(date2, "%Y-%m-%d")
        except ValueError:
            return "日期格式不正确，请使用 YYYY-MM-DD 格式"

        diff_days = abs((d2 - d1).days)
        start, end = (d1, d2) if d1 < d2 else (d2, d1)
        workdays = 0
        d = start
        while d <= end:
            if d.weekday() < 5:
                workdays += 1
            d += timedelta(days=1)

        return f"{date1} 到 {date2}：共 {diff_days} 天，其中工作日 {workdays} 天"

    if operation == "add_days" and date1 and date2:
        try:
            d = datetime.strptime(date1, "%Y-%m-%d") + timedelta(days=int(date2))
        except ValueError:
            return "日期格式不正确，请使用 YYYY-MM-DD 格式"
        return f"{date1} 加 {date2} 天后是 {d.strftime('%Y-%m-%d')}"

    return f"今天是 {now.strftime('%Y-%m-%d')}"


# ── 工具5：生成并保存报告 ─────────────────────────────────────
class WriteReportArgs(BaseModel):
    title: str = Field(description="报告标题")
    content: str = Field(description="报告正文内容，使用 Markdown 格式")
    format: Literal["markdown", "plain"] | None = Field(default="markdown")


@tool("write_report", args_schema=WriteReportArgs)
async def write_report_tool(title: str, content: str, format: str | None = "markdown") -> str:
    """将分析结果整理成结构化报告并保存。当需要输出最终分析报告时使用，确保在收集到所有信息后才调用此工具。"""
    logger.info("tool:write_report", {"title": title, "format": format})

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    report = f"# {title}\n\n> 生成时间：{timestamp}\n\n{content}\n\n---\n*由 WorkMind AI Agent 自动生成*"

    return json.dumps({
        "success": True,
        "title": title,
        "content": report,
        "savedAt": timestamp,
        "message": f"报告「{title}」已生成，共 {len(report)} 字",
    }, ensure_ascii=False)


# ── 工具6：发送通知 ────────────────────────────────────────────
class SendNotifyArgs(BaseModel):
    to: str = Field(description='接收人，如"张三"或"tech-team"')
    subject: str = Field(description="消息主题")
    message: str = Field(description="消息正文（简洁）")
    channel: Literal["email", "feishu", "dingtalk"] | None = Field(default="feishu")


@tool("send_notify", args_schema=SendNotifyArgs)
async def send_notify_tool(to: str, subject: str, message: str, channel: str | None = "feishu") -> str:
    """发送消息通知。可以发送邮件、飞书消息或钉钉消息。用于任务完成后通知相关人员，或发送报告摘要。"""
    logger.info("tool:send_notify", {"to": to, "subject": subject, "channel": channel})

    await asyncio.sleep(0.2)  # 模拟网络延迟

    return json.dumps({
        "success": True,
        "to": to,
        "subject": subject,
        "channel": channel,
        "sentAt": datetime.now(timezone.utc).isoformat(),
        "message": f"通知已通过 {channel} 发送给 {to}",
    }, ensure_ascii=False)


# 导出所有工具（供 AgentService 使用）
all_tools = [
    search_tool,
    read_doc_tool,
    calculate_tool,
    get_date_tool,
    write_report_tool,
    send_notify_tool,
]
