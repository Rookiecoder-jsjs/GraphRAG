<template>
  <div class="eval-page">
    <PageHeader
      :icon="FlaskIcon"
      kicker="协作 · 评测"
      title="评测用例"
      subtitle="把真实反馈沉淀为 RAG 回归门禁的 gold 用例"
    >
      <template #actions>
        <Button
          variant="primary"
          size="sm"
          :icon="PlusIcon"
          icon-position="left"
          @click="showCreate = !showCreate"
        >
          新建用例
        </Button>
      </template>
    </PageHeader>

    <div class="eval-content">
      <LoadingState v-if="loading" message="加载评测用例…" />

      <ErrorState
        v-else-if="loadError"
        title="加载评测用例失败"
        description="获取评测用例列表时出了点问题。"
        @retry="load"
      />

      <template v-else>
        <!-- FEAT-018: 手动新建（对话里的 👎 转换在 ChatPage 完成） -->
        <form v-if="showCreate" class="create-form" @submit.prevent="submitCreate">
          <input
            v-model="createForm.query"
            class="create-input"
            type="text"
            placeholder="评测问题（必填），例如：什么是知识图谱？"
            :disabled="creating"
          />
          <input
            v-model="createForm.keywords"
            class="create-input"
            type="text"
            placeholder="期望答案关键词，逗号分隔（可选）"
            :disabled="creating"
          />
          <div class="create-actions">
            <Button variant="primary" size="sm" type="submit" :loading="creating">
              保存用例
            </Button>
            <Button variant="ghost" size="sm" @click="resetCreate">取消</Button>
          </div>
        </form>

        <section class="eval-stats">
          <Stat
            variant="icon"
            tone="primary"
            :icon="FlaskIcon"
            :value="cases.length"
            label="总用例数"
          />
          <Stat
            variant="icon"
            tone="warm"
            :icon="MessageSquareIcon"
            :value="feedbackCount"
            label="来自反馈"
          />
          <Stat
            variant="icon"
            tone="cool"
            :icon="CheckIcon"
            :value="enabledCount"
            label="已启用"
          />
        </section>

        <div class="toolbar">
          <input
            v-model="keyword"
            type="text"
            class="search-input"
            placeholder="按问题内容过滤…"
            aria-label="过滤评测用例"
          />
        </div>

        <EmptyState
          v-if="cases.length === 0"
          :icon="FlaskIcon"
          title="暂无评测用例"
          description="在对话中对不满意的回答点 👎 并「存为评测用例」，或在此手动新建。"
        />
        <EmptyState
          v-else-if="filtered.length === 0"
          :icon="SearchIcon"
          title="没有匹配的用例"
          description="换个关键词试试。"
        />

        <ul v-else class="case-list">
          <li v-for="c in filtered" :key="c.id" class="case-row">
            <div class="row-main">
              <span class="row-query">{{ c.query }}</span>
              <span class="row-meta">
                <Tag shape="pill" :tone="isFeedback(c) ? 'warning' : 'muted'">
                  {{ isFeedback(c) ? '来自反馈' : '手动' }}
                </Tag>
                <Tag shape="pill" tone="muted">
                  {{ (c.expected_chunk_ids || []).length }} 个来源块
                </Tag>
                <Tag
                  shape="pill"
                  clickable
                  :tone="c.enabled ? 'success' : 'muted'"
                  :title="c.enabled ? '点击停用（停用后评测不运行该用例）' : '点击启用'"
                  @click="toggleCase(c)"
                >
                  {{ c.enabled ? '已启用' : '已停用' }}
                </Tag>
                <span class="row-time">{{ formatDate(c.created_at) }}</span>
              </span>
            </div>
            <div class="row-actions">
              <Button
                variant="ghost"
                size="sm"
                :icon="TrashIcon"
                icon-position="only"
                @click="onDelete(c)"
                title="删除用例"
              />
            </div>
          </li>
        </ul>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import { evalApi } from '../api/eval'
import { PageHeader, Button, Tag, Stat, EmptyState, LoadingState, ErrorState } from '../components/ui'
import {
  FlaskIcon, PlusIcon, TrashIcon, SearchIcon,
  MessageSquareIcon, CheckIcon
} from '../components/ui/icons'
import { useConfirm } from '../composables/confirm'
import { useToast } from '../composables/toast'

const { confirm } = useConfirm()
const { success, error: toastError } = useToast()

const cases = ref([])
const loading = ref(true)
const loadError = ref(false)
const keyword = ref('')

const showCreate = ref(false)
const creating = ref(false)
const createForm = ref({ query: '', keywords: '' })

