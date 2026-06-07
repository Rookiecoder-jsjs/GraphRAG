<template>
  <div class="entity-detail-page">
    <PageHeader
      :icon="EntityIcon"
      :kicker="detail ? `Entity · ${detail.entity.type}` : 'Entity'"
      :italic-title="true"
      :title="detail?.entity?.name || entityName"
    >
      <template #subtitle>
        <span v-if="detail" class="entity-type-badge">{{ detail.entity.type }}</span>
        <span v-else>Entity</span>
      </template>
      <template #actions>
        <BackButton :to="{ name: 'Graph' }" title="Back to graph" />
      </template>
    </PageHeader>

    <div class="entity-content">
      <LoadingState v-if="loading" message="Loading entity…" />

      <EmptyState
        v-else-if="notFound"
        :icon="XCircleIcon"
        title="Entity not found"
        :description="`“${entityName}” isn't in your knowledge graph yet.`"
      />

      <template v-else-if="detail">
        <blockquote v-if="detail.entity.description" class="pull-quote">
          <p>{{ detail.entity.description }}</p>
          <cite>Entity description · last edited by you</cite>
        </blockquote>

        <div v-if="detail" class="byline">
          <span>{{ detail.entity.type }}</span>
          <span>{{ detail.stats.document_count }} document{{ detail.stats.document_count === 1 ? '' : 's' }}</span>
          <span>{{ detail.stats.mention_count }} mention{{ detail.stats.mention_count === 1 ? '' : 's' }}</span>
          <span>{{ detail.stats.related_entity_count }} related</span>
        </div>

        <section class="stats-row">
          <Stat variant="tile" :value="detail.stats.mention_count" label="mentions" />
          <Stat variant="tile" :value="detail.stats.document_count" label="documents" />
          <Stat variant="tile" :value="detail.stats.related_entity_count" label="related" />
        </section>

        <div class="grid">
          <div class="col-left">
            <Card title="Mentioned in" :meta="`${detail.documents.length} document${detail.documents.length === 1 ? '' : 's'}`">
              <div v-if="detail.documents.length === 0" class="card-empty">
                Not mentioned in any document yet.
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
                      <span>{{ d.chunk_count }} chunk{{ d.chunk_count === 1 ? '' : 's' }}</span>
                      <span v-if="d.first_seen" class="doc-sep">·</span>
                      <span v-if="d.first_seen">{{ formatDate(d.first_seen) }}</span>
                    </div>
                  </div>
                </li>
              </ul>
            </Card>

            <Card title="Related entities" :meta="`${detail.related_entities.length} link${detail.related_entities.length === 1 ? '' : 's'}`">
              <div v-if="detail.related_entities.length === 0" class="card-empty">
                No relations to other entities yet.
              </div>
              <ul v-else class="related-list">
                <li
                  v-for="(r, i) in detail.related_entities"
                  :key="`${r.name}-${i}`"
                  class="related-item"
                  @click="goEntity(r.name)"
                >
                  <span class="related-dir" :class="`dir-${r.direction}`" :title="r.direction">
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
            <Card title="Sample mentions" :meta="`up to ${detail.sample_chunks.length} chunk${detail.sample_chunks.length === 1 ? '' : 's'}`">
              <div v-if="detail.sample_chunks.length === 0" class="card-empty">
                No chunk excerpts available.
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
  PageHeader, BackButton, Stat, Card, Tag, LoadingState, EmptyState
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
  detail.value = null
  try {
    const { data } = await graphApi.getEntityDetail(name)
    detail.value = data
  } catch (error) {
    if (error?.response?.status === 404) {
      notFound.value = true
    } else {
      console.error('Failed to load entity detail:', error)
      notFound.value = true
    }
  } finally {
    loading.value = false
  }
}

onMounted(loadIfChanged)
watch(() => route.params.name, loadIfChanged)

const goBack = () => {
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push('/graph')
  }
}

const goEntity = (name) => {
  router.push({ name: 'EntityDetail', params: { name } })
}

const openDoc = (doc) => {
  router.push({ path: '/documents', query: { doc: doc.doc_id } })
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
.related-dir.dir-incoming { color: #8b5cf6; background: rgba(139,92,246,0.12); }
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
