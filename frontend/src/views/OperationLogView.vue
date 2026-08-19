<!-- frontend/src/views/OperationLogView.vue -->
<!-- 用户操作记录：汇总会话/消息/ERP 申请，按北京时间展示 -->
<template>
  <div class="op-view">
    <!-- 统计卡片 -->
    <div class="stats-grid">
      <div v-for="card in statCards" :key="card.label" class="stat-card" :class="'color-'+card.color">
        <div class="stat-value">{{ card.value }}</div>
        <div class="stat-label">{{ card.label }}</div>
      </div>
    </div>

    <!-- 过滤栏 -->
    <div class="filter-bar">
      <select v-model="typeFilter" class="input filter-select" @change="load(1)">
        <option value="all">全部类型</option>
        <option v-for="(label, key) in typeLabels" :key="key" :value="key">{{ label }}</option>
      </select>

      <select v-model="daysFilter" class="input filter-select" @change="load(1)">
        <option :value="0">全部时间</option>
        <option :value="7">近 7 天</option>
        <option :value="1">今天</option>
      </select>

      <span class="filter-total">共 {{ total }} 条记录</span>
      <button class="btn btn-ghost btn-sm refresh-btn" @click="load(currentPage)">刷新</button>
    </div>

    <!-- 记录表格 -->
    <div class="table-card">
      <div class="table-wrap">
        <table class="op-table">
          <thead>
            <tr>
              <th>时间（北京时间）</th>
              <th>用户</th>
              <th>动作</th>
              <th>内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!items.length">
              <td colspan="4" class="empty-row">暂无操作记录</td>
            </tr>
            <tr v-for="(item, i) in items" :key="i">
              <td class="time-cell">{{ item.time }}</td>
              <td class="user-cell">
                <span class="user-name">{{ item.nickname }}</span>
                <span class="user-account">@{{ item.user }}</span>
              </td>
              <td><span class="type-tag" :class="'tag-'+typeClass(item.type)">{{ typeLabels[item.type] || item.type }}</span></td>
              <td class="content-cell">
                <span class="content-text">{{ item.content }}</span>
                <span v-if="item.ref?.appId" class="ref-badge" :title="'申请号：' + item.ref.appId">
                  {{ item.ref.appId }}
                </span>
                <span v-else-if="item.ref?.sessionId" class="ref-badge" :title="'会话：' + (item.ref?.sessionTitle || item.ref?.sessionId)">
                  会话
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 分页 -->
    <div v-if="total > pageSize" class="pagination">
      <button class="btn btn-ghost btn-sm" :disabled="currentPage <= 1" @click="load(currentPage - 1)">上一页</button>
      <span class="page-info">{{ currentPage }} / {{ totalPages }}</span>
      <button class="btn btn-ghost btn-sm" :disabled="currentPage >= totalPages" @click="load(currentPage + 1)">下一页</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import http from '@/utils/http.js'

const items = ref([])
const total = ref(0)
const pageSize = ref(20)
const currentPage = ref(1)
const typeFilter = ref('all')
const daysFilter = ref(0)
const typeLabels = ref({})
const stats = ref({})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

const statCards = computed(() => {
  const s = stats.value
  return [
    { label: '全部操作', value: total.value, color: 'blue' },
    { label: '用户提问', value: s.chat_human || 0, color: 'green' },
    { label: 'AI 回复', value: s.chat_ai || 0, color: 'purple' },
    { label: '新建会话', value: s.session_create || 0, color: 'amber' },
    { label: 'ERP 申请', value: (s.erp_expense || 0) + (s.erp_leave || 0), color: 'red' },
  ]
})

function typeClass(type) {
  if (type === 'chat_human') return 'human'
  if (type === 'chat_ai') return 'ai'
  if (type === 'session_create') return 'session'
  if (type.startsWith('erp')) return 'erp'
  return 'other'
}

async function load(page = 1) {
  currentPage.value = page
  try {
    const d = await http.get('/operations/records', {
      params: { type: typeFilter.value, days: daysFilter.value, page, pageSize: pageSize.value },
    })
    items.value = d.items || []
    total.value = d.total || 0
    stats.value = d.stats || {}
    typeLabels.value = d.typeLabels || {}
  } catch {
    /* http.js 已统一提示错误 */
  }
}

onMounted(() => load(1))
</script>

<style scoped>
.op-view {
  height: 100%;
  overflow-y: auto;
  padding: var(--space-lg) var(--space-xl);
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
  background: var(--color-bg);
}

/* 统计卡片 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--space-md);
}
.stat-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-md) var(--space-lg);
}
.color-blue   { border-top: 3px solid var(--color-info); }
.color-green  { border-top: 3px solid var(--color-success); }
.color-purple { border-top: 3px solid var(--color-primary); }
.color-amber  { border-top: 3px solid var(--color-warning); }
.color-red    { border-top: 3px solid var(--color-danger); }
.stat-value { font-size: 24px; font-weight: 800; color: var(--color-text); line-height: 1.2; }
.stat-label { font-size: 11px; font-weight: 600; color: var(--color-text-muted); margin-top: 4px; }

/* 过滤栏 */
.filter-bar {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-sm) var(--space-lg);
}
.filter-select {
  padding: 5px 10px;
  font-size: 12px;
  width: auto;
}
.filter-total {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-text-muted);
}
.refresh-btn { flex-shrink: 0; }

/* 表格 */
.table-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.table-wrap { overflow-x: auto; }
.op-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.op-table th {
  padding: 8px 16px;
  background: var(--color-bg);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--color-text-muted);
  text-align: left;
  border-bottom: 1px solid var(--color-border);
}
.op-table td {
  padding: 8px 16px;
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text-sub);
  vertical-align: middle;
}
.op-table tr:hover td { background: var(--color-border-light); }
.empty-row { text-align: center; color: var(--color-text-muted); padding: 24px !important; }

.time-cell { font-family: var(--font-mono); color: var(--color-text-muted); white-space: nowrap; }

.user-cell { white-space: nowrap; }
.user-name { color: var(--color-text); font-weight: 600; }
.user-account { font-size: 11px; color: var(--color-text-muted); margin-left: 4px; }

.type-tag {
  display: inline-block;
  font-size: 10px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  white-space: nowrap;
}
.tag-human   { background: rgba(16,185,129,.12); color: var(--color-success); }
.tag-ai      { background: var(--color-primary-bg); color: var(--color-primary); }
.tag-session { background: rgba(245,158,11,.14); color: var(--color-warning); }
.tag-erp     { background: rgba(239,68,68,.12); color: var(--color-danger); }
.tag-other   { background: var(--color-border-light); color: var(--color-text-muted); }

.content-cell { max-width: 520px; }
.content-text {
  color: var(--color-text);
  word-break: break-all;
}
.ref-badge {
  display: inline-block;
  margin-left: 8px;
  font-size: 10px;
  padding: 1px 7px;
  background: var(--color-border-light);
  color: var(--color-text-muted);
  border-radius: var(--radius-full);
  white-space: nowrap;
}

/* 分页 */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-md);
}
.page-info { font-size: 12px; color: var(--color-text-muted); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn-sm { padding: 5px 12px; font-size: 12px; }
</style>
