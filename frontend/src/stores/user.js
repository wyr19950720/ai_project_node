// frontend/src/stores/user.js
// 用户登录态：token 存后端 HttpOnly Cookie（JS 不可读），store 只保存用户信息
import { defineStore } from 'pinia'
import { ref } from 'vue'
import http from '@/utils/http.js'

export const useUserStore = defineStore('user', () => {
  const userInfo = ref(null)

  function setAuth(data) {
    userInfo.value = data.user
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

  async function logout() {
    // 先同步清空前端登录态：即使请求未完成，路由守卫也能正确判断"未登录"，避免跳 /login 被弹回
    userInfo.value = null
    // 清除服务端 HttpOnly Cookie + 吊销 refresh token
    await http.post('/auth/logout').catch(() => {})
  }

  // 刷新页面后拉取用户信息；未登录 / Cookie 失效返回 null
  async function fetchMe() {
    try {
      const data = await http.get('/auth/me')
      userInfo.value = data.user
      return data.user
    } catch {
      userInfo.value = null
      return null
    }
  }

  return { userInfo, login, register, logout, fetchMe }
})
