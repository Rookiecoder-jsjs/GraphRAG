<template>
  <div class="graph-global-page">
    <PageHeader
      :icon="Share2Icon"
      kicker="分析 · 图谱"
      title="全局问答"
      subtitle="知识图谱按主题聚类并生成摘要——先看全局，再就主题提问"
    />

    <div class="global-content">
      <LoadingState v-if="loading" message="加载社区…" />

      <ErrorState
        v-else-if="loadError"
        title="加载失败"
        description="读取主题社区时出了点问题。"
        @retry="load"
      />

      <template v-else>
        <div class="global-toolbar">
          <div class="global-summary">
            <Tag shape="pill" tone="cool">{{ communities.length }} 个主题社区</Tag>
            <span v-if="lastBuiltAt" class="global-summary-text">
              上次构建 {{ formatTime(lastBuiltAt) }}
            </span>
            <Tag v-if="stale" shape="pill" tone="warm">图谱有更新，建议重建</Tag>
          </div>
          <Button
            variant="primary"
            size="sm"
            :loading="rebuilding"
            @click="rebuild"
          >
            {{ communities.length ? '重建社区' : '构建社区' }}
          </Button>
        </div>

        <EmptyState
          v-if="!communities.length"
          :icon="Share2Icon"
          title="还没有主题社区"
          description="基于图谱中的实体关系聚类主题并生成摘要（每个主题一次 LLM 调用）。上传文档并构建图谱后，点击「构建社区」。"
        />

        <Card
          v-for="g in communities"
          :key="g.id"
          :title="g.title"
          :meta="`${g.member_count} 实体 · ${g.mention_total} 提及`"
        >
          <p class="community-summary">{{ g.summary }}</p>
          <div class="community-members">
            <router-link
              v-for="name in g.members"
              :key="name"
              :to="`/entities/${encodeURIComponent(name)}`"
              class="member-chip"
            >
              {{ name }}
            </router-link>
          </div>
          <div class="community-actions">
            <Button
              variant="secondary"
              size="sm"
              @click="askAbout(g)"
            >
              就此主题提问
            </Button>
          </div>
        </Card>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onActivated, h } from 'vue'
import { useRouter } from 'vue-router'
import { graphApi } from '../api/graph'
import { PageHeader, Card, Tag, Button, EmptyState, LoadingState, ErrorState } from '../components/ui'
import { useToast } from '../composables/toast'

// FEAT-028: 全局问答页。社区由后端离线算出（networkx 聚类 + LLM 摘要），
// 本页只读展示 + 触发重建 + 跳转提问（问题预填到 ChatPage，图谱开关
// 自动打开——社区通道在 use_graph_rag 下才激活）。keep-alive 双钩子。

const Share2Icon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 18, cy: 5, r: 3 }),
    h('circle', { cx: 6, cy: 12, r: 3 }),
    h('circle', { cx: 18, cy: 19, r: 3 }),
    h('line', { x1: 8.59, y1: 13.51, x2: 15.42, y2: 17.49 }),
    h('line', { x1: 15.41, y1: 6.51, x2: 8.59, y2: 10.49 })
  ])
}

const router = useRouter()
const { toast } = useToast()

const communities = ref([])
const stale = ref(false)
const loading = ref(true)
const loadError = ref(false)
const rebuilding = ref(false)

const load = async () => {
  loading.value = true
  loadError.value = false
  try {
    const { data } = await graphApi.getCommunities()
    communities.value = data?.communities || []
    stale.value = Boolean(data?.stale)
  } catch (error) {
    console.error('Failed to load communities:', error)
    loadError.value = true
    communities.value = []
  } finally {
    loading.value = false
  }
}

// Layout caches pages in <keep-alive>: returning here fires onActivated
// without re-running onMounted — reload so a background rebuild shows up.
onMounted(load)
onActivated(load)

const lastBuiltAt = computed(() => {
  const times = communities.value
    .map((g) => g.created_at && new Date(g.created_at).getTime())
    .filter((t) => t && !isNaN(t))
  return times.length ? Math.max(...times) : null
})

const rebuild = async () => {
  if (rebuilding.value) return
  rebuilding.value = true
  try {
    const { data } = await graphApi.rebuildCommunities()
    const n = data?.communities ?? 0
    if (n) {
      toast.success(`已构建 ${n} 个主题社区`)
    } else {
      toast.warning('图谱中还聚不出社区（需要 ≥3 个相互关联的实体），先多上传一些文档吧。')
    }
    await load()
  } catch (error) {
    console.error('Failed to rebuild communities:', error)
    toast.error(error?.response?.data?.detail || '重建失败，请稍后重试。')
  } finally {
    rebuilding.value = false
  }
}

const askAbout = (g) => {
  router.push({
    path: '/chat',
    query: { q: `请结合知识库，介绍一下「${g.title}」这个主题的主要内容`, graph: '1' },
  })
}

const formatTime = (ts) => {
  const d = new Date(ts)
  return d.toLocaleString([], {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}
</script>

<style scoped>
/* 同 EntityDuplicatesPage：布局壳不滚动，页面自带滚动容器。 */
.graph-global-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}
.global-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 0 2rem 2rem;
}

.global-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.global-summary {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  flex-wrap: wrap;
}
.global-summary-text {
  font-size: 0.8125rem;
  color: var(--text-tertiary);
}

.community-summary {
  font-size: 0.9375rem;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 0 0.875rem;
}

.community-members {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin-bottom: 0.875rem;
}
.member-chip {
  font-size: 0.75rem;
  font-family: var(--font-display);
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.1875rem 0.625rem;
  text-decoration: none;
  transition: border-color var(--transition-fast), color var(--transition-fast);
}
.member-chip:hover {
  border-color: var(--primary-light);
  color: var(--primary);
}

.community-actions {
  display: flex;
  justify-content: flex-end;
}
</style>
