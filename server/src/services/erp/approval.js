// server/src/services/erp/approval.js
// 真实多智能体审批流（Multi-Agent Collaboration）
// 相对旧的"角色扮演式"实现，本版本具备 4 个真实协作要素：
//   1. 结构化 Agent 协议：每个 Agent 输出 ApproverVerdict/ApplicantAnswer（zod），
//      替代"关键词猜测文本意图"（isApproved / hasQuestion）
//   2. 确定性工具节点：合规检查是纯代码工具（checkCompliance），
//      与主管 Agent 并行执行，结果由财务 Agent 直接采信，不占用 LLM 计算
//   3. 结构化上下文注入：每个 Agent 只接收结构化输入（合规报告 + 上一环意见），
//      替代"全局共享长对话历史全文拼接"
//   4. 收敛机制：任一 Agent 驳回即短路终止；ask 交互有轮次上限；最终由确定性汇总仲裁
import { createChatModel } from '../model.js'
import { HumanMessage, SystemMessage } from '@langchain/core/messages'
import { z } from 'zod'
import { logger } from '../../utils/logger.js'
import { checkCompliance } from './parser.js'

const model = createChatModel({ temperature: 0.3 })

// ── 审批角色定义 ──────────────────────────────────────────────
const APPROVAL_ROLES = {
  applicant: {
    id:    'applicant',
    name:  '申请人',
    icon:  '👤',
    color: '#4f46e5',
    desc:  '提交申请，回答审批人的问题',
  },
  manager: {
    id:    'manager',
    name:  '直属主管',
    icon:  '👔',
    color: '#0891b2',
    desc:  '审核申请合理性，确认业务必要性',
  },
  finance: {
    id:    'finance',
    name:  '财务专员',
    icon:  '💰',
    color: '#059669',
    desc:  '审核费用合规性，确认金额和票据',
  },
  hr: {
    id:    'hr',
    name:  'HR 专员',
    icon:  '📋',
    color: '#d97706',
    desc:  '审核假期政策合规性，确认余额',
  },
  director: {
    id:    'director',
    name:  '部门总监',
    icon:  '🏢',
    color: '#dc2626',
    desc:  '大额报销或长期请假时的最终审批',
  },
}

// ask 交互轮次上限（收敛机制：防止无限追问）
const MAX_ASK_ROUNDS = 2

// ── 结构化 Agent 通信协议 ─────────────────────────────────────
const ApproverVerdictSchema = z.object({
  decision: z.enum(['approve', 'reject', 'ask'])
    .describe('approve=批准, reject=驳回, ask=需要申请人补充说明'),
  question: z.string().nullable().optional().describe('decision 为 ask 时必填'),
  reason: z.string().describe('审批意见，80字以内'),
})

const ApplicantAnswerSchema = z.object({
  answer: z.string().describe('对审批人问题的回答，60字以内'),
})

// ── 系统提示词（角色人格 + 结构化输出契约）───────────────────
function getRoleSystem(roleId, formData, formType, complianceReport = '', managerOpinion = '') {
  const formJson = JSON.stringify(formData, null, 2)
  const label = formType === 'expense' ? '报销' : '请假'

  const verdictContract =
    '【输出要求】只输出一个 JSON 对象，不要输出其他任何内容：\n' +
    '{"decision": "approve 或 reject 或 ask", "question": "需要提问时填写，否则填 null", "reason": "审批意见，80字以内"}'

  const systems = {
    applicant: `你是${formData.applicantName || '小王'}，正在提交${label}申请。
申请内容：${formJson}
要求：简洁回答审批人的问题，提供必要的说明，像真实对话。
【输出要求】只输出一个 JSON 对象，不要输出其他任何内容：
{"answer": "对问题的回答，60字以内"}`,

    manager: `你是直属主管，正在审核下属的${label}申请。
申请内容：${formJson}
你的职责：
1. 判断这次${formType === 'expense' ? '报销是否有业务必要性' : '请假是否影响团队工作'}
2. 金额或时间是否合理
3. 信息不足时 decision="ask" 并填写 question；信息充分时直接 approve/reject
4. 语气严肃专业，像真实的主管
${verdictContract}`,

    finance: `你是财务专员，负责审核报销合规性。
申请内容：${formJson}
【系统合规校验结果】（由程序按公司标准确定性计算，数值准确，请直接采信，严禁自行重新计算或推翻）：
${complianceReport || '（无明细）'}
【直属主管意见】${managerOpinion || '（无）'}
公司规定：
- 差旅：酒店每晚不超过800元，机票必须经济舱
- 餐饮：每天不超过200元，单次不超过500元
- 单笔超过3000元需附发票扫描件
审核要求：
1. 系统判定"合规"的项目，不得以任何理由判为超标或"偏高"
2. 系统判定"超标"或"不一致"的项目，decision="reject"，reason 指出原因
3. 金额无误且无业务问题 → decision="approve"
${verdictContract}`,

    hr: `你是 HR 专员，负责审核请假合规性。
申请内容：${formJson}
【直属主管意见】${managerOpinion || '（无）'}
假期规定：
- 年假：入职满1年后享有5天，每多1年增加1天，最多15天
- 事假：每年最多10天，超过3天影响年终绩效
- 病假：需提供医院证明
- 婚假：3天，需提供结婚证
审核要求：核实假期余额和规定，信息不足时 ask，否则直接 approve/reject。
${verdictContract}`,

    director: `你是部门总监，只处理大额报销（>5000元）或长假（>5工作日）。
申请内容：${formJson}
【直属主管意见】${managerOpinion || '（无）'}
你态度严格但公正，关注业务合理性和成本控制。
最终直接给出 approve 或 reject，不提问，并说明理由。
【输出要求】只输出一个 JSON 对象，不要输出其他任何内容：
{"decision": "approve 或 reject", "question": null, "reason": "审批意见，100字以内"}`,
  }

  return systems[roleId] || systems.manager
}

