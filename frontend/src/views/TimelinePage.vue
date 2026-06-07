<template>
  <div class="timeline-page">
    <PageHeader
      :icon="ClockIcon"
      title="Timeline"
      subtitle="How your knowledge base has grown"
    />

    <div class="timeline-content">
      <LoadingState v-if="loading" message="Loading timeline..." />

      <EmptyState
        v-else-if="!hasAnything"
        :icon="ClockIcon"
        title="No history yet"
        description="Upload a document to start building your timeline."
      />

      <template v-else>
        <Card v-if="documentsByMonth.length > 0" compact>
          <header class="section-header">
            <h2 class="section-title">Documents over time</h2>
            <span class="section-meta">{{ totalDocuments }} total · {{ documentsByMonth.length }} months</span>
          </header>

          <div class="bar-chart">
            <div
              v-for="bucket in documentsByMonth"
              :key="bucket.month"
              class="bar-col"
              :title="`${bucket.month}: ${bucket.count} document${bucket.count === 1 ? '' : 's'}`"
            >
              <div class="bar-count">{{ bucket.count }}</div>
              <div class="bar" :style="{ height: barHeightPct(bucket.count) + '%' }" />
              <div class="bar-label">{{ formatMonth(bucket.month) }}</div>
            </div>
          </div>
        </Card>

        <Card v-if="entityTimeline.length > 0" compact>
          <header class="section-header">
            <h2 class="section-title">Topics first seen</h2>
            <span class="section-meta">{{ entityTimeline.length }} entities</span>
          </header>

          <ul class="entity-list">
            <li
              v-for="e in entityTimeline"
              :key="e.name"
              class="entity-row"
            >
              <div class="entity-date">
                <span v-if="e.first_seen" class="date-pretty">{{ formatPrettyDate(e.first_seen) }}</span>
                <span v-else class="date-missing">no date</span>
              </div>
              <div class="entity-marker" :class="`marker-${entityMarkerClass(e.type)}`">
                <span class="marker-dot" />
              </div>
              <div class="entity-info">
                <div class="entity-name-row">
                  <span class="entity-name">{{ e.name }}</span>
                  <span class="entity-type">{{ e.type }}</span>
                </div>
                <div class="entity-meta">
                  <template v-if="e.first_seen_doc_id && e.first_seen_doc_title">
                    introduced in
                    <router-link
                      to="/documents"
                      class="doc-link"
                      :title="`Open ${e.first_seen_doc_title} in Documents`"
                    >{{ e.first_seen_doc_title }}</router-link>
                  </template>
                  <template v-else-if="e.mention_count > 0">
                    <span class="orphan-meta">mentions exist but all source documents were deleted</span>
                  </template>
                  <span class="entity-stats">
                    · {{ e.mention_count }} mention{{ e.mention_count === 1 ? '' : 's' }}
                    across {{ e.doc_count }} document{{ e.doc_count === 1 ? '' : 's' }}
                  </span>
                </div>
              </div>
            </li>
          </ul>
        </Card>

        <Card v-if="recentDocuments.length > 0" compact>
          <header class="section-header">
            <h2 class="section-title">Recently added</h2>
            <span class="section-meta">last {{ recentDocuments.length }}</span>
          </header>

          <ul class="doc-list">
            <li
              v-for="doc in recentDocuments"
              :key="doc.id"
              class="doc-row"
            >
              <div class="doc-date">
                <span class="date-pretty">{{ formatPrettyDate(doc.created_at) }}</span>
                <span class="time-ago">{{ formatTimeAgo(doc.created_at) }}</span>
              </div>
              <div class="doc-info">
                <span class="doc-title">{{ doc.title || doc.original_filename }}</span>
                <span class="doc-filename">{{ doc.original_filename }}</span>
              </div>
            </li>
          </ul>
        </Card>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, h } from 'vue'
import { timelineApi } from '../api/timeline'
import { PageHeader, Card, EmptyState, LoadingState } from '../components/ui'

const ClockIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 10 }),
    h('polyline', { points: '12 6 12 12 16 14' })
  ])
}

const loading = ref(true)
const documentsByMonth = ref([])
const recentDocuments = ref([])
const entityTimeline = ref([])

const totalDocuments = computed(() =>
  documentsByMonth.value.reduce((sum, b) => sum + b.count, 0)
)
const hasAnything = computed(() =>
  documentsByMonth.value.length > 0 ||
  recentDocuments.value.length > 0 ||
  entityTimeline.value.length > 0
)

const loadTimeline = async () => {
  loading.value = true
  try {
    const { data } = await timelineApi.get()
    documentsByMonth.value = data?.documents_by_month || []
    recentDocuments.value = data?.recent_documents || []
    entityTimeline.value = data?.entity_timeline || []
  } catch (error) {
    console.error('Failed to load timeline:', error)
    documentsByMonth.value = []
    recentDocuments.value = []
    entityTimeline.value = []
  } finally {
    loading.value = false
  }
}

