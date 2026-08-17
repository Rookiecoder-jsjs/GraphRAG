<template>
  <div class="history-page">
    <PageHeader
      :icon="ClockIcon"
      kicker="协作 · 会话管理"
      title="历史会话"
      subtitle="浏览、搜索与管理你的全部对话记录"
    >
      <template #actions>
        <Button
          variant="primary"
          size="sm"
          :icon="PlusIcon"
          icon-position="left"
          @click="goNewChat"
        >
          新建对话
        </Button>
      </template>
    </PageHeader>

    <div class="history-content">
      <LoadingState v-if="loading" message="加载会话历史…" />

      <ErrorState
        v-else-if="loadError"
        title="加载会话历史失败"
        description="获取对话记录时出了点问题。"
        @retry="load"
      />

      <EmptyState
        v-else-if="conversations.length === 0"
        :icon="MessageSquareIcon"
        title="暂无历史会话"
        description="开始一段对话后，它会出现在这里。"
        action-label="开始对话"
        @action="goNewChat"
      />

      <template v-else>
        <!-- 概览统计 -->
        <section class="history-stats">
          <Stat
            variant="icon"
            tone="primary"
            :icon="MessageSquareIcon"
            :value="conversations.length"
            label="总会话数"
          />
          <Stat
            variant="icon"
            tone="cool"
            :icon="ClockIcon"
            :value="totalMessages"
            label="总消息数"
          />
          <Stat
            variant="icon"
            tone="warm"
            :icon="PlusIcon"
            :value="activeLast7d"
            label="最近 7 天活跃"
          />
        </section>

        <!-- 搜索 -->
        <div class="toolbar">
          <div class="search-box">
            <SearchIcon class="search-icon" />
            <input
              v-model="keyword"
              type="text"
              class="search-input"
              placeholder="搜索会话标题或内容…"
              aria-label="搜索会话"
            />
          </div>
          <span v-if="keyword" class="filter-hint">
            匹配 {{ filtered.length }} / {{ conversations.length }} 个会话
          </span>
        </div>

        <EmptyState
          v-if="filtered.length === 0"
          :icon="SearchIcon"
          title="没有匹配的会话"
          description="换个关键词试试。"
        />

        <ul v-else class="conversation-list">
          <li
            v-for="conv in filtered"
            :key="conv.id"
            class="conversation-row"
          >
            <button
              type="button"
              class="row-main"
              @click="openConversation(conv.id)"
              :title="`继续对话：${conv.title || '未命名会话'}`"
            >
              <span class="row-title">
                {{ conv.title || '未命名会话' }}
              </span>
              <span class="row-preview">{{ previewOf(conv) }}</span>
              <span class="row-meta">
                <Tag shape="pill" tone="muted">{{ conv.message_count }} 条消息</Tag>
                <span class="row-time">{{ timeAgo(conv.last_activity || conv.created_at) }}</span>
              </span>
            </button>
            <div class="row-actions">
              <Button
                variant="ghost"
                size="sm"
                :icon="MessageSquareIcon"
                icon-position="left"
                @click="openConversation(conv.id)"
              >
                继续
              </Button>
              <Button
                variant="ghost"
                size="sm"
                :icon="TrashIcon"
                icon-position="left"
                class="row-delete"
                @click="onDelete(conv)"
              >
                删除
              </Button>
            </div>
          </li>
        </ul>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, h } from 'vue'
import { useRouter } from 'vue-router'
import { chatApi } from '../api/chat'
import { PageHeader, Button, Tag, Stat, EmptyState, LoadingState, ErrorState } from '../components/ui'
import { ClockIcon, MessageSquareIcon, PlusIcon, TrashIcon, SearchIcon } from '../components/ui/icons'
import { useConfirm } from '../composables/confirm'
import { useToast } from '../composables/toast'

const router = useRouter()
const { confirm } = useConfirm()
const { success, error: toastError } = useToast()

const conversations = ref([])
const loading = ref(true)
const loadError = ref(false)
const keyword = ref('')

const load = async () => {
  loading.value = true
  loadError.value = false
  try {
    const { data } = await chatApi.getConversations()
    conversations.value = data || []
  } catch (error) {
    console.error('Failed to load conversations:', error)
    loadError.value = true
  } finally {
    loading.value = false
  }
}
onMounted(load)

const totalMessages = computed(() =>
  conversations.value.reduce((sum, c) => sum + (c.message_count || 0), 0)
)

const activeLast7d = computed(() => {
  const cutoff = Date.now() - 7 * 24 * 3600 * 1000
  return conversations.value.filter((c) => {
    const t = new Date(c.last_activity || c.created_at).getTime()
    return !isNaN(t) && t >= cutoff
  }).length
})

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return conversations.value
  return conversations.value.filter((c) =>
    (c.title || '').toLowerCase().includes(kw) ||
    (c.last_message || '').toLowerCase().includes(kw)
  )
})

const openConversation = (id) => {
  router.push({ path: '/chat', query: { conversation: id } })
}

const goNewChat = () => {
  router.push('/chat')
}

const onDelete = async (conv) => {
  const title = conv.title || '未命名会话'
  const ok = await confirm({
    title: '删除会话',
    message: `确定删除「${title}」吗？该对话的全部消息将被永久删除，无法恢复。`,
    confirmLabel: '删除',
    cancelLabel: '取消',
    danger: true,
  })
  if (!ok) return
  try {
    await chatApi.deleteConversation(conv.id)
    conversations.value = conversations.value.filter((c) => c.id !== conv.id)
    success('会话已删除')
  } catch (error) {
    console.error('Failed to delete conversation:', error)
    toastError('删除失败，请重试')
  }
}

const previewOf = (conv) => {
  const text = (conv.last_message || '').trim()
  if (!text) return '暂无消息'
  return text.length > 90 ? text.slice(0, 90) + '…' : text
}

const timeAgo = (input) => {
  if (!input) return ''
  const d = new Date(input)
  if (isNaN(d.getTime())) return ''
  const diff = Date.now() - d.getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} 天前`
  return `${Math.floor(day / 30)} 个月前`
}
</script>

<style scoped>
.history-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.history-content {
  flex: 1;
  padding: 1.75rem 2rem;
  overflow-y: auto;
  max-width: 960px;
  margin: 0 auto;
  width: 100%;
}

.history-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.search-box {
  position: relative;
  flex: 1;
  max-width: 420px;
}

.search-icon {
  position: absolute;
  left: 0.75rem;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  color: var(--text-tertiary);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 0.5rem 0.75rem 0.5rem 2.25rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 0.875rem;
  outline: none;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.search-input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-light);
}

.filter-hint {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.conversation-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.conversation-row {
  display: flex;
  align-items: stretch;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.conversation-row:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}

.row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.875rem 1rem;
  text-align: left;
  background: transparent;
  border: none;
  cursor: pointer;
  font-family: inherit;
}

.row-title {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-preview {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-meta {
  display: flex;
  align-items: center;
  gap: 0.625rem;
}

.row-time {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0 0.75rem;
  flex-shrink: 0;
}

.row-delete:hover {
  color: var(--error) !important;
}
</style>
