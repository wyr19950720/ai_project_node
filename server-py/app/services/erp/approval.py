# server-py/app/services/erp/approval.py
# 真实多智能体审批流（Multi-Agent Collaboration）
# 相对旧的"角色扮演式"实现，本版本具备 4 个真实协作要素：
#   1. 结构化 Agent 协议：每个 Agent 输出 ApproverVerdict/ApplicantAnswer（Pydantic），
#      替代"关键词猜测文本意图"（is_approved / has_question）
#   2. 确定性工具节点：合规检查是纯代码工具（build_compliance_report），
#      与主管 Agent 并行执行，结果由财务 Agent 直接采信，不占用 LLM 计算
#   3. 结构化上下文注入：每个 Agent 只接收结构化输入（合规报告 + 上一环意见），
#      替代"全局共享长对话历史全文拼接"
#   4. 收敛机制：任一 Agent 驳回即短路终止；ask 交互有轮次上限；最终由确定性汇总仲裁
import asyncio
import json
from datetime import datetime, timezone
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.services.model import create_chat_model
from app.services.erp.parser import build_compliance_report
from app.utils.logger import logger

_model = create_chat_model(temperature=0.3)

# ── 审批角色定义 ──────────────────────────────────────────────
APPROVAL_ROLES = {
    "applicant": {"id": "applicant", "name": "申请人", "icon": "👤", "color": "#4f46e5", "desc": "提交申请，回答审批人的问题"},
    "manager": {"id": "manager", "name": "直属主管", "icon": "👔", "color": "#0891b2", "desc": "审核申请合理性，确认业务必要性"},
    "finance": {"id": "finance", "name": "财务专员", "icon": "💰", "color": "#059669", "desc": "审核费用合规性，确认金额和票据"},
    "hr": {"id": "hr", "name": "HR 专员", "icon": "📋", "color": "#d97706", "desc": "审核假期政策合规性，确认余额"},
    "director": {"id": "director", "name": "部门总监", "icon": "🏢", "color": "#dc2626", "desc": "大额报销或长期请假时的最终审批"},
}

# ask 交互轮次上限（收敛机制：防止无限追问）
MAX_ASK_ROUNDS = 2


# ── 结构化 Agent 通信协议 ─────────────────────────────────────
class ApproverVerdict(BaseModel):
    """审批 Agent 的结构化输出：三态决策 + 提问 + 意见"""

    decision: Literal["approve", "reject", "ask"] = Field(
        description="approve=批准, reject=驳回, ask=需要申请人补充说明"
    )
    question: Optional[str] = Field(
        default=None, description="decision 为 ask 时必填：要向申请人提的问题"
    )
    reason: str = Field(description="审批意见：批准/驳回的理由，80字以内")


class ApplicantAnswer(BaseModel):
    """申请人 Agent 的结构化输出"""

    answer: str = Field(description="对审批人问题的回答，60字以内")


# ── 结构化输出解析（稳健兼容 DeepSeek 包裹 JSON 的行为）────────
def _extract_verdict(text: str) -> ApproverVerdict:
    """解析模型输出为 ApproverVerdict；无 JSON 时按文本兜底"""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            # unwrap：兼容 {"verdict": {...}} / {"output": {...}} 包裹
            for key in ("verdict", "output", "result", "decision"):
                if isinstance(data, dict) and key in data and isinstance(data[key], dict):
                    data = data[key]
                    break
            return ApproverVerdict.model_validate(data)
        except Exception:
            pass
    # 兜底：无结构化 JSON 时按文本推断（保留旧逻辑兼容）
    reject_kws = ["驳回", "不批", "拒绝", "不同意", "不予批准", "无法批准"]
    ask_kws = ["请问", "能否", "？", "?"]
    if any(k in text for k in reject_kws):
        decision = "reject"
    elif any(k in text for k in ask_kws):
        decision = "ask"
    else:
        decision = "approve"
    return ApproverVerdict(decision=decision, reason=text.strip()[:80])


def _extract_answer(text: str) -> str:
    """解析申请人 Agent 的结构化回答；失败时直接返回原文"""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            for key in ("answer", "content", "response"):
                if isinstance(data, dict) and data.get(key):
                    return str(data[key]).strip()
        except Exception:
            pass
    return text.strip()