// ── 审批流程规划（确定性）────────────────────────────────────
function planApprovalFlow(formData, formType) {
  const flow = ['manager']  // 主管是必须的

  if (formType === 'expense') {
    flow.push('finance')  // 报销必须过财务
    if (formData.totalAmount > 5000) {
      flow.push('director')  // 大额需要总监
    }
  } else {
    flow.push('hr')  // 请假必须过 HR
    if ((formData.workdays || 0) > 5) {
      flow.push('director')  // 长假需要总监
    }
  }

  return flow
}

// ── 确定性工具节点 ────────────────────────────────────────────
function buildComplianceReport(expenseForm) {
  // 确定性合规检查（纯代码工具，不调用 LLM）：结果供财务 Agent 直接采信
  if (!expenseForm) return ''
  const alerts = checkCompliance(expenseForm)
  if (!alerts.length) return '（未发现违规项，全部合规）'
  return alerts.map(a => `- ${a}`).join('\n')
}

// ── 结构化输出解析（带文本兜底）──────────────────────────────
async function callVerdict(systemPrompt, message) {
  try {
    const structuredModel = model.withStructuredOutput(ApproverVerdictSchema)
    const v = await structuredModel.invoke([new SystemMessage(systemPrompt), message])
    return {
      decision: v.decision || 'approve',
      question: v.question || null,
      reason:   v.reason || '',
    }
  } catch (err) {
    // 兜底：解析失败时按文本推断（兼容旧逻辑）
    logger.warn('erp: structured verdict fallback', { error: err.message })
    const text = typeof message?.content === 'string' ? message.content : ''
    const rejectKws = ['驳回', '不批', '拒绝', '不同意', '不予批准', '无法批准']
    const askKws = ['请问', '能否', '？', '?']
    let decision = 'approve'
    if (rejectKws.some(kw => text.includes(kw))) decision = 'reject'
    else if (askKws.some(kw => text.includes(kw))) decision = 'ask'
    return { decision, question: null, reason: text.slice(0, 80) }
  }
}

// ── 单个审批 Agent 的执行 ─────────────────────────────────────
async function askApplicant(roleId, question, formData, formType) {
  const applicantSystem = getRoleSystem('applicant', formData, formType)
  try {
    const structuredModel = model.withStructuredOutput(ApplicantAnswerSchema)
    const v = await structuredModel.invoke([
      new SystemMessage(applicantSystem),
      new HumanMessage(`${APPROVAL_ROLES[roleId].name}提问：${question}`),
    ])
    return v.answer || '已补充说明。'
  } catch (err) {
    logger.warn('erp: applicant answer fallback', { error: err.message })
    return '已补充说明。'
  }
}

