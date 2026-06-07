<template>
  <div class="dashboard-page">
    <PageHeader
      :icon="LayoutIcon"
      kicker="Dashboard · Overview"
      title="Dashboard"
      subtitle="Your knowledge base at a glance"
    />

    <div class="dashboard-content">
      <LoadingState v-if="loading" message="Loading dashboard..." />

      <EmptyState
        v-else-if="isEmpty"
        :icon="LayoutIcon"
        title="Your knowledge base is empty"
        description="Upload a document or start a chat to populate the dashboard."
        action-label="Upload document"
        decor="paper"
        decor-tone="muted"
        @action="goUpload"
      />

      <template v-else>
        <section class="hero-stats">
          <Stat
            v-for="(s, i) in heroStats"
            :key="s.key"
            class="stagger-item"
            :style="{ '--i': i }"
            variant="icon"
            :tone="s.tone"
            :icon="s.icon"
            :value="s.value"
            :label="s.label"
          />
        </section>

        <div class="grid">
          <div class="col-left">
            <Card title="Knowledge growth" meta="last 6 months">
              <div class="bar-chart">
                <div
                  v-for="b in summary.growth"
                  :key="b.month"
                  class="bar-col"
                  :title="`${b.month}: ${b.count} document${b.count === 1 ? '' : 's'}`"
                >
                  <div class="bar-count">{{ b.count || '' }}</div>
                  <div class="bar" :style="{ height: growthBarHeightPct(b.count) + '%' }" />
                  <div class="bar-label">{{ formatMonthShort(b.month) }}</div>
                </div>
              </div>
              <div class="figure-caption">
                <span class="fig-num">Fig. 1</span>
                <span>Documents added per month — last 6 months.</span>
              </div>
            </Card>

            <Card v-if="summary.top_entities.length > 0" title="Top entities" meta="by mention count">
              <ul class="ranked-list">
                <li
                  v-for="(e, idx) in summary.top_entities"
                  :key="e.name"
                  class="ranked-row stagger-item"
                  :style="{ '--i': idx }"
                >
                  <span class="rank-num">{{ idx + 1 }}</span>
                  <span class="rank-name">
                    <span class="entity-name">{{ e.name }}</span>
                    <span class="entity-type">{{ e.type }}</span>
                  </span>
                  <span class="rank-stats">
                    <Tag shape="pill" tone="primary">{{ e.mention_count }} mentions</Tag>
                    <Tag shape="pill" tone="muted">{{ e.doc_count }} doc{{ e.doc_count === 1 ? '' : 's' }}</Tag>
                  </span>
                </li>
              </ul>
            </Card>
          </div>

          <div class="col-right">
            <Card v-if="summary.recent_activity.length > 0" title="Recent activity" meta="uploads + messages">
              <ul class="activity-list">
                <li
                  v-for="(a, idx) in summary.recent_activity"
                  :key="`${a.kind}-${a.id}`"
                  class="activity-row stagger-item"
                  :style="{ '--i': idx }"
                >
                  <span class="activity-icon" :class="`kind-${a.kind}`">
                    <DocumentIcon v-if="a.kind === 'document'" />
                    <MessageIcon v-else />
                  </span>
                  <span class="activity-body">
                    <span class="activity-title">{{ a.title }}</span>
                    <span class="activity-meta">
                      <template v-if="a.kind === 'document'">
                        uploaded · {{ formatTimeAgo(a.created_at) }}
                      </template>
                      <template v-else>
                        {{ a.role === 'user' ? 'you' : 'assistant' }} in
                        <router-link to="/chat" class="link-inline">
                          {{ a.conversation_title || 'untitled chat' }}
                        </router-link>
                        · {{ formatTimeAgo(a.created_at) }}
                      </template>
                    </span>
                  </span>
                </li>
              </ul>
            </Card>

            <Card v-if="summary.top_tags.length > 0" title="Top tags" meta="by usage">
              <div class="tag-cloud">
                <Tag
                  v-for="t in summary.top_tags"
                  :key="t.tag"
                  shape="pill"
                  clickable
                  :title="`${t.count} document${t.count === 1 ? '' : 's'} tagged ${t.tag}`"
                  @click="onTagClick(t.tag)"
                >
                  <span>{{ t.tag }}</span>
                  <span class="tag-count">{{ t.count }}</span>
                </Tag>
              </div>
            </Card>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import { useRouter } from 'vue-router'
import { dashboardApi } from '../api/dashboard'
import {
  PageHeader, Card, Tag, Stat, LoadingState, EmptyState
} from '../components/ui'

const router = useRouter()
const loading = ref(true)
const summary = ref({
  stats: {},
  recent_activity: [],
  top_entities: [],
  top_tags: [],
  growth: []
})

// ---- Icon components (render functions) ----
const LayoutIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('rect', { x: 3, y: 3, width: 7, height: 7 }),
    h('rect', { x: 14, y: 3, width: 7, height: 7 }),
    h('rect', { x: 14, y: 14, width: 7, height: 7 }),
    h('rect', { x: 3, y: 14, width: 7, height: 7 })
  ])
}
const DocumentIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }),
    h('polyline', { points: '14 2 14 8 20 8' })
  ])
}
const MessageIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' })
  ])
}
const EntitiesIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 6, cy: 6, r: 3 }),
    h('circle', { cx: 18, cy: 6, r: 3 }),
    h('circle', { cx: 12, cy: 18, r: 3 }),
    h('line', { x1: 8.59, y1: 8.59, x2: 15.42, y2: 15.42 }),
    h('line', { x1: 15.41, y1: 6.34, x2: 8.59, y2: 9.66 })
  ])
}
const RelationsIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('polyline', { points: '9 11 12 14 22 4' }),
    h('path', { d: 'M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11' })
  ])
}
const MessagesIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('line', { x1: 17, y1: 10, x2: 3, y2: 10 }),
    h('line', { x1: 21, y1: 6, x2: 3, y2: 6 }),
    h('line', { x1: 21, y1: 14, x2: 3, y2: 14 }),
    h('line', { x1: 17, y1: 18, x2: 3, y2: 18 })
  ])
}
const TagIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z' }),
    h('line', { x1: 7, y1: 7, x2: 7.01, y2: 7 })
  ])
}

