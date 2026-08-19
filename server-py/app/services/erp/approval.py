# server-py/app/services/erp/approval.py
# Multi-Agent 审批流：多个 Agent 扮演不同角色，模拟企业审批过程
import asyncio
import json
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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


# ── 系统提示词（每个角色的人格设定）──────────────────────────
def _get_role_system(role_id: str, form_data: dict, form_type: str) -> str:
    form_json = json.dumps(form_data, ensure_ascii=False, indent=2)
    label = "报销" if form_type == "expense" else "请假"
    # 报销申请：系统确定性合规报告（供财务 Agent 采信，避免模型算术误判）
    compliance_report = ""
    if form_type == "expense":
        compliance_report = build_compliance_report(form_data) or "（无明细）"

    systems = {
        "applicant": f"""你是{form_data.get('applicantName', '小王')}，正在提交{label}申请。
申请内容：{form_json}
要求：简洁回答审批人的问题，提供必要的说明。语气自然，像真实对话。不超过60字。""",

        "manager": f"""你是直属主管，正在审核下属的{label}申请。
申请内容：{form_json}
你的职责：
1. 判断这次{'报销是否有业务必要性' if form_type == 'expense' else '请假是否影响团队工作'}
2. 金额或时间是否合理
3. 可以提问补充信息，然后给出批准/驳回/要求补充的意见
4. 语气严肃专业，像真实的主管。不超过80字。""",

        "finance": f"""你是财务专员，负责审核报销合规性。
申请内容：{form_json}
公司规定：
- 差旅：酒店每晚不超过800元，机票必须经济舱
- 餐饮：每天不超过200元，单次不超过500元
- 单笔超过3000元需附发票扫描件
【系统合规校验结果】（由程序按公司标准确定性计算，数值准确，请直接采信，严禁自行重新计算或推翻）：
{compliance_report}
审核要求：
1. 系统判定"合规"的项目，不得以任何理由判为超标或"偏高"
2. 系统判定"超标"或"不一致"的项目，指出并说明原因
3. 可补充指出业务层面问题（如信息不全、事由笼统）
4. 你的职责：检查是否合规，发现问题要指出。不超过80字。""",

        "hr": f"""你是 HR 专员，负责审核请假合规性。
申请内容：{form_json}
假期规定：
- 年假：入职满1年后享有5天，每多1年增加1天，最多15天
- 事假：每年最多10天，超过3天影响年终绩效
- 病假：需提供医院证明
- 婚假：3天，需提供结婚证
你的职责：核实假期余额和规定。不超过80字。""",

        "director": f"""你是部门总监，只处理大额报销（>5000元）或长假（>5工作日）。
申请内容：{form_json}
你态度严格但公正，关注业务合理性和成本控制。
最终给出明确的批准或驳回，并说明理由。不超过100字。""",
    }

    return systems.get(role_id, systems["manager"])


# ── 审批流程规划 ──────────────────────────────────────────────
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


def _is_approved(text: str) -> bool:
    reject_keywords = ["驳回", "不批", "拒绝", "不同意", "不予批准", "无法批准"]
    return not any(kw in text for kw in reject_keywords)


# ── 单个审批角色的对话执行 ────────────────────────────────────
async def _run_approver_turn(role_id: str, form_data: dict, form_type: str, conversation_history: list, on_event):
    role = APPROVAL_ROLES[role_id]
    system_prompt = _get_role_system(role_id, form_data, form_type)

    logger.info("erp: approver turn", {"roleId": role_id})

    question_response = await _model.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content="请审核这份申请。如果有疑问，可以提问；如果信息充分，直接给出审批意见（批准/驳回）。"),
        *conversation_history,
    ])

    question_text = question_response.content

    await on_event("message", {"from": role_id, "role": role, "content": question_text, "type": "question"})
    conversation_history.append(AIMessage(content=f"[{role['name']}]：{question_text}"))

    has_question = any(s in question_text for s in ["？", "?", "请问", "能否"])

    if has_question and role_id != "director":
        applicant_system = _get_role_system("applicant", form_data, form_type)
        answer_response = await _model.ainvoke([
            SystemMessage(content=applicant_system),
            *conversation_history,
            HumanMessage(content=f"{role['name']}刚才提了问题，请以申请人身份回答"),
        ])
        answer_text = answer_response.content

        await on_event("message", {"from": "applicant", "role": APPROVAL_ROLES["applicant"], "content": answer_text, "type": "answer"})
        conversation_history.append(AIMessage(content=f"[申请人]：{answer_text}"))

        decision_response = await _model.ainvoke([
            SystemMessage(content=system_prompt),
            *conversation_history,
            HumanMessage(content="申请人已经回答了你的问题，现在请给出最终的审批意见：批准或驳回，并说明理由。"),
        ])
        decision_text = decision_response.content

        await on_event("message", {"from": role_id, "role": role, "content": decision_text, "type": "decision"})
        conversation_history.append(AIMessage(content=f"[{role['name']}]：{decision_text}"))

        return _is_approved(decision_text), decision_text

    return _is_approved(question_text), question_text


async def run_approval_flow(form_data: dict, form_type: str, on_event) -> dict:
    """
    on_event 类型：
      'plan'           → 公布审批流程
      'approver_start' → 某个审批人开始审核
      'message'        → 某个角色发出一条消息
      'approver_done'  → 某个审批人给出决定
      'final'          → 最终审批结果
    """
    logger.info("erp: approval flow started", {"formType": form_type})

    approver_ids = _plan_approval_flow(form_data, form_type)
    label = "报销" if form_type == "expense" else "请假"

    await on_event("plan", {
        "approvers": [APPROVAL_ROLES[rid] for rid in approver_ids],
        "totalSteps": len(approver_ids),
    })

    conversation_history = [
        HumanMessage(content=f"申请人提交了{label}申请：\n{json.dumps(form_data, ensure_ascii=False, indent=2)}"),
    ]

    all_approved = True
    final_comment = ""

    for role_id in approver_ids:
        role = APPROVAL_ROLES[role_id]
        await on_event("approver_start", {"roleId": role_id, "role": role})

        approved, comment = await _run_approver_turn(role_id, form_data, form_type, conversation_history, on_event)

        await on_event("approver_done", {"roleId": role_id, "role": role, "approved": approved, "comment": comment})

        if not approved:
            all_approved = False
            final_comment = f"被{role['name']}驳回：{comment}"
            break

        final_comment = comment
        await asyncio.sleep(0.3)

    result = {
        "approved": all_approved,
        "status": "approved" if all_approved else "rejected",
        "comment": final_comment,
        "approvedBy": [APPROVAL_ROLES[rid]["name"] for rid in approver_ids] if all_approved else [],
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }

    await on_event("final", result)
    logger.info("erp: approval flow done", {"formType": form_type, "approved": all_approved})

    return result
