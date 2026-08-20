// frontend/src/stores/erp.js
// ERP 模块状态：表单解析、审批流、申请记录
import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { fetchStream } from '@/utils/http.js'
import http from '@/utils/http.js'
import { useAppStore } from './app.js'

export const useErpStore = defineStore('erp', () => {
  const appStore = useAppStore()

  // ── 当前表单 ──────────────────────────────────────────────
  // 'expense' | 'leave'
  const formType   = ref('expense')
  // 解析出的结构化表单数据
  const parsedForm = ref(null)
  // 解析中
  const parsing    = ref(false)

  // ── 审批流状态 ────────────────────────────────────────────
  // 审批过程中产生的所有消息（对话气泡列表）
  const approvalMessages = ref([])
  // 当前审批流程步骤 [{ roleId, role, status: 'pending'|'running'|'approved'|'rejected' }]
  const approvalSteps    = ref([])
  // 审批是否进行中
  const approving  = ref(false)
  // 最终审批结果
  const finalResult = ref(null)
  // 当前申请编号
  const currentAppId = ref('')

  // ── 申请列表 ──────────────────────────────────────────────
  const applications = ref([])
  // 正在查看的历史申请 id（null 表示未在查看历史）
  const viewingAppId = ref(null)

  // ── 解析表单 ──────────────────────────────────────────────
  async function parseForm(text) {
    if (!text.trim() || parsing.value) return

    parsing.value = true
    parsedForm.value = null

    try {
      const data = await http.post('/erp/parse', {
        text,
        formType: formType.value,
      })
      parsedForm.value = data.form
      return data.form
    } catch (err) {
      appStore.toast.error('解析失败，请重新描述')
    } finally {
      parsing.value = false
    }
  }

  // ── 提交审批 ──────────────────────────────────────────────
  async function submitApproval(applicantName = '申请人') {
    if (!parsedForm.value || approving.value) return

    approving.value      = true
    approvalMessages.value = []
    approvalSteps.value    = []
    finalResult.value      = null

    await fetchStream(
      '/api/erp/submit/stream',
      {
        formData:      parsedForm.value,
        formType:      formType.value,
        applicantName,
      },
      {
        onEvent: (event, data) => {
          if (event === 'start') {
            currentAppId.value = data.appId
          }

          // 公布审批流程（需要哪些审批人）
          if (event === 'plan') {
            const approvers = data.approvers.map(role => ({
              roleId: role.id,
              role,
              status: 'pending',
            }))
            // 报销申请：前置一个"合规检查"确定性工具节点，与主管 Agent 并行执行
            if (formType.value === 'expense') {
              approvers.unshift({
                roleId: 'compliance_check',
                role: {
                  id: 'compliance_check',
                  name: '合规检查',
                  icon: '⚙️',
                  color: '#6b7280',
                  desc: '系统确定性合规校验（工具节点，不消耗 AI）',
                },
                status: 'pending',
                parallel: true,
              })
            }
            approvalSteps.value = approvers
          }

          // 某个审批人开始审核
          if (event === 'approver_start') {
            const step = approvalSteps.value.find(s => s.roleId === data.roleId)
            if (step) step.status = 'running'
            // 主管开始审核 → 合规检查工具并行执行（确定性工具即时返回）
            if (data.roleId === 'manager') {
              const tool = approvalSteps.value.find(s => s.roleId === 'compliance_check')
              if (tool) tool.status = 'running'
            }
          }

          // 对话消息（最核心的部分）
          if (event === 'message') {
            approvalMessages.value.push({
              id:       `msg_${Date.now()}_${Math.random()}`,
              from:     data.from,
              role:     data.role,
              content:  data.content,
              type:     data.type,
              time:     new Date().toISOString(),
            })
          }

          // 某个审批人完成
          if (event === 'approver_done') {
            const step = approvalSteps.value.find(s => s.roleId === data.roleId)
            if (step) step.status = data.approved ? 'approved' : 'rejected'
            // 主管完成 → 合规检查工具同步完成（确定性工具无等待）
            if (data.roleId === 'manager') {
              const tool = approvalSteps.value.find(s => s.roleId === 'compliance_check')
              if (tool) tool.status = 'approved'
            }
          }

          // 最终结果
          if (event === 'final') {
            finalResult.value = data
            approving.value   = false
            // 刷新申请列表
            loadApplications()
          }

          if (event === 'done') {
            approving.value = false
          }
        },
        onError: (err) => {
          approving.value = false
          appStore.toast.error(err.message || '审批流程出错')
        },
      }
    )
  }

  // ── 申请记录 ──────────────────────────────────────────────
  async function loadApplications() {
    try {
      const data = await http.get('/erp/applications')
      applications.value = data.applications
    } catch {}
  }

  // 打开历史申请：拉取详情，恢复当时的表单、审批步骤与对话消息
  async function openApplication(id) {
    try {
      const app = await http.get(`/erp/applications/${id}`)
      viewingAppId.value    = id
      formType.value        = app.formType
      parsedForm.value      = app.formData || null
      currentAppId.value    = app.id
      finalResult.value     = app.result || null
      const restoredSteps = (app.approvers || []).map(role => ({
        roleId: role.id,
        role,
        status: (app.result && app.result.approvedBy?.includes(role.name)) ? 'approved' : 'rejected',
      }))
      // 历史恢复：报销申请补上合规检查工具节点（最终通过则视为已通过）
      if (app.formType === 'expense') {
        restoredSteps.unshift({
          roleId: 'compliance_check',
          role: {
            id: 'compliance_check',
            name: '合规检查',
            icon: '⚙️',
            color: '#6b7280',
            desc: '系统确定性合规校验（工具节点）',
          },
          status: app.result ? 'approved' : 'pending',
          parallel: true,
        })
      }
      approvalSteps.value = restoredSteps
      approvalMessages.value = (app.messages || []).map((m, i) => ({
        id:      `his_${i}_${id}`,
        from:    m.from,
        role:    m.role,
        content: m.content,
        type:    m.type,
        time:    app.createdAt,
      }))
      approving.value = false
    } catch {
      appStore.toast.error('加载申请记录失败')
    }
  }

  // 重置（开始新申请）
  function reset() {
    parsedForm.value       = null
    approvalMessages.value = []
    approvalSteps.value    = []
    finalResult.value      = null
    currentAppId.value     = ''
    viewingAppId.value     = null
  }

  // 清空全部状态（退出登录时调用，防止切换账号后残留上一账号的申请记录）
  function clear() {
    reset()
    formType.value     = 'expense'
    parsing.value      = false
    approving.value    = false
    applications.value = []
  }

  return {
    formType, parsedForm, parsing,
    approvalMessages, approvalSteps, approving, finalResult, currentAppId,
    applications, viewingAppId,
    parseForm, submitApproval, loadApplications, openApplication, reset, clear,
  }
})
