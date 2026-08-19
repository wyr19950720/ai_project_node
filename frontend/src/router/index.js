// frontend/src/router/index.js
// 路由配置：每个模块对应一个一级路由
import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user.js'

const routes = [
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '智能对话', icon: '💬' },
  },
  {
    path: '/knowledge',
    name: 'Knowledge',
    component: () => import('@/views/KnowledgeView.vue'),
    meta: { title: '知识库', icon: '📚' },
  },
  {
    path: '/agent',
    name: 'Agent',
    component: () => import('@/views/AgentView.vue'),
    meta: { title: '任务 Agent', icon: '🤖' },
  },
  {
    path: '/workflow',
    name: 'Workflow',
    component: () => import('@/views/WorkflowView.vue'),
    meta: { title: '内容工作流', icon: '⚙️' },
  },
  {
    path: '/erp',
    name: 'ERP',
    component: () => import('@/views/ErpView.vue'),
    meta: { title: '报销请假', icon: '📋' },
  },
  {
    path: '/prompt',
    name: 'Prompt',
    component: () => import('@/views/PromptView.vue'),
    meta: { title: 'Prompt 调试', icon: '🔧' },
  },
  {
    path: '/monitor',
    name: 'Monitor',
    component: () => import('@/views/MonitorView.vue'),
    meta: { title: '用量看板', icon: '📊' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// ── 全局守卫：未登录跳登录页，已登录访问 /login 跳首页 ─────────
router.beforeEach((to) => {
  const userStore = useUserStore()
  const isLoggedIn = !!userStore.token

  if (!to.meta.public && !isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.path === '/login' && isLoggedIn) {
    return { path: '/' }
  }
})

// 路由切换时更新页面 title
router.afterEach((to) => {
  document.title = `${to.meta.title || 'WorkMind'} — WorkMind AI`
})

export default router