const heroStats = computed(() => {
  const s = summary.value.stats || {}
  return [
    { key: 'documents',     value: s.documents     || 0, label: 'Documents',     tone: 'primary', icon: DocumentIcon },
    { key: 'entities',      value: s.entities      || 0, label: 'Entities',      tone: 'accent',  icon: EntitiesIcon },
    { key: 'relations',     value: s.relations     || 0, label: 'Relations',     tone: 'warm',    icon: RelationsIcon },
    { key: 'conversations', value: s.conversations || 0, label: 'Conversations', tone: 'cool',    icon: MessageIcon },
    { key: 'messages',      value: s.messages      || 0, label: 'Messages',      tone: 'cool',    icon: MessagesIcon },
    { key: 'tags',          value: s.tags          || 0, label: 'Tags',          tone: 'warm',    icon: TagIcon }
  ]
})

const isEmpty = computed(() => {
  const s = summary.value.stats || {}
  return s.documents === 0 && s.entities === 0 && s.conversations === 0 && s.tags === 0
})

let summaryCtrl = null
const loadSummary = async () => {
  summaryCtrl?.abort()
  summaryCtrl = new AbortController()
  loading.value = true
  try {
    const { data } = await dashboardApi.getSummary({ signal: summaryCtrl.signal })
    summary.value = data || { stats: {}, recent_activity: [], top_entities: [], top_tags: [], growth: [] }
  } catch (error) {
    if (error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED') return
    console.error('Failed to load dashboard:', error)
    summary.value = { stats: {}, recent_activity: [], top_entities: [], top_tags: [], growth: [] }
  } finally {
    loading.value = false
  }
}

const growthBarHeightPct = (count) => {
  const max = Math.max(1, ...summary.value.growth.map(b => b.count))
  if (count <= 0) return 4
  return Math.max(8, Math.round((count / max) * 100))
}

const formatMonthShort = (ym) => {
  if (!ym) return ''
  const [y, m] = ym.split('-')
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  return `${months[parseInt(m, 10) - 1]} '${y.slice(2)}`
}

const formatTimeAgo = (input) => {
  if (!input) return ''
  const d = new Date(input)
  if (isNaN(d.getTime())) return ''
  const diffMs = Date.now() - d.getTime()
  const sec = Math.floor(diffMs / 1000)
  if (sec < 60) return 'just now'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day}d ago`
  return `${Math.floor(day / 30)}mo ago`
}

const goUpload = () => router.push('/documents')
const onTagClick = (tag) => router.push({ path: '/documents', query: { tag } })

onMounted(loadSummary)
onUnmounted(() => summaryCtrl?.abort())
</script>

<style scoped>
.dashboard-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.dashboard-content {
  flex: 1;
  padding: 2rem;
  overflow-y: auto;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}

.hero-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}

.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.25rem;
}
.col-left,
.col-right {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  min-width: 0;
}
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; }
}

.bar-chart {
  display: flex;
  align-items: flex-end;
  gap: 0.5rem;
  height: 140px;
  padding: 0 0.125rem;
  border-bottom: 1px solid var(--border);
}
.bar-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
}
.bar-count {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  margin-bottom: 0.25rem;
  min-height: 0.85em;
}
.bar {
  width: 100%;
  max-width: 28px;
  background: linear-gradient(180deg, var(--primary) 0%, var(--primary-dark) 100%);
  border-radius: 4px 4px 0 0;
  box-shadow: 0 0 16px var(--primary-light);
  transition: opacity var(--transition-fast);
  min-height: 2px;
}
.bar-col:hover .bar { opacity: 0.7; }
.bar-label {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  color: var(--text-tertiary);
  margin-top: 0.4rem;
  white-space: nowrap;
}

.ranked-list { list-style: none; margin: 0; padding: 0; }
.ranked-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--border);
}
.ranked-row:last-child { border-bottom: none; }
.rank-num {
  width: 1.5rem;
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  text-align: center;
}
.rank-name {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.entity-name {
  font-family: var(--font-display);
  font-size: 0.9375rem;
  color: var(--text-primary);
  font-weight: 500;
}
.entity-type {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.rank-stats {
  display: flex;
  gap: 0.375rem;
  flex-shrink: 0;
}

.activity-list { list-style: none; margin: 0; padding: 0; }
.activity-row {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.625rem 0;
  border-bottom: 1px solid var(--border);
}
.activity-row:last-child { border-bottom: none; }
.activity-icon {
  width: 24px;
  height: 24px;
  padding: 4px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 2px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.activity-icon.kind-document { background: var(--glass-bg); color: var(--primary); -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px); border: 1px solid var(--glass-border); }
.activity-icon.kind-message  { background: var(--glass-bg); color: var(--primary); -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px); border: 1px solid var(--glass-border); }
.activity-icon :deep(svg) { width: 100%; height: 100%; }
.activity-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.125rem; }
.activity-title {
  font-size: 0.875rem;
  color: var(--text-primary);
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.activity-meta {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
}
.link-inline { color: var(--primary); text-decoration: none; }
.link-inline:hover { text-decoration: underline; }

.tag-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.tag-count {
  font-variant-numeric: tabular-nums;
  color: var(--text-tertiary);
  font-size: 0.6875rem;
  margin-left: 0.25rem;
}
</style>