const barHeightPct = (count) => {
  if (!documentsByMonth.value.length) return 0
  const max = Math.max(...documentsByMonth.value.map(b => b.count))
  if (max <= 0) return 0
  return Math.max(8, Math.round((count / max) * 100))
}

const formatMonth = (ym) => {
  if (!ym) return ''
  const [y, m] = ym.split('-')
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  return `${months[parseInt(m, 10) - 1]} '${y.slice(2)}`
}

const formatPrettyDate = (input) => {
  if (!input) return ''
  const d = new Date(input)
  if (isNaN(d.getTime())) return String(input)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
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
  const mo = Math.floor(day / 30)
  if (mo < 12) return `${mo}mo ago`
  return `${Math.floor(mo / 12)}y ago`
}

const entityMarkerClass = (type) => {
  if (!type) return 'default'
  const t = type.toLowerCase()
  if (t.includes('person') || t.includes('people')) return 'person'
  if (t.includes('product') || t.includes('service')) return 'product'
  if (t.includes('org') || t.includes('company')) return 'org'
  if (t.includes('place') || t.includes('location')) return 'place'
  if (t.includes('concept') || t.includes('topic')) return 'concept'
  return 'default'
}

onMounted(loadTimeline)
</script>

<style scoped>
.timeline-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.timeline-content {
  flex: 1;
  padding: 2rem;
  overflow-y: auto;
  max-width: 1000px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.section-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 1.25rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--border);
}

.section-title {
  font-family: var(--font-display);
  font-size: 1.125rem;
  font-weight: 500;
  color: var(--text-primary);
  letter-spacing: -0.01em;
  margin: 0;
}

.section-meta {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.bar-chart {
  display: flex;
  align-items: flex-end;
  gap: 0.5rem;
  height: 200px;
  padding: 0 0.25rem;
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
  position: relative;
}
.bar-count {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  margin-bottom: 0.25rem;
}
.bar {
  width: 100%;
  max-width: 36px;
  background: var(--primary);
  border-radius: 2px 2px 0 0;
  transition: opacity var(--transition-fast);
  min-height: 2px;
}
.bar-col:hover .bar { opacity: 0.7; }
.bar-label {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  color: var(--text-tertiary);
  margin-top: 0.5rem;
  white-space: nowrap;
  letter-spacing: 0.02em;
}

.entity-list,
.doc-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.entity-row {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--border);
}
.entity-row:last-child { border-bottom: none; }

.entity-date {
  width: 7.5rem;
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-secondary);
  padding-top: 0.125rem;
}
.date-pretty { display: block; font-variant-numeric: tabular-nums; }
.date-missing { color: var(--text-tertiary); font-style: italic; }

.entity-marker {
  position: relative;
  width: 14px;
  flex-shrink: 0;
  padding-top: 0.5rem;
}
.entity-marker::before {
  content: '';
  position: absolute;
  left: 50%;
  top: 0;
  bottom: -0.75rem;
  width: 1px;
  background: var(--border);
  transform: translateX(-50%);
}
.entity-row:last-child .entity-marker::before { display: none; }

.marker-dot {
  position: relative;
  display: block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--primary);
  border: 2px solid var(--bg-primary);
  margin: 0 auto;
  z-index: 1;
}
.marker-person .marker-dot { background: #8b6cef; }
.marker-product .marker-dot { background: var(--primary); }
.marker-org .marker-dot { background: #d97706; }
.marker-place .marker-dot { background: #059669; }
.marker-concept .marker-dot { background: #6366f1; }
.marker-default .marker-dot { background: var(--text-tertiary); }

.entity-info { flex: 1; min-width: 0; }
.entity-name-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.entity-name {
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 500;
  color: var(--text-primary);
}
.entity-type {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.entity-meta {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  margin-top: 0.125rem;
}
.doc-link {
  color: var(--primary);
  text-decoration: none;
  font-weight: 500;
  border-bottom: 1px dotted transparent;
  transition: border-color var(--transition-fast);
}
.doc-link:hover { border-bottom-color: var(--primary); }
.orphan-meta { color: var(--text-tertiary); font-style: italic; }
.entity-stats {
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-size: 0.75rem;
}

.doc-row {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.625rem 0;
  border-bottom: 1px solid var(--border);
}
.doc-row:last-child { border-bottom: none; }
.doc-date {
  width: 7.5rem;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}
.time-ago {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
}
.doc-info {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
}
.doc-title {
  font-family: var(--font-display);
  font-size: 0.9375rem;
  color: var(--text-primary);
  font-weight: 500;
}
.doc-filename {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