# ── 系统提示词（角色人格 + 结构化输出契约）────────────────────
def _get_role_system(
    role_id: str,
    form_data: dict,
    form_type: str,
    compliance_report: str = "",
    manager_opinion: str = "",
) -> str:
    form_json = json.dumps(form_data, ensure_ascii=False, indent=2)
    label = "报销" if form_type == "expense" else "请假"

    systems = {
        "applicant": f"""你是{form_data.get('applicantName', '小王')}，正在提交{label}申请。
申请内容：{form_json}
要求：简洁回答审批人的问题，提供必要的说明，像真实对话。
【输出要求】只输出一个 JSON 对象，不要输出其他任何内容：
{{"answer": "对问题的回答，60字以内"}}""",

        "manager": f"""你是直属主管，正在审核下属的{label}申请。
申请内容：{form_json}
你的职责：
1. 判断这次{'报销是否有业务必要性' if form_type == 'expense' else '请假是否影响团队工作'}
2. 金额或时间是否合理
3. 信息不足时 decision="ask" 并填写 question；信息充分时直接 approve/reject
4. 语气严肃专业，像真实的主管
【输出要求】只输出一个 JSON 对象，不要输出其他任何内容：
{{"decision": "approve 或 reject 或 ask", "question": "需要提问时填写，否则填 null", "reason": "审批意见，80字以内"}}""",

        "finance": f"""你是财务专员，负责审核报销合规性。
申请内容：{form_json}
【系统合规校验结果】（由程序按公司标准确定性计算，数值准确，请直接采信，严禁自行重新计算或推翻）：
{compliance_report}
【直属主管意见】{manager_opinion or "（无）"}
公司规定：
- 差旅：酒店每晚不超过800元，机票必须经济舱
- 餐饮：每天不超过200元，单次不超过500元
- 单笔超过3000元需附发票扫描件
审核要求：
1. 系统判定"合规"的项目，不得以任何理由判为超标或"偏高"
2. 系统判定"超标"或"不一致"的项目，decision="reject"，reason 指出原因
3. 金额无误且无业务问题 → decision="approve"
【输出要求】只输出一个 JSON 对象，不要输出其他任何内容：
{{"decision": "approve 或 reject 或 ask", "question": "需要提问时填写，否则填 null", "reason": "审批意见，80字以内"}}""",

        "hr": f"""你是 HR 专员，负责审核请假合规性。
申请内容：{form_json}
【直属主管意见】{manager_opinion or "（无）"}
假期规定：
- 年假：入职满1年后享有5天，每多1年增加1天，最多15天
- 事假：每年最多10天，超过3天影响年终绩效
- 病假：需提供医院证明
- 婚假：3天，需提供结婚证
审核要求：核实假期余额和规定，信息不足时 ask，否则直接 approve/reject。
【输出要求】只输出一个 JSON 对象，不要输出其他任何内容：
{{"decision": "approve 或 reject 或 ask", "question": "需要提问时填写，否则填 null", "reason": "审批意见，80字以内"}}""",

        "director": f"""你是部门总监，只处理大额报销（>5000元）或长假（>5工作日）。
申请内容：{form_json}
【直属主管意见】{manager_opinion or "（无）"}
你态度严格但公正，关注业务合理性和成本控制。
最终直接给出 approve 或 reject，不提问，并说明理由。
【输出要求】只输出一个 JSON 对象，不要输出其他任何内容：
{{"decision": "approve 或 reject", "question": null, "reason": "审批意见，100字以内"}}""",
    }

    return systems.get(role_id, systems["manager"])


# ── 审批流程规划（确定性）────────────────────────────────────
def _plan_approval_flow(form_data: dict, form_type: str) -> list[str]:
    flow = ["manager"]

    if form_type == "expense":
        flow.append("finance")
        if (form_data.get("totalAmount") or 0) > 5000:
            flow.append("director")
    else:
        flow.append("hr")
        if (form_data.get("workdays") or 0) > 5:
            flow.append("director")

    return flow


# ── 确定性工具节点 ────────────────────────────────────────────
async def _run_compliance_check(form_data: dict, form_type: str) -> str:
    """确定性合规检查（纯代码工具，不调用 LLM）：结果供财务 Agent 直接采信"""
    if form_type != "expense":
        return ""
    return build_compliance_report(form_data) or "（无费用明细，请在 items 中补充）"


# ── 单个审批 Agent 的执行 ─────────────────────────────────────
async def _call_verdict(system_prompt: str, message: HumanMessage) -> ApproverVerdict:
    resp = await _model.ainvoke([SystemMessage(content=system_prompt), message])
    return _extract_verdict(str(resp.content))


async def _ask_applicant(role_id: str, question: str, form_data: dict, form_type: str) -> str:
    """申请人 Agent：结构化回答审批人的提问"""
    applicant_system = _get_role_system("applicant", form_data, form_type)
    resp = await _model.ainvoke(
        [
            SystemMessage(content=applicant_system),
            HumanMessage(content=f"{APPROVAL_ROLES[role_id]['name']}提问：{question}"),
        ]
    )
    return _extract_answer(str(resp.content))


