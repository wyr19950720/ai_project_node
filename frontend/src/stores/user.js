// frontend/src/stores/user.js
// 用户登录态：token（localStorage 持久化）+ 用户信息
import { defineStore } from 'pinia'
import { ref } from 'vue'
import http from '@/utils/http.js'

const TOKEN_KEY = 'workmind_token'

export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const userInfo = ref(null)

  function setAuth(data) {
    token.value = data.token
    userInfo.value = data.user
    localStorage.setItem(TOKEN_KEY, data.token)
  }

  async function login(username, password) {
    const data = await http.post('/auth/login', { username, password })
    setAuth(data)
    return data.user
  }

  async function register(username, password, nickname = '') {
    const data = await http.post('/auth/register', { username, password, nickname })
    setAuth(data)
    return data.user
  }

  function logout() {
    token.value = ''
    userInfo.value = null
    localStorage.removeItem(TOKEN_KEY)
  }

  // 刷新页面后拉取最新用户信息；token 失效返回 null
  async function fetchMe() {
    if (!token.value) return null
    try {
      const data = await http.get('/auth/me')
      userInfo.value = data.user
      return data.user
    } catch {
      return null
    }
  }

  return { token, userInfo, login, register, logout, fetchMe }
})
