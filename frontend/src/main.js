// frontend/src/main.js
// 应用入口：注册 Vue 插件，挂载应用
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router/index.js'
import './styles/global.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

const app = createApp(App)

// 全局注册 Element Plus 组件库（el-form / el-input / el-button / el-dropdown 等）
app.use(ElementPlus)

// 全局注册 Element Plus 图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// Pinia：全局状态管理
app.use(createPinia())

// Vue Router：页面路由
app.use(router)

app.mount('#app')
