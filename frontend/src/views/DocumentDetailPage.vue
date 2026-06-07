<template>
  <div class="document-detail-page">
    <PageHeader
      :icon="DocumentIcon"
      :kicker="detail?.document?.file_type ? `Document · ${detail.document.file_type.toUpperCase()}` : 'Document'"
      :title="detail?.document?.title || 'Document'"
    >
      <template #subtitle>
        <span v-if="detail" class="filename">
          {{ detail.document.original_filename }}
          <span v-if="detail.document.file_type" class="filetype">
            · {{ detail.document.file_type.toUpperCase() }}
          </span>
        </span>
        <span v-else>Document</span>
      </template>
      <template #actions>
        <BackButton :to="{ name: 'Documents' }" title="Back to documents" />
      </template>
    </PageHeader>

    <div class="document-content">
      <LoadingState v-if="loading" message="Loading document…" />

      <EmptyState
        v-else-if="notFound"
        :icon="XCircleIcon"
        title="Document not found"
        description="The document you're looking for doesn't exist or has been deleted."
      />

      <template v-else-if="detail">
        <section v-if="detail.document.tags && detail.document.tags.length" class="tags-row">
          <Tag v-for="tag in detail.document.tags" :key="tag" shape="pill" tone="muted">
            {{ tag }}
          </Tag>
        </section>

        <div v-if="detail.stats" class="byline doc-byline">
          <span>{{ detail.document.file_type?.toUpperCase() || 'FILE' }}</span>
          <span>{{ detail.stats.chunk_count }} chunk{{ detail.stats.chunk_count === 1 ? '' : 's' }}</span>
          <span>{{ detail.stats.key_entity_count }} entit{{ detail.stats.key_entity_count === 1 ? 'y' : 'ies' }}</span>
          <span>{{ detail.stats.related_document_count }} related</span>
        </div>

        <section class="stats-row">
          <Stat variant="tile" :value="detail.stats.chunk_count" label="chunks" />
          <Stat variant="tile" :value="detail.stats.key_entity_count" label="key entities" />
          <Stat variant="tile" :value="detail.stats.related_document_count" label="related" />
        </section>

        <div class="grid">
          <div class="col-left">
            <Card title="Key entities" :meta="`${detail.key_entities.length} entit${detail.key_entities.length === 1 ? 'y' : 'ies'}`">
              <div v-if="detail.key_entities.length === 0" class="card-empty">
                No entities extracted yet.
              </div>
              <ul v-else class="entity-list">
                <li
                  v-for="(ent, idx) in detail.key_entities"
                  :key="ent.name"
                  class="entity-row"
                >
                  <span class="entity-rank">#{{ idx + 1 }}</span>
                  <router-link
                    :to="{ name: 'EntityDetail', params: { name: encodeURIComponent(ent.name) } }"
                    class="entity-name"
                  >{{ ent.name }}</router-link>
                  <Tag shape="badge" tone="muted">{{ ent.type }}</Tag>
                  <span class="entity-count">{{ ent.mention_count }} mention{{ ent.mention_count === 1 ? '' : 's' }}</span>
                </li>
              </ul>
            </Card>

            <Card title="Sample chunks" :meta="`${detail.sample_chunks.length} of ${detail.stats.chunk_count} chunks`">
              <div v-if="detail.sample_chunks.length === 0" class="card-empty">
                No chunks available.
              </div>
              <ol v-else class="chunk-list">
                <li
                  v-for="chunk in detail.sample_chunks"
                  :key="chunk.chunk_id"
                  class="chunk-row"
                >
                  <div v-if="chunk.hierarchy_path" class="chunk-path">
                    {{ chunk.hierarchy_path.split(',').map(s => s.trim()).filter(Boolean).join(' › ') }}
                  </div>
                  <div class="chunk-content">{{ chunk.content }}</div>
                </li>
              </ol>
            </Card>
          </div>

          <div class="col-right">
            <Card title="Related documents" :meta="`${detail.related_documents.length} document${detail.related_documents.length === 1 ? '' : 's'}`">
              <div v-if="detail.related_documents.length === 0" class="card-empty">
                No related documents found. Other documents that mention the same entities as this one will appear here.
              </div>
              <ul v-else class="related-list">
                <li
                  v-for="rel in detail.related_documents"
                  :key="rel.doc_id"
                  class="related-row"
                >
                  <router-link
                    :to="{ name: 'DocumentDetail', params: { id: rel.doc_id } }"
                    class="related-link"
                  >
                    <span class="related-title">{{ rel.title }}</span>
                    <span class="related-shared">
                      {{ rel.shared_count }} shared entit{{ rel.shared_count === 1 ? 'y' : 'ies' }}
                    </span>
                  </router-link>
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
import { documentApi } from '../api/documents'
import {
  PageHeader, BackButton, Stat, Card, Tag, LoadingState, EmptyState
} from '../components/ui'

const DocumentIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }),
    h('polyline', { points: '14 2 14 8 20 8' })
  ])
}
const XCircleIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 9 }),
    h('line', { x1: 9, y1: 9, x2: 15, y2: 15 }),
    h('line', { x1: 15, y1: 9, x2: 9, y2: 15 })
  ])
}

const route = useRoute()
const router = useRouter()

const detail = ref(null)
const loading = ref(true)
const notFound = ref(false)

const docId = () => route.params.id

const loadDetail = async () => {
  loading.value = true
  notFound.value = false
  detail.value = null
  try {
    const { data } = await documentApi.getDetail(docId())
    detail.value = data
  } catch (err) {
    if (err?.response?.status === 404) {
      notFound.value = true
    } else {
      console.error('Failed to load document detail:', err)
    }
  } finally {
    loading.value = false
  }
}

const goBack = () => {
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push({ name: 'Documents' })
  }
}

let lastId = null
const loadIfChanged = async () => {
  const id = docId()
  if (!id || id === lastId) return
  lastId = id
  await loadDetail()
}

onMounted(loadIfChanged)
watch(() => route.params.id, loadIfChanged)
</script>

<style scoped>
.document-detail-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.document-content {
  flex: 1;
  padding: 2rem;
  overflow-y: auto;
}

.filename { color: var(--text-secondary); }
.filetype { color: var(--text-tertiary); font-weight: 600; }

.tags-row {
  max-width: 1200px;
  margin: 0 auto 1rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.doc-byline {
  max-width: 1200px;
  margin: 0 auto 1rem;
  padding: 0 0.125rem;
}

.stats-row {
  max-width: 1200px;
  margin: 0 auto 1.5rem;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
}

.grid {
  max-width: 1200px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 1.5rem;
}
.col-left, .col-right {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  min-width: 0;
}
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; }
}

.card-empty {
  font-size: 0.8125rem;
  color: var(--text-tertiary);
  font-style: italic;
  text-align: center;
  padding: 1rem 0;
}

.entity-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.entity-row {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 0.625rem;
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius-sm);
  transition: background-color var(--transition-fast);
}
.entity-row:hover { background: var(--bg-tertiary); }
.entity-rank {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  font-weight: 600;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
.entity-name {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--primary);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.entity-name:hover { text-decoration: underline; }
.entity-count {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.chunk-list {
  list-style: none;
  margin: 0;
  padding: 0;
  counter-reset: chunk;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.chunk-row {
  position: relative;
  padding: 0.75rem 0.875rem 0.75rem 2rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  border-left: 2px solid var(--primary);
}
.chunk-row::before {
  counter-increment: chunk;
  content: counter(chunk);
  position: absolute;
  left: 0.625rem;
  top: 0.75rem;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  font-weight: 600;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
.chunk-path {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  margin-bottom: 0.375rem;
  letter-spacing: 0.02em;
}
.chunk-content {
  font-size: 0.8125rem;
  line-height: 1.5;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-wrap: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.related-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.375rem; }
.related-row {
  border-radius: var(--radius-sm);
  transition: background-color var(--transition-fast);
}
.related-row:hover { background: var(--bg-tertiary); }
.related-link {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.625rem 0.75rem;
  text-decoration: none;
}
.related-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.related-link:hover .related-title { color: var(--primary); }
.related-shared {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

@media (max-width: 600px) {
  .document-content { padding: 1rem; }
}
</style>
