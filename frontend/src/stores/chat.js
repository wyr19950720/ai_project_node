// frontend/src/stores/chat.js
// 对话模块全局状态：会话列表、当前会话消息、角色、用户画像
import { defineStore } from 'pinia'
import { ref, computed, reactive } from 'vue'
import { fetchStream } from '@/utils/http.js'
import http from '@/utils/http.js'
import { useAppStore } from './app.js'
import { useMonitorStore } from './monitor.js'
import { useUserStore } from './user.js'

export const useChatStore = defineStore('chat', () => {
  const appStore     = useAppStore()
  const monitorStore = useMonitorStore()
  const userStore    = useUserStore()

  // ── 会话列表 ──────────────────────────────────────────────────
  // 每个会话：{ id, title, messages: [], createdAt }
  const sessions    = ref([])
  const currentId   = ref(null)

  const currentSession = computed(() =>
    sessions.value.find(s => s.id === currentId.value) || null
  )

  const messages = computed(() =>
    currentSession.value?.messages || []
  )

  // ── 初始化：加载历史会话，无则新建 ───────────────────────────
  async function init() {
    await loadSessions()
    if (sessions.value.length === 0) {
      newSession()
    }
  }

  // 从服务端加载当前用户的历史会话列表（不加载消息，按需加载）
  async function loadSessions() {
    try {
      const data = await http.get('/chat/sessions')
      sessions.value = (data.sessions || []).map(s => ({
        id:           s.id,
        title:        s.title,
        messageCount: s.messageCount || 0,
        messages:     [],
        loaded:       false,   // 消息是否已从服务端拉取
        remote:       true,    // 是否服务端会话（决定切换时是否加载消息）
        createdAt:    '',
      }))
      if (sessions.value.length > 0) {
        currentId.value = sessions.value[0].id
        await loadMessages(currentId.value)
      }
    } catch {}
  }

  // 拉取某会话的全部消息
  async function loadMessages(sessionId) {
    const s = sessions.value.find(x => x.id === sessionId)
    if (!s) return
    try {
      const data = await http.get(`/chat/sessions/${sessionId}/messages`)
      s.messages     = data.messages || []
      s.messageCount = s.messages.length
      s.loaded       = true
    } catch {}
  }

  function newSession() {
    const id = `session_${Date.now()}`
    sessions.value.unshift({
      id,
      title: '新对话',
      messages: [],
      messageCount: 0,
      loaded: false,
      remote: false,     // 本地新会话，服务端还没有，切换时无需加载
      createdAt: new Date().toISOString(),
    })
    currentId.value = id
    return id
  }

  function switchSession(id) {
    if (currentId.value === id) return
    currentId.value = id
    // 服务端会话且消息未加载过 → 拉取历史消息
    const s = sessions.value.find(x => x.id === id)
    if (s && s.remote && !s.loaded) {
      loadMessages(id)
    }
  }

  function deleteSession(id) {
    const idx = sessions.value.findIndex(s => s.id === id)
    if (idx === -1) return
    sessions.value.splice(idx, 1)

    // 如果删的是当前会话，切到第一个并加载其消息
    if (currentId.value === id) {
      currentId.value = sessions.value[0]?.id || null
      if (!currentId.value) {
        newSession()
      } else {
        const first = sessions.value[0]
        if (first.remote && !first.loaded) loadMessages(first.id)
      }
    }

    // 同步删除服务端会话历史
    http.delete(`/chat/sessions/${id}`).catch(() => {})
  }

  // 根据第一条消息自动生成会话标题
  function updateTitle(sessionId, firstMessage) {
    const s = sessions.value.find(s => s.id === sessionId)
    if (s && s.title === '新对话') {
      s.title = firstMessage.slice(0, 20) + (firstMessage.length > 20 ? '...' : '')
    }
  }

  // ── 角色 ──────────────────────────────────────────────────────
  const selectedRole = ref('default')
  const roles = ref([])

  async function loadRoles() {
    try {
      const data = await http.get('/chat/roles')
      roles.value = data.roles
    } catch {}
  }

  // ── 用户画像 ──────────────────────────────────────────────────
  const profile = ref({})
  // 用户 id 来自登录态（后端以 JWT 为准，会话/画像都归属当前用户）
  const userId = computed(() => userStore.userInfo?.id || '')

  async function loadProfile() {
    try {
      const data = await http.get('/chat/profile')
      profile.value = data
    } catch {}
  }

  // ── 发送消息（核心）──────────────────────────────────────────
  const loading = ref(false)

  async function sendMessage(text) {
    if (!text.trim() || loading.value) return
    if (!currentId.value) newSession()

    const session = currentSession.value
    loading.value = true

    // 添加用户消息
    const userMsg = {
      id:      `msg_${Date.now()}`,
      role:    'user',
      content: text,
      time:    new Date().toISOString(),
    }
    session.messages.push(userMsg)
    session.messageCount = session.messages.length
    updateTitle(currentId.value, text)

    // 添加 AI 消息占位（流式填充）
    // 必须用 reactive() 包裹，使本地引用也是响应式代理
    // 否则 push 后 Vue 给数组元素套的 Proxy 与本地变量是两个对象，onToken 里的赋值不触发更新
    const aiMsg = reactive({
      id:         `msg_${Date.now() + 1}`,
      role:       'assistant',
      content:    '',
      fromCache:  false,
      streaming:  true,
      time:       new Date().toISOString(),
    })
    session.messages.push(aiMsg)

    await fetchStream(
      '/api/chat/stream',
      {
        message:   text,
        sessionId: currentId.value,
        role:      selectedRole.value,
        userId:    userId.value,
      },
      {
        onToken: (token) => {
          aiMsg.content += token
        },
        onEvent: (event, data) => {
          if (event === 'cache_hit') aiMsg.fromCache = true
          if (event === 'start')     aiMsg.streaming = true
        },
        onDone: (data) => {
          aiMsg.streaming = false
          // 记录用量
          if (!data.fromCache) {
            monitorStore.recordCall({
              inputTokens:  data.inputTokens || 0,
              outputTokens: data.outputTokens || 0,
              fromCache:    false,
              feature:      'chat',
            })
          } else {
            monitorStore.recordCall({ fromCache: true, feature: 'chat' })
          }
          // 刷新画像（后台可能更新了）
          loadProfile()
        },
        onError: (err) => {
          aiMsg.streaming = false
          aiMsg.content   = aiMsg.content || '抱歉，出现了一些问题，请重试。'
          appStore.toast.error(err.message || '发送失败')
        },
      }
    )

    loading.value = false
  }

  // 重新生成最后一条 AI 回复
  async function regenerate() {
    const msgs = currentSession.value?.messages || []
    // 找最后一条用户消息
    const lastUser = [...msgs].reverse().find(m => m.role === 'user')
    if (!lastUser) return

    // 移除最后一条 AI 消息
    const lastAiIdx = msgs.length - 1
    if (msgs[lastAiIdx]?.role === 'assistant') {
      msgs.splice(lastAiIdx, 1)
    }

    await sendMessage(lastUser.content)
  }

  // 复制消息内容
  async function copyMessage(content) {
    await navigator.clipboard.writeText(content)
    appStore.toast.success('已复制到剪贴板')
  }

  // 清空全部状态（退出登录时调用，防止切换账号后残留上一账号的数据）
  function clear() {
    sessions.value     = []
    currentId.value    = null
    selectedRole.value = 'default'
    roles.value        = []
    profile.value      = {}
    loading.value      = false
  }

  return {
    sessions, currentId, currentSession, messages,
    selectedRole, roles,
    profile, userId,
    loading,
    init, newSession, switchSession, deleteSession,
    loadRoles, loadProfile,
    sendMessage, regenerate, copyMessage, clear,
  }
})
