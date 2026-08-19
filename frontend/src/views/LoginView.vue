<!-- frontend/src/views/LoginView.vue -->
<!-- 登录/注册页：全屏居中卡片，无侧边栏/顶栏 -->
<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <div class="logo-badge">
          <el-icon :size="26"><MagicStick /></el-icon>
        </div>
        <h1 class="login-title">WorkMind AI</h1>
        <p class="login-subtitle">智能办公助手 · 首次使用请先注册，登录后开始使用</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" size="large" @submit.prevent>
        <el-form-item v-if="isRegister" label="昵称（选填）" prop="nickname">
          <el-input v-model="form.nickname" placeholder="怎么称呼你？" maxlength="20" clearable />
        </el-form-item>

        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" autocomplete="username" clearable>
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" placeholder="至少 6 位" autocomplete="current-password" show-password>
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-form-item v-if="isRegister" label="确认密码" prop="confirm">
          <el-input v-model="form.confirm" type="password" placeholder="再次输入密码" autocomplete="new-password" show-password>
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-button type="primary" class="submit-btn" :loading="loading" @click="onSubmit">
          {{ isRegister ? '注 册' : '登 录' }}
        </el-button>
      </el-form>

      <div class="login-switch">
        {{ isRegister ? '已有账号？' : '还没有账号？' }}
        <span class="switch-link" @click="toggleMode">{{ isRegister ? '去登录' : '立即注册' }}</span>
      </div>
    </div>

    <p class="login-footer">WorkMind · 未登录无法使用系统，请先注册账号并登录</p>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app.js'
import { useUserStore } from '@/stores/user.js'

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()
const userStore = useUserStore()

const isRegister = ref(false)
const loading = ref(false)
const formRef = ref(null)

const form = reactive({
  username: '',
  password: '',
  nickname: '',
  confirm: '',
})

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 64, message: '用户名长度 2-64 位', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    {
      validator: (_, value, cb) => {
        if (!isRegister.value) return cb()
        if (!value) return cb(new Error('请再次输入密码'))
        if (value !== form.password) return cb(new Error('两次输入的密码不一致'))
        cb()
      },
      trigger: 'blur',
    },
  ],
}

function toggleMode() {
  isRegister.value = !isRegister.value
  form.confirm = ''
}

async function onSubmit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    if (isRegister.value) {
      await userStore.register(form.username.trim(), form.password, form.nickname.trim())
      appStore.toast.success('注册成功，已自动登录')
    } else {
      await userStore.login(form.username.trim(), form.password)
      appStore.toast.success('登录成功')
    }
    router.push(route.query.redirect || '/')
  } catch (err) {
    // 错误提示已由 http 拦截器统一弹出
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  min-height: 100vh;
  overflow-y: auto;   /* 注册模式表单变高时内部可滚动，避免内容被裁切 */
  display: flex;
  flex-direction: column;
  align-items: center;
  background: linear-gradient(135deg, var(--color-primary-bg) 0%, var(--color-bg) 100%);
  padding: var(--space-lg);
}

.login-card {
  width: 100%;
  max-width: 400px;
  margin: auto;   /* 配合 flex 实现居中；内容超高时 margin 自动归零，可滚动访问全部字段 */
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  padding: var(--space-2xl);
}

.login-logo {
  text-align: center;
  margin-bottom: var(--space-xl);
}

.logo-badge {
  width: 56px;
  height: 56px;
  margin: 0 auto var(--space-md);
  border-radius: var(--radius-lg);
  background: var(--color-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.login-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text);
  margin: 0 0 var(--space-xs);
}

.login-subtitle {
  font-size: 13px;
  color: var(--color-text-muted);
  margin: 0;
}

.submit-btn {
  width: 100%;
  margin-top: var(--space-sm);
}

.login-switch {
  margin-top: var(--space-lg);
  text-align: center;
  font-size: 13px;
  color: var(--color-text-sub);
}

.switch-link {
  color: var(--color-primary);
  cursor: pointer;
  font-weight: 500;
  margin-left: 4px;
}
.switch-link:hover { text-decoration: underline; }

.login-footer {
  margin-top: var(--space-lg);
  font-size: 12px;
  color: var(--color-text-muted);
}
</style>
