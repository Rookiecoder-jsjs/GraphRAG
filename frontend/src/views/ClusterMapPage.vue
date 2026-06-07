<template>
  <div class="cluster-map-page">
    <PageHeader
      :icon="ClusterIcon"
      title="Document Cluster Map"
      subtitle="Each dot is a document. Distance reflects semantic similarity — nearby dots share topics."
    >
      <template #actions>
        <BackButton :to="{ name: 'Documents' }" title="Back to documents" />
      </template>
    </PageHeader>

    <div class="map-content">
      <LoadingState v-if="loading" message="Loading map…" />

      <div v-else-if="errorMessage" class="error-state">
        <p>{{ errorMessage }}</p>
        <Button variant="secondary" @click="loadMap">Retry</Button>
      </div>

      <EmptyState
        v-else-if="!points || points.length < 2"
        :icon="ClusterIcon"
        title="Not enough data yet"
        :description="`Upload at least two documents with content to see them clustered by topic similarity. Currently showing ${points?.length || 0}.`"
        action-label="Back to documents"
        @action="goBack"
      />

      <template v-else>
        <div v-if="legendItems.length > 1" class="legend">
          <span class="legend-label">File type:</span>
          <span
            v-for="item in legendItems"
            :key="item.type"
            class="legend-item"
          >
            <Dot :color="item.color" />
            <span class="legend-text">{{ item.type || 'unknown' }}</span>
          </span>
        </div>

        <Card class="map-card">
          <svg
            class="map-svg"
            :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`"
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label="Document cluster map"
          >
            <g class="grid">
              <line v-for="i in 4" :key="`v${i}`"
                :x1="(i * VIEW_W) / 5" :y1="0"
                :x2="(i * VIEW_W) / 5" :y2="VIEW_H"
                stroke="var(--border)" stroke-width="0.5" stroke-dasharray="2,4" />
              <line v-for="i in 4" :key="`h${i}`"
                :x1="0" :y1="(i * VIEW_H) / 5"
                :x2="VIEW_W" :y2="(i * VIEW_H) / 5"
                stroke="var(--border)" stroke-width="0.5" stroke-dasharray="2,4" />
            </g>

            <g class="crosshair">
              <line :x1="VIEW_W / 2" :y1="0" :x2="VIEW_W / 2" :y2="VIEW_H"
                stroke="var(--border)" stroke-width="1" />
              <line :x1="0" :y1="VIEW_H / 2" :x2="VIEW_W" :y2="VIEW_H / 2"
                stroke="var(--border)" stroke-width="1" />
            </g>

            <g class="points">
              <g
                v-for="p in renderedPoints"
                :key="p.doc_id"
                class="point-group"
                :class="{ hovered: hoveredId === p.doc_id }"
                @mouseenter="hoveredId = p.doc_id"
                @mouseleave="hoveredId = null"
                @click="goToDetail(p.doc_id)"
              >
                <circle
                  :cx="p._vx"
                  :cy="p._vy"
                  :r="hoveredId === p.doc_id ? 9 : 6"
                  :fill="colorFor(p.file_type)"
                  :stroke="hoveredId === p.doc_id ? 'var(--text-primary)' : 'none'"
                  stroke-width="1.5"
                />
                <text
                  v-if="hoveredId === p.doc_id"
                  :x="p._vx"
                  :y="p._vy - 14"
                  text-anchor="middle"
                  class="point-label"
                >{{ p.title }}</text>
              </g>
            </g>
          </svg>
        </Card>

        <div class="footer-hint">
          Hover a dot to see the title · click to open the document
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, h } from 'vue'
import { useRouter } from 'vue-router'
import { documentApi } from '../api/documents'
import { PageHeader, BackButton, Card, EmptyState, LoadingState, Button, Dot } from '../components/ui'

const router = useRouter()

const ClusterIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 6, cy: 6, r: 2 }),
    h('circle', { cx: 18, cy: 6, r: 2 }),
    h('circle', { cx: 12, cy: 18, r: 2 })
  ])
}

const VIEW_W = 800
const VIEW_H = 560
const PADDING = 40

const points = ref([])
const loading = ref(true)
const errorMessage = ref(null)
const hoveredId = ref(null)

const PALETTE = [
  '#6366f1', '#10b981', '#f59e0b',
  '#ef4444', '#8b5cf6', '#06b6d4',
]
const FALLBACK_COLOR = '#9ca3af'

const fileTypeColorIndex = computed(() => {
  const map = {}
  let i = 0
  for (const p of points.value || []) {
    const t = (p.file_type || '').toLowerCase()
    if (!(t in map)) {
      map[t] = i % PALETTE.length
      i += 1
    }
  }
  return map
})

const colorFor = (fileType) => {
  const t = (fileType || '').toLowerCase()
  const idx = fileTypeColorIndex.value[t]
  if (idx === undefined) return FALLBACK_COLOR
  return PALETTE[idx]
}

const legendItems = computed(() => {
  const items = []
  for (const [t, idx] of Object.entries(fileTypeColorIndex.value)) {
    items.push({ type: t, color: PALETTE[idx] })
  }
  return items
})

const renderedPoints = computed(() => {
  const pts = points.value || []
  if (pts.length < 2) return []
  const xs = pts.map((p) => p.x)
  const ys = pts.map((p) => p.y)
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const rangeX = maxX - minX || 1
  const rangeY = maxY - minY || 1
  return pts.map((p) => ({
    ...p,
    _vx: PADDING + ((p.x - minX) / rangeX) * (VIEW_W - 2 * PADDING),
    _vy: PADDING + (1 - (p.y - minY) / rangeY) * (VIEW_H - 2 * PADDING),
  }))
})

const loadMap = async () => {
  loading.value = true
  errorMessage.value = null
  try {
    const { data } = await documentApi.getClusterMap()
    points.value = data?.points || []
  } catch (err) {
    if (err?.response?.status === 404) {
      points.value = []
    } else {
      console.error('Failed to load cluster map:', err)
      errorMessage.value = 'Could not load the map. Please try again.'
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

const goToDetail = (docId) => {
  router.push({ name: 'DocumentDetail', params: { id: docId } })
}

onMounted(loadMap)
</script>

<style scoped>
.cluster-map-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.map-content {
  flex: 1;
  padding: 2rem;
  overflow-y: auto;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  color: var(--text-tertiary);
  gap: 1rem;
  text-align: center;
}

.legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-bottom: 1rem;
  padding: 0.625rem 1rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.legend-label {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-tertiary);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.map-card { padding: 1rem; }
.map-svg { width: 100%; height: auto; display: block; }

.point-group { cursor: pointer; }
.point-group circle { transition: r 0.15s ease, stroke-width 0.15s ease; }

.point-label {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 500;
  fill: var(--text-primary);
  paint-order: stroke;
  stroke: var(--bg-primary);
  stroke-width: 4;
  stroke-linejoin: round;
}

.footer-hint {
  text-align: center;
  margin-top: 1rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

@media (max-width: 600px) {
  .map-content { padding: 1rem; }
}
</style>
