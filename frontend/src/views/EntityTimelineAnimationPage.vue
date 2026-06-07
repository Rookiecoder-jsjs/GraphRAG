<template>
  <div class="timeline-anim-page">
    <PageHeader
      :icon="ClockIcon"
      title="Entity Timeline"
      subtitle="Watch your knowledge base grow — each dot is an entity, introduced on the day it first appeared in your documents."
    >
      <template #actions>
        <BackButton :to="{ name: 'Graph' }" title="Back to graph" />
      </template>
    </PageHeader>

    <div class="anim-content">
      <LoadingState v-if="loading" message="Loading entities…" />

      <div v-else-if="errorMessage" class="error-state">
        <p>{{ errorMessage }}</p>
        <Button variant="secondary" @click="loadAll">Retry</Button>
      </div>

      <EmptyState
        v-else-if="!hasData"
        :icon="XCircleIcon"
        title="No dated entities yet"
        description="Entity dates are derived from when each entity first appears in your documents. Upload a processed document, then come back to watch the timeline fill in."
        action-label="Back to graph"
        @action="goBack"
      />

      <template v-else>
        <div class="controls">
          <div class="stats">
            <Tag shape="pill" tone="muted">
              <span class="stat-num">{{ visibleCount }}</span>
              <span class="stat-label">of {{ entities.length }} entities</span>
            </Tag>
            <Tag shape="pill" tone="muted">
              <span class="stat-num">{{ formatDate(currentDate) }}</span>
              <span class="stat-label">up to</span>
            </Tag>
            <Button
              v-if="!atEnd"
              variant="link"
              size="sm"
              class="jump-btn"
              @click="jumpToEnd"
            >
              Skip to end ›
            </Button>
          </div>

          <div class="slider-row">
            <input
              type="range"
              class="time-slider"
              :min="0"
              :max="totalDays - 1"
              :value="dayIndex"
              :disabled="playing"
              @input="onSliderInput"
              aria-label="Time scrubber"
            />
            <div class="slider-bounds">
              <span>{{ formatDate(range.min) }}</span>
              <span>{{ formatDate(range.max) }}</span>
            </div>
          </div>

          <div class="playback">
            <Button
              variant="primary"
              :class="{ playing }"
              :disabled="atEnd && !playing"
              @click="togglePlay"
              class="play-btn"
            >
              <PlayIcon v-if="!playing" />
              <PauseIcon v-else />
              <span>{{ playing ? 'Pause' : 'Play' }}</span>
            </Button>
            <div class="speed-control">
              <label>Speed:</label>
              <select v-model.number="speedMs" :disabled="playing" @change="onSpeedChange">
                <option :value="800">0.5×</option>
                <option :value="400">1×</option>
                <option :value="200">2×</option>
                <option :value="100">4×</option>
              </select>
            </div>
          </div>
        </div>

        <div class="body">
          <Card compact class="map-card">
            <svg
              class="anim-svg"
              :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`"
              preserveAspectRatio="xMidYMid meet"
              role="img"
              aria-label="Entity timeline"
            >
              <g class="grid">
                <line
                  v-for="(t, i) in typeList"
                  :key="`h${i}`"
                  :x1="40" :y1="yForType(t)"
                  :x2="VIEW_W" :y2="yForType(t)"
                  stroke="var(--border)" stroke-width="0.5" stroke-dasharray="2,4"
                />
              </g>

              <g class="y-labels">
                <text
                  v-for="(t, i) in typeList"
                  :key="`yl${i}`"
                  x="8" :y="yForType(t) + 4"
                  class="y-label"
                >{{ t }}</text>
              </g>

              <line
                v-if="totalDays > 0"
                :x1="xForDayIndex(dayIndex)"
                :y1="0"
                :x2="xForDayIndex(dayIndex)"
                :y2="VIEW_H"
                stroke="var(--primary)" stroke-width="1.5" stroke-dasharray="3,3"
              />

              <g class="dots">
                <g
                  v-for="e in visibleEntities"
                  :key="e.name"
                  class="dot-group"
                  :class="{ hovered: hoveredName === e.name }"
                  @mouseenter="hoveredName = e.name"
                  @mouseleave="hoveredName = null"
                  @click="goToEntity(e.name)"
                >
                  <circle
                    :cx="xForDate(e.first_seen)"
                    :cy="yForType(e.type || 'Unknown')"
                    :r="radiusFor(e.mention_count)"
                    :fill="colorForType(e.type || 'Unknown')"
                    :opacity="hoveredName === e.name ? 1 : 0.78"
                    :stroke="hoveredName === e.name ? 'var(--text-primary)' : 'none'"
                    stroke-width="1.5"
                  />
                  <text
                    v-if="hoveredName === e.name"
                    :x="xForDate(e.first_seen)"
                    :y="yForType(e.type || 'Unknown') - radiusFor(e.mention_count) - 6"
                    text-anchor="middle"
                    class="dot-label"
                  >{{ e.name }}</text>
                </g>
              </g>
            </svg>
          </Card>

          <Card compact class="list-card">
            <header class="list-header">
              <h3>Introduced by {{ formatDate(currentDate) }}</h3>
              <span class="list-count">{{ visibleCount }} entit{{ visibleCount === 1 ? 'y' : 'ies' }}</span>
            </header>
            <div v-if="visibleEntities.length === 0" class="list-empty">
              Scrub forward to reveal entities.
            </div>
            <ul v-else class="entity-list">
              <li
                v-for="e in visibleEntities"
                :key="e.name"
                class="entity-item"
                :class="{ hovered: hoveredName === e.name }"
                @mouseenter="hoveredName = e.name"
                @mouseleave="hoveredName = null"
                @click="goToEntity(e.name)"
              >
                <span class="entity-dot" :style="{ background: colorForType(e.type || 'Unknown') }" />
                <div class="entity-meta">
                  <span class="entity-name">{{ e.name }}</span>
                  <span class="entity-sub">
                    {{ e.type || 'Unknown' }} ·
                    {{ e.mention_count }} mention{{ e.mention_count === 1 ? '' : 's' }} ·
                    {{ formatDate(e.first_seen) }}
                  </span>
                </div>
              </li>
            </ul>
          </Card>
        </div>

        <div class="footer-hint">
          Hover an entity to highlight it in both views · click to open its detail page
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import { useRouter } from 'vue-router'
import { timelineApi } from '../api/timeline'
import { entitiesVisibleAt, dateRangeOf } from '../utils/timelineAnim'
import { PageHeader, BackButton, Card, Tag, Button, EmptyState, LoadingState } from '../components/ui'

const ClockIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 10 }),
    h('polyline', { points: '12 6 12 12 16 14' })
  ])
}
const XCircleIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 9 }),
    h('line', { x1: 9, y1: 9, x2: 15, y2: 15 }),
    h('line', { x1: 15, y1: 9, x2: 9, y2: 15 })
  ])
}
const PlayIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [
    h('polygon', { points: '6 4 20 12 6 20 6 4' })
  ])
}
const PauseIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [
    h('rect', { x: 6, y: 5, width: 4, height: 14, rx: 1 }),
    h('rect', { x: 14, y: 5, width: 4, height: 14, rx: 1 })
  ])
}

const router = useRouter()

const VIEW_W = 800
const VIEW_H = 400
const TYPE_AXIS_PADDING = 50

const entities = ref([])
const loading = ref(true)
const errorMessage = ref(null)

const dayIndex = ref(0)
const playing = ref(false)
const speedMs = ref(400)
let playTimer = null

const hoveredName = ref(null)

const range = computed(() => dateRangeOf(entities.value))
const totalDays = computed(() => {
  if (!range.value) return 0
  const ms = range.value.max.getTime() - range.value.min.getTime()
  return Math.max(1, Math.ceil(ms / (1000 * 60 * 60 * 24)) + 1)
})

const currentDate = computed(() => {
  if (!range.value) return new Date()
  return new Date(range.value.min.getTime() + dayIndex.value * 86400000)
})

const visibleEntities = computed(() => {
  if (!range.value) return []
  return entitiesVisibleAt(entities.value, currentDate.value)
})
const visibleCount = computed(() => visibleEntities.value.length)
const atEnd = computed(() => dayIndex.value >= totalDays.value - 1)
const hasData = computed(() => entities.value.length > 0 && totalDays.value > 0)

const typeList = computed(() => {
  const set = new Set()
  for (const e of entities.value) {
    set.add((e.type || 'Unknown').toUpperCase())
  }
  return Array.from(set).sort()
})

const PALETTE = [
  '#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#84cc16', '#f97316', '#ec4899', '#14b8a6',
]
const FALLBACK = '#9ca3af'

function colorForType(t) {
  const i = typeList.value.indexOf(t.toUpperCase())
  if (i < 0) return FALLBACK
  return PALETTE[i % PALETTE.length]
}

function yForType(t) {
  if (typeList.value.length === 0) return VIEW_H / 2
  const i = typeList.value.indexOf(t.toUpperCase())
  const usable = VIEW_H - 2 * TYPE_AXIS_PADDING
  if (typeList.value.length === 1) return VIEW_H / 2
  return TYPE_AXIS_PADDING + (i / (typeList.value.length - 1)) * usable
}

function xForDate(iso) {
  if (!range.value || !iso) return 0
  const ms = new Date(iso).getTime() - range.value.min.getTime()
  const total = range.value.max.getTime() - range.value.min.getTime() || 1
  return 50 + (ms / total) * (VIEW_W - 70)
}

function xForDayIndex(idx) {
  if (!range.value || totalDays.value <= 1) return 50
  const total = range.value.max.getTime() - range.value.min.getTime() || 1
  return 50 + (idx / (totalDays.value - 1)) * (total / total) * (VIEW_W - 70)
}

function radiusFor(mentions) {
  if (!mentions || mentions <= 0) return 4
  return Math.max(4, Math.min(14, 4 + Math.log2(mentions) * 2.5))
}

function formatDate(d) {
  if (!d) return ''
  const date = (d instanceof Date) ? d : new Date(d)
  if (isNaN(date.getTime())) return ''
  return date.toISOString().slice(0, 10)
}

function onSliderInput(e) {
  dayIndex.value = Number(e.target.value)
  if (atEnd.value) stopPlay()
}

function jumpToEnd() {
  dayIndex.value = totalDays.value - 1
  stopPlay()
}

function togglePlay() {
  if (playing.value) {
    stopPlay()
  } else {
    if (atEnd.value) dayIndex.value = 0
    startPlay()
  }
}

function startPlay() {
  if (playing.value) return
  playing.value = true
  playTimer = setInterval(() => {
    if (dayIndex.value >= totalDays.value - 1) {
      stopPlay()
      return
    }
    dayIndex.value += 1
  }, speedMs.value)
}

function stopPlay() {
  if (playTimer) {
    clearInterval(playTimer)
    playTimer = null
  }
  playing.value = false
}

function onSpeedChange() {
  if (playing.value) {
    stopPlay()
    startPlay()
  }
}

const loadAll = async () => {
  loading.value = true
  errorMessage.value = null
  try {
    const { data } = await timelineApi.get()
    entities.value = (data?.entity_timeline || []).filter((e) => e.first_seen)
    if (range.value) {
      dayIndex.value = 0
    }
  } catch (err) {
    console.error('Failed to load timeline:', err)
    errorMessage.value = 'Could not load the timeline. Please try again.'
  } finally {
    loading.value = false
  }
}

const goBack = () => {
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push({ name: 'Graph' })
  }
}

const goToEntity = (name) => {
  router.push({ name: 'EntityDetail', params: { name: encodeURIComponent(name) } })
}

onMounted(loadAll)
onUnmounted(stopPlay)
</script>

<style scoped>
.timeline-anim-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.anim-content {
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
  min-height: 50vh;
  color: var(--text-tertiary);
  gap: 1rem;
  text-align: center;
}

.controls {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 1rem;
  align-items: center;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.25rem;
  margin-bottom: 1.25rem;
}

.stats {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.stat-num { font-weight: 600; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.stat-label { color: var(--text-tertiary); }
.jump-btn { font-family: var(--font-mono); font-size: 0.6875rem; }

.slider-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.time-slider {
  width: 100%;
  accent-color: var(--primary);
}
.slider-bounds {
  display: flex;
  justify-content: space-between;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
}

.playback {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.75rem;
}
.play-btn.playing { background: var(--accent); border-color: var(--accent); }

.speed-control {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
}
.speed-control select {
  padding: 0.25rem 0.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-primary);
  cursor: pointer;
}

.body {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 1.25rem;
}

.map-card { padding: 1rem; }
.anim-svg { width: 100%; height: auto; display: block; }

.dot-group { cursor: pointer; }
.dot-group circle { transition: r 0.15s ease, opacity 0.15s ease, stroke-width 0.15s ease; }

.dot-label,
.y-label {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  fill: var(--text-primary);
  paint-order: stroke;
  stroke: var(--bg-primary);
  stroke-width: 3;
  stroke-linejoin: round;
}
.y-label {
  stroke-width: 2;
  fill: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-size: 10px;
}

.list-card { padding: 1rem; max-height: 480px; overflow-y: auto; }
.list-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 0.75rem;
  gap: 0.5rem;
}
.list-header h3 {
  font-family: var(--font-display);
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-primary);
  margin: 0;
}
.list-count {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-tertiary);
}
.list-empty {
  font-size: 0.8125rem;
  color: var(--text-tertiary);
  font-style: italic;
  text-align: center;
  padding: 1.5rem 0;
}

.entity-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.entity-item {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}
.entity-item:hover,
.entity-item.hovered { background: var(--bg-tertiary); }
.entity-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.entity-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.entity-name {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.entity-sub {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

.footer-hint {
  text-align: center;
  margin-top: 1rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

@media (max-width: 900px) {
  .body { grid-template-columns: 1fr; }
  .controls { grid-template-columns: 1fr; }
  .playback { justify-content: flex-start; }
}
@media (max-width: 600px) {
  .anim-content { padding: 1rem; }
}
</style>
