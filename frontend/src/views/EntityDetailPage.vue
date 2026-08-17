<template>
  <div class="entity-detail-page">
    <PageHeader
      :icon="EntityIcon"
      :kicker="detail ? `实体 · ${detail.entity.type}` : '实体'"
      :italic-title="true"
      :title="detail?.entity?.name || entityName"
    >
      <template #subtitle>
        <span v-if="detail" class="entity-type-badge">{{ detail.entity.type }}</span>
        <span v-else>实体</span>
      </template>
      <template #actions>
        <BackButton :to="{ name: 'Graph' }" title="返回图谱" />
      </template>
    </PageHeader>

    <div class="entity-content">
      <LoadingState v-if="loading" message="正在加载实体…" />

      <ErrorState
        v-else-if="loadError"
        title="加载实体失败"
        description="获取该实体时出现了问题。"
        @retry="load"
      />

      <EmptyState
        v-else-if="notFound"
        :icon="XCircleIcon"
        title="未找到实体"
        :description="`“${entityName}” 尚未存在于你的知识图谱中。`"
      />

      <template v-else-if="detail">
        <blockquote v-if="detail.entity.description" class="pull-quote">
          <p>{{ detail.entity.description }}</p>
          <cite>实体描述 · 最后由你编辑</cite>
        </blockquote>

        <div v-if="detail" class="byline">
          <span>{{ detail.entity.type }}</span>
          <span>{{ detail.stats.document_count }} 份文档</span>
          <span>{{ detail.stats.mention_count }} 次提及</span>
          <span>{{ detail.stats.related_entity_count }} 个相关</span>
        </div>

        <section class="stats-row">
          <Stat variant="tile" :value="detail.stats.mention_count" label="提及" />
          <Stat variant="tile" :value="detail.stats.document_count" label="文档" />
          <Stat variant="tile" :value="detail.stats.related_entity_count" label="相关" />
        </section>

        <div class="grid">
          <div class="col-left">
            <Card title="提及于" :meta="`${detail.documents.length} 份文档`">
              <div v-if="detail.documents.length === 0" class="card-empty">
                尚未在任何文档中被提及。
              </div>
              <ul v-else class="doc-list">
                <li
                  v-for="d in detail.documents"
                  :key="d.doc_id"
                  class="doc-item"
                  @click="openDoc(d)"
                >
                  <div class="doc-icon">
                    <DocumentIcon />
                  </div>
                  <div class="doc-meta">
                    <div class="doc-title">{{ d.title }}</div>
                    <div class="doc-sub">
                      <span>{{ d.chunk_count }} 个分块</span>
                      <span v-if="d.first_seen" class="doc-sep">·</span>
                      <span v-if="d.first_seen">{{ formatDate(d.first_seen) }}</span>
                    </div>
                  </div>
                </li>
              </ul>
            </Card>

            <Card title="相关实体" :meta="`${detail.related_entities.length} 条关联`">
              <div v-if="detail.related_entities.length === 0" class="card-empty">
                尚未与其他实体建立关系。
              </div>
              <ul v-else class="related-list">
                <li
                  v-for="(r, i) in detail.related_entities"
                  :key="`${r.name}-${i}`"
                  class="related-item"
                  @click="goEntity(r.name)"
                >
                  <span class="related-dir" :class="`dir-${r.direction}`" :title="directionLabel(r.direction)">
                    <ArrowRightIcon v-if="r.direction === 'outgoing'" />
                    <ArrowLeftIcon v-else />
                  </span>
                  <span class="related-name">{{ r.name }}</span>
                  <Tag shape="badge" tone="muted">{{ r.relation_type }}</Tag>
                  <Tag shape="badge" tone="muted">{{ r.type }}</Tag>
                </li>
              </ul>
            </Card>
          </div>

          <div class="col-right">
            <Card title="提及示例" :meta="`最多 ${detail.sample_chunks.length} 个分块`">
              <div v-if="detail.sample_chunks.length === 0" class="card-empty">
                暂无分块摘录。
              </div>
              <ul v-else class="chunk-list">
                <li
                  v-for="c in detail.sample_chunks"
                  :key="c.chunk_id"
                  class="chunk-item"
                >
                  <div class="chunk-source">{{ c.doc_title }}</div>
                  <div class="chunk-preview">{{ c.content_preview || '…' }}</div>
                </li>
              </ul>
            </Card>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { graphApi } from '../api/graph'
import {
  PageHeader, BackButton, Stat, Card, Tag, LoadingState, EmptyState, ErrorState
} from '../components/ui'

const EntityIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 3 }),
    h('circle', { cx: 12, cy: 12, r: 9 })
  ])
}
const XCircleIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 9 }),
    h('line', { x1: 9, y1: 9, x2: 15, y2: 15 }),
    h('line', { x1: 15, y1: 9, x2: 9, y2: 15 })
  ])
}
const DocumentIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }),
    h('polyline', { points: '14 2 14 8 20 8' })
  ])
}
const ArrowRightIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('line', { x1: 5, y1: 12, x2: 19, y2: 12 }),
    h('polyline', { points: '12 5 19 12 12 19' })
  ])
}
const ArrowLeftIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('line', { x1: 19, y1: 12, x2: 5, y2: 12 }),
    h('polyline', { points: '12 19 5 12 12 5' })
  ])
}

const route = useRoute()
const router = useRouter()

const detail = ref(null)
const loading = ref(true)
const notFound = ref(false)
const loadError = ref(false)
const entityName = ref('')

let lastName = null
const loadIfChanged = () => load()

const load = async () => {
  const name = String(route.params.name || '').trim()
  if (!name || name === lastName) return
  lastName = name
  entityName.value = name
  loading.value = true
  notFound.value = false
  loadError.value = false
  detail.value = null
  try {
    const { data } = await graphApi.getEntityDetail(name)
    detail.value = data
  } catch (error) {
    if (error?.response?.status === 404) {
      notFound.value = true
    } else {
      console.error('Failed to load entity detail:', error)
      loadError.value = true
    }
  } finally {
    loading.value = false
  }
}

onMounted(loadIfChanged)
watch(() => route.params.name, loadIfChanged)

// 关系方向的 tooltip：后端返回 outgoing/incoming 原始值，展示时映射为中文
const directionLabel = (d) => (d === 'outgoing' ? '指向该实体' : '来自该实体')

const goBack = () => {
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push('/graph')
  }
}

const goEntity = (name) => {
  // LLM-extracted entity names are unconstrained (?/#//…); the route is
  // `entities/:name(.*)*` and the router decodes the encoded segment back,
  // so encode on push (same convention as GraphPage / Timeline).
  router.push({ name: 'EntityDetail', params: { name: encodeURIComponent(name) } })
}

const openDoc = (doc) => {
  // DocumentsPage never reads `route.query.doc`, so the old `/documents?doc=`
  // navigation was a no-op. Point it at the real per-document detail page.
  router.push({ name: 'DocumentDetail', params: { id: doc.doc_id } })
}

const formatDate = (s) => {
  if (!s) return ''
  return String(s).split(' ')[0] || s
}
</script>

<style scoped>
.entity-detail-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.entity-content {
  flex: 1;
  overflow-y: auto;
  max-width: 1200px;
  width: 100%;
  margin: 0 auto;
  padding: 1.75rem 2rem 3rem;
}

.entity-type-badge {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  letter-spacing: 0.06em;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}

.description-card {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.25rem;
  margin-bottom: 1.5rem;
}
.description-label {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  letter-spacing: 0.06em;
  color: var(--text-tertiary);
  margin-bottom: 0.5rem;
}
.description-text {
  color: var(--text-primary);
  line-height: 1.6;
  font-size: 0.9375rem;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 1.5rem;
}
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; }
}

.col-left, .col-right {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  min-width: 0;
}

.card-empty {
  padding: 1.25rem;
  color: var(--text-tertiary);
  font-size: 0.875rem;
  font-style: italic;
}

.doc-list { list-style: none; }
.doc-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1.25rem;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}
.doc-item:last-child { border-bottom: none; }
.doc-item:hover { background-color: var(--bg-tertiary); }
.doc-icon {
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  flex-shrink: 0;
}
.doc-icon :deep(svg) { width: 16px; height: 16px; }
.doc-meta { min-width: 0; flex: 1; }
.doc-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-sub {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 2px;
  font-size: 0.75rem;
  color: var(--text-tertiary);
}

.related-list { list-style: none; }
.related-item {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.625rem 1.25rem;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}
.related-item:last-child { border-bottom: none; }
.related-item:hover { background-color: var(--bg-tertiary); }
.related-dir {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  flex-shrink: 0;
}
.related-dir :deep(svg) { width: 12px; height: 12px; }
.related-dir.dir-outgoing { color: var(--primary); background: var(--primary-light); }
.related-dir.dir-incoming { color: var(--graph-concept); background: var(--primary-light); }
.related-name {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-primary);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-list { list-style: none; }
.chunk-item {
  padding: 0.875rem 1.25rem;
  border-bottom: 1px solid var(--border);
}
.chunk-item:last-child { border-bottom: none; }
.chunk-source {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 0.375rem;
}
.chunk-preview {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  line-height: 1.5;
  font-family: var(--font-display);
  font-style: italic;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