async def _ask_approver(
    role_id: str,
    form_data: dict,
    form_type: str,
    compliance_report: str,
    manager_opinion: str,
    on_event,
) -> tuple[bool, ApproverVerdict]:
    """审批 Agent 节点：结构化三态决策；ask 时与申请人 Agent 结构化交互，有轮次上限"""
    role = APPROVAL_ROLES[role_id]
    system_prompt = _get_role_system(role_id, form_data, form_type, compliance_report, manager_opinion)
    logger.info("erp: approver turn", {"roleId": role_id})

    await on_event("approver_start", {"roleId": role_id, "role": role})

    verdict = await _call_verdict(
        system_prompt,
        HumanMessage(content=f"请审核这份申请，按 JSON 输出审批意见。\n申请内容：{json.dumps(form_data, ensure_ascii=False)}"),
    )

    # 收敛：ask 交互最多 MAX_ASK_ROUNDS 轮（总监不提问）
    rounds = 0
    while verdict.decision == "ask" and role_id != "director" and rounds < MAX_ASK_ROUNDS:
        question = verdict.question or "请补充说明申请的具体情况"
        await on_event("message", {"from": role_id, "role": role, "content": question, "type": "question"})

        answer = await _ask_applicant(role_id, question, form_data, form_type)
        await on_event("message", {"from": "applicant", "role": APPROVAL_ROLES["applicant"], "content": answer, "type": "answer"})

        rounds += 1
        verdict = await _call_verdict(
            system_prompt,
            HumanMessage(content=f"申请人已回答：{answer}\n请基于回答给出最终审批意见（approve 或 reject），按 JSON 输出。"),
        )

    await on_event("message", {"from": role_id, "role": role, "content": verdict.reason, "type": "decision"})

    approved = verdict.decision == "approve"
    await on_event("approver_done", {"roleId": role_id, "role": role, "approved": approved, "comment": verdict.reason})
    return approved, verdict


# ── 确定性汇总仲裁 ────────────────────────────────────────────
def _build_result(approved: bool, comment: str, approver_ids: list[str], form_type: str) -> dict:
    return {
        "approved": approved,
        "status": "approved" if approved else "rejected",
        "comment": comment,
        "approvedBy": [APPROVAL_ROLES[r]["name"] for r in approver_ids] if approved else [],
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }


# ── 主流程：多智能体协作调度 ──────────────────────────────────
async def run_approval_flow(form_data: dict, form_type: str, on_event) -> dict:
    """
    真实多智能体协作：
      阶段1（并行）：确定性合规工具 ∥ 主管 Agent
      阶段2/3（条件串行）：财务/HR Agent（结构化输入：合规报告 + 主管意见）→ 总监 Agent
      阶段4（确定性）：汇总仲裁，任一驳回即收敛终止
    on_event 类型（与前端 SSE 协议一致）：
      'plan'           → 公布审批流程
      'approver_start' → 某个审批 Agent 开始审核
      'message'        → 某个 Agent 发出一条消息（question/answer/decision）
      'approver_done'  → 某个审批 Agent 给出决定
      'final'          → 最终审批结果
    """
    logger.info("erp: approval flow started", {"formType": form_type})

    approver_ids = _plan_approval_flow(form_data, form_type)
    label = "报销" if form_type == "expense" else "请假"

    await on_event("plan", {
        "approvers": [APPROVAL_ROLES[rid] for rid in approver_ids],
        "totalSteps": len(approver_ids),
    })

    # ── 阶段1：并行执行 ────────────────────────────────────────
    # 确定性合规工具（纯代码，零 LLM）与主管 Agent 同时启动
    compliance_task = asyncio.create_task(_run_compliance_check(form_data, form_type))
    manager_task = asyncio.create_task(_ask_approver("manager", form_data, form_type, "", "", on_event))
    compliance_report, (manager_approved, manager_verdict) = await asyncio.gather(compliance_task, manager_task)

    if not manager_approved:
        # 收敛：主管驳回 → 终止
        result = _build_result(False, f"被{APPROVAL_ROLES['manager']['name']}驳回：{manager_verdict.reason}", [], form_type)
        await on_event("final", result)
        logger.info("erp: approval flow done", {"formType": form_type, "approved": False})
        return result

    # ── 阶段2/3：后续审批 Agent（结构化上下文注入，依赖前一环）──
    # manager 已在并行阶段处理，此处只走后续角色
    last_opinion = manager_verdict.reason
    for role_id in approver_ids[1:]:
        approved, verdict = await _ask_approver(role_id, form_data, form_type, compliance_report, last_opinion, on_event)
        if not approved:
            # 收敛：任一 Agent 驳回 → 短路终止
            result = _build_result(False, f"被{APPROVAL_ROLES[role_id]['name']}驳回：{verdict.reason}", [], form_type)
            await on_event("final", result)
            logger.info("erp: approval flow done", {"formType": form_type, "approved": False})
            return result
        last_opinion = verdict.reason
        await asyncio.sleep(0.3)  # 停顿，让前端看清步骤

    # ── 阶段4：确定性汇总仲裁（全部通过）──────────────────────
    result = _build_result(True, last_opinion, approver_ids, form_type)
    await on_event("final", result)
    logger.info("erp: approval flow done", {"formType": form_type, "approved": True})

    return result