async function askApprover(roleId, formData, formType, complianceReport, managerOpinion, onEvent) {
  const role = APPROVAL_ROLES[roleId]
  const systemPrompt = getRoleSystem(roleId, formData, formType, complianceReport, managerOpinion)

  logger.info('erp: approver turn', { roleId })

  onEvent('approver_start', { roleId, role })

  let verdict = await callVerdict(
    systemPrompt,
    new HumanMessage(`请审核这份申请，按 JSON 输出审批意见。\n申请内容：${JSON.stringify(formData)}`)
  )

  // 收敛：ask 交互最多 MAX_ASK_ROUNDS 轮（总监不提问）
  let rounds = 0
  while (verdict.decision === 'ask' && roleId !== 'director' && rounds < MAX_ASK_ROUNDS) {
    const question = verdict.question || '请补充说明申请的具体情况'
    onEvent('message', { from: roleId, role, content: question, type: 'question' })

    const answer = await askApplicant(roleId, question, formData, formType)
    onEvent('message', { from: 'applicant', role: APPROVAL_ROLES.applicant, content: answer, type: 'answer' })

    rounds += 1
    verdict = await callVerdict(
      systemPrompt,
      new HumanMessage(`申请人已回答：${answer}\n请基于回答给出最终审批意见（approve 或 reject），按 JSON 输出。`)
    )
  }

  onEvent('message', { from: roleId, role, content: verdict.reason, type: 'decision' })

  const approved = verdict.decision === 'approve'
  onEvent('approver_done', { roleId, role, approved, comment: verdict.reason })
  return { approved, verdict }
}

// ── 确定性汇总仲裁 ────────────────────────────────────────────
function buildResult(approved, comment, approverIds) {
  return {
    approved:    approved,
    status:      approved ? 'approved' : 'rejected',
    comment:     comment,
    approvedBy:  approved ? approverIds.map(id => APPROVAL_ROLES[id].name) : [],
    completedAt: new Date().toISOString(),
  }
}

// ── 主流程：多智能体协作调度 ──────────────────────────────────
/**
 * @param {object} formData   - 解析好的结构化表单数据
 * @param {string} formType   - 'expense' | 'leave'
 * @param {function} onEvent  - 事件回调
 *
 * 真实多智能体协作：
 *   阶段1（并行）：确定性合规工具 ∥ 主管 Agent
 *   阶段2/3（条件串行）：财务/HR Agent（结构化输入：合规报告 + 主管意见）→ 总监 Agent
 *   阶段4（确定性）：汇总仲裁，任一驳回即收敛终止
 *
 * onEvent 类型（与前端 SSE 协议一致）：
 *   'plan'           → 公布审批流程（需要哪些角色）
 *   'approver_start' → 某个审批 Agent 开始审核
 *   'message'        → 某个 Agent 发出一条消息（question/answer/decision）
 *   'approver_done'  → 某个审批 Agent 给出决定
 *   'final'          → 最终审批结果
 */
export async function runApprovalFlow(formData, formType, onEvent) {
  logger.info('erp: approval flow started', { formType })

  // 1. 规划审批流程（确定性）
  const approverIds = planApprovalFlow(formData, formType)

  onEvent('plan', {
    approvers: approverIds.map(id => APPROVAL_ROLES[id]),
    totalSteps: approverIds.length,
  })

  // 2. 阶段1：并行执行
  //    确定性合规工具（纯代码，零 LLM）与主管 Agent 同时启动
  const [complianceReport, [managerApproved, managerVerdict]] = await Promise.all([
    Promise.resolve(buildComplianceReport(formType === 'expense' ? formData : null)),
    askApprover('manager', formData, formType, '', '', onEvent),
  ])

  if (!managerApproved) {
    // 收敛：主管驳回 → 终止
    const result = buildResult(false, `被${APPROVAL_ROLES.manager.name}驳回：${managerVerdict.reason}`, [])
    onEvent('final', result)
    logger.info('erp: approval flow done', { formType, approved: false })
    return result
  }

  // 3. 阶段2/3：后续审批 Agent（结构化上下文注入，依赖前一环）
  //    manager 已在并行阶段处理，此处只走后续角色
  let lastOpinion = managerVerdict.reason
  for (let i = 1; i < approverIds.length; i++) {
    const roleId = approverIds[i]

    const { approved, verdict } = await askApprover(roleId, formData, formType, complianceReport, lastOpinion, onEvent)

    if (!approved) {
      // 收敛：任一 Agent 驳回 → 短路终止
      const result = buildResult(false, `被${APPROVAL_ROLES[roleId].name}驳回：${verdict.reason}`, [])
      onEvent('final', result)
      logger.info('erp: approval flow done', { formType, approved: false })
      return result
    }

    lastOpinion = verdict.reason
    await new Promise(r => setTimeout(r, 300))  // 停顿，让前端看清步骤
  }

  // 4. 阶段4：确定性汇总仲裁（全部通过）
  const result = buildResult(true, lastOpinion, approverIds)
  onEvent('final', result)
  logger.info('erp: approval flow done', { formType, approved: true })

  return result
}

export { APPROVAL_ROLES }
