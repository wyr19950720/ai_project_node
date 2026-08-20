// frontend/src/utils/http.js
// 统一封装 axios：请求拦截、响应拦截、错误处理、401 自动刷新 access token
import axios from 'axios'
import { useAppStore } from '@/stores/app.js'

// 创建 axios 实例
const http = axios.create({
  baseURL: '/api',           // 配合 vite proxy，开发时自动转发到 :3000
  timeout: 30000,            // 普通请求 30s 超时
  withCredentials: true,     // 跨域/代理下携带 HttpOnly Cookie
})

// ── 401 自动刷新（双 token） ───────────────────────────────────
// access(2h) 过期时，用 refresh(7d) 换新；refresh 存后端 HttpOnly Cookie，JS 不可读
// refreshPromise 做"单飞"：并发多个 401 只触发一次刷新，其余等待同一个 Promise
let refreshPromise = null

function tryRefresh() {
  if (!refreshPromise) {
    // 用原生 fetch 调刷新接口，避免再次进入 axios 拦截器形成递归
    refreshPromise = fetch('/api/auth/refresh', {
      method: 'POST',
      credentials: 'include',
    })
      .then((r) => {
        if (!r.ok) throw new Error('refresh-failed')
      })
      .finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

// 这些接口的 401 是业务语义（如密码错误），不该触发自动刷新
const NO_RETRY_PATHS = ['/auth/login', '/auth/register']

// ── 响应拦截器 ─────────────────────────────────────────────────
http.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const appStore = useAppStore()

    if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) {
      appStore.toast.error('请求超时，请稍后重试')
    } else if (error.response) {
      const status = error.response.status
      const msg = error.response.data?.error || '请求失败'
      const url = error.config?.url || ''

      // access token 过期：刷新后重试原请求一次（已标记 _retried 则不再重试）
      if (status === 401 && !error.config?._retried && !NO_RETRY_PATHS.some((p) => url.startsWith(p))) {
        error.config._retried = true
        try {
          await tryRefresh()
          return http(error.config)
        } catch {
          // refresh 也失效：清除登录态并跳登录页
          fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {})
          if (window.location.pathname !== '/login') {
            window.location.href = '/login'
          }
          appStore.toast.error('登录已过期，请重新登录')
          return Promise.reject(error)
        }
      } else if (status === 401) {
        // 登录/注册接口的 401（如密码错误）或刷新失败：直接提示
        appStore.toast.error(msg || '请先登录')
      } else if (status === 429) {
        appStore.toast.warning('请求太频繁，请稍后再试')
      } else if (status >= 500) {
        appStore.toast.error('服务器异常，请稍后重试')
      } else {
        appStore.toast.error(msg)
      }
    } else {
      appStore.toast.error('网络异常，请检查连接')
    }

    return Promise.reject(error)
  }
)

// ── SSE 流式请求工具 ───────────────────────────────────────────
// 浏览器原生 fetch + ReadableStream，不走 axios
// onToken：每收到一个 token 的回调
// onEvent：收到特定事件（sources、tool_start 等）的回调
// onDone：流结束时的回调
// onError：出错时的回调
export async function fetchStream(url, body, { onToken, onEvent, onDone, onError } = {}) {
  // 发起请求；401 时先刷新 access 再重试一次（retried 防止死循环）
  const run = async (retried = false) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    if (!response.ok) {
      if (response.status === 401 && !retried) {
        try {
          await tryRefresh()
          return await run(true)
        } catch {
          /* refresh 失败，走下方统一错误抛出 */
        }
      }
      const data = await response.json().catch(() => ({}))
      throw new Error(data.error || `HTTP ${response.status}`)
    }
    return response
  }

  try {
    // SSE 流通过 HttpOnly Cookie 认证（同源 /api 代理自动携带）
    const response = await run()

    const reader  = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer    = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // SSE 格式：event 和 data 之间用 \n\n 分隔
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''

      for (const part of parts) {
        if (!part.trim()) continue

        const lines = part.split('\n')
        let event = 'message'
        let dataStr = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) event = line.slice(7).trim()
          if (line.startsWith('data: '))  dataStr = line.slice(6)
        }

        if (!dataStr) continue

        let data
        try { data = JSON.parse(dataStr) } catch { continue }

        // 分发事件
        if (event === 'token' && onToken) {
          onToken(data.token || '')
        } else if (event === 'done' && onDone) {
          onDone(data)
        } else if (event === 'error') {
          onError?.(new Error(data.message || '流式请求出错'))
          return
        } else if (onEvent) {
          onEvent(event, data)
        }
      }
    }
  } catch (err) {
    onError?.(err)
  }
}

export default http