const load = async () => {
  loading.value = true
  loadError.value = false
  try {
    const { data } = await evalApi.listCases()
    cases.value = data || []
  } catch (error) {
    console.error('Failed to load eval cases:', error)
    loadError.value = true
  } finally {
    loading.value = false
  }
}
// Layout caches pages in <keep-alive>: returning here fires onActivated
// without re-running onMounted — reload, or newly converted cases (created
// from ChatPage) go stale. (Same pattern as ConversationHistoryPage.)
onMounted(load)
onActivated(load)

const isFeedback = (c) => (c.tags || []).includes('user-feedback')
const feedbackCount = computed(() => cases.value.filter(isFeedback).length)
const enabledCount = computed(() => cases.value.filter((c) => c.enabled).length)

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return cases.value
  return cases.value.filter((c) => (c.query || '').toLowerCase().includes(kw))
})

const resetCreate = () => {
  createForm.value = { query: '', keywords: '' }
  showCreate.value = false
}

const submitCreate = async () => {
  const query = createForm.value.query.trim()
  if (!query) {
    toastError('请填写评测问题')
    return
  }
  creating.value = true
  try {
    const keywords = createForm.value.keywords
      .split(/[,，]/)
      .map((k) => k.trim())
      .filter(Boolean)
    await evalApi.createCase({
      query,
      expected_keywords: keywords,
      tags: ['manual']
    })
    success('评测用例已创建')
    resetCreate()
    await load()
  } catch (error) {
    console.error('Failed to create eval case:', error)
    toastError(error?.response?.data?.detail || '创建评测用例失败，请重试。')
  } finally {
    creating.value = false
  }
}

const toggleCase = async (c) => {
  try {
    const { data } = await evalApi.updateCase(c.id, { enabled: !c.enabled })
    const idx = cases.value.findIndex((x) => x.id === c.id)
    if (idx !== -1) cases.value[idx] = { ...cases.value[idx], ...data }
  } catch (error) {
    console.error('Failed to toggle eval case:', error)
    toastError(error?.response?.data?.detail || '更新评测用例失败，请重试。')
  }
}

const onDelete = async (c) => {
  const ok = await confirm({
    title: '删除评测用例？',
    message: `“${c.query}” 将从评测集中移除，之后的回归门禁不再运行它。`,
    confirmLabel: '删除',
    danger: true
  })
  if (!ok) return
  try {
    await evalApi.deleteCase(c.id)
    cases.value = cases.value.filter((x) => x.id !== c.id)
    success('评测用例已删除')
  } catch (error) {
    console.error('Failed to delete eval case:', error)
    toastError(error?.response?.data?.detail || '删除评测用例失败，请重试。')
  }
}

const formatDate = (dateStr) => {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toLocaleDateString('zh-CN', {
      month: 'short',
      day: 'numeric'
    })
  } catch {
    return dateStr
  }
}
</script>

<style scoped>
.eval-content { display: flex; flex-direction: column; gap: 1.25rem; }
.create-form {
  display: flex; flex-direction: column; gap: 0.5rem;
  padding: 0.875rem; border: 1px dashed var(--border);
  border-radius: 10px; background: var(--surface);
}
.create-input {
  padding: 0.5rem 0.65rem; border: 1px solid var(--border);
  border-radius: 8px; background: var(--bg); color: var(--text-primary);
  font-size: 0.875rem;
}
.create-input:focus { outline: none; border-color: var(--primary); }
.create-actions { display: flex; gap: 0.5rem; }
.eval-stats {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
}
.toolbar { display: flex; align-items: center; gap: 0.75rem; }
.search-input {
  flex: 1; max-width: 22rem; padding: 0.5rem 0.75rem;
  border: 1px solid var(--border); border-radius: 999px;
  background: var(--surface); color: var(--text-primary); font-size: 0.875rem;
}
.search-input:focus { outline: none; border-color: var(--primary); }
.case-list {
  list-style: none; margin: 0; padding: 0;
  display: flex; flex-direction: column; gap: 0.5rem;
}
.case-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 0.75rem; padding: 0.75rem 0.875rem;
  border: 1px solid var(--border); border-radius: 10px;
  background: var(--surface);
}
.row-main {
  display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; flex: 1;
}
.row-query {
  font-family: var(--font-display); font-size: 0.9375rem;
  font-weight: 500; color: var(--text-primary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.row-meta { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.row-time { font-size: 0.75rem; color: var(--text-tertiary); }
.row-actions { flex-shrink: 0; }
@media (max-width: 640px) {
  .eval-stats { grid-template-columns: 1fr; }
  .case-row { flex-direction: column; align-items: stretch; }
}
</style>
