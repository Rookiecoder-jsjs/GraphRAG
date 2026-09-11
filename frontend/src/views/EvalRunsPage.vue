<template>
  <div class="eval-runs-page">
    <PageHeader
      :icon="TrendingUpIcon"
      kicker="协作 · 评测"
      title="评测报告"
      subtitle="eval.runner --save 的历史趋势，军规②的数字证据链"
    />

    <div class="runs-content">
      <LoadingState v-if="loading" message="加载评测报告…" />

      <ErrorState
        v-else-if="loadError"
        title="加载评测报告失败"
        description="获取评测运行列表时出了点问题。"
        @retry="load"
      />

      <template v-else>
        <section class="run-stats">
          <Stat
            variant="icon"
            tone="primary"
            :icon="TrendingUpIcon"
            :value="runs.length"
            label="总运行次数"
          />
          <Stat
            variant="icon"
            tone="warm"
            :icon="FlaskIcon"
            :value="latest?.total_cases ?? 0"
            label="最新用例数"
          />
          <Stat
            variant="icon"
            tone="cool"
            :icon="CheckIcon"
            :value="fmtStat(latestMetricValue)"
            :label="`最新 ${selectedMetric || '—'}`"
          />
        </section>

        <EmptyState
          v-if="runs.length === 0"
          :icon="TrendingUpIcon"
          title="暂无评测报告"
          description="运行 python -m eval.runner --user-id 1 --save 生成第一份报告。"
        />

        <template v-else>
          <Card title="指标趋势" meta="按运行时间从左到右">
            <div class="metric-picker">
              <label class="metric-label" for="metric-select">指标</label>
              <select id="metric-select" v-model="selectedMetric" class="metric-select">
                <option v-for="m in metricOptions" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div class="bar-chart">
              <div
                v-for="r in trendRuns"
                :key="r.id"
                class="bar-col"
                :title="`${runTitle(r)}：${fmtStat(metricOf(r))}`"
              >
                <div class="bar-count">{{ fmtStat(metricOf(r)) }}</div>
                <div class="bar" :style="{ height: barHeightPct(metricOf(r)) + '%' }" />
                <div class="bar-label">{{ shortDate(r) }}</div>
              </div>
            </div>
            <div class="figure-caption">
              <span class="fig-num">图 1</span>
              <span>{{ selectedMetric }} 随运行次数的变化。</span>
            </div>
          </Card>

          <Card title="运行记录" :meta="`共 ${runs.length} 次`">
            <ul class="run-list">
              <li v-for="r in runs" :key="r.id" class="run-row">
                <div class="run-main">
                  <span class="run-title">
                    {{ r.label || `运行 #${r.id}` }}
                    <span class="run-time">{{ formatDate(r.created_at) }}</span>
                  </span>
                  <span class="run-meta">
                    <Tag shape="pill" :tone="r.mode === 'no-llm' ? 'muted' : 'primary'">
                      {{ r.mode === 'no-llm' ? '仅检索' : '含生成' }}
                    </Tag>
                    <Tag shape="pill" tone="muted">{{ r.total_cases }} 用例</Tag>
                    <Tag shape="pill" tone="muted">{{ fmtStat(r.summary?.elapsed_seconds) }}s</Tag>
                    <Tag
                      v-for="(badge, i) in configBadges(r)"
                      :key="i"
                      shape="pill"
                      tone="muted"
                    >
                      {{ badge }}
                    </Tag>
                  </span>
                </div>
                <div class="run-metrics">
                  <span
                    v-for="col in metricColumns"
                    :key="col"
                    class="metric-cell"
                    :title="col"
                  >
                    <span class="metric-name">{{ col }}</span>
                    <span class="metric-value">{{ fmtMetric(r, col) }}</span>
                  </span>
                </div>
              </li>
            </ul>
          </Card>
        </template>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import { evalApi } from '../api/eval'
import { PageHeader, Card, Tag, Stat, EmptyState, LoadingState, ErrorState } from '../components/ui'
import { TrendingUpIcon, FlaskIcon, CheckIcon } from '../components/ui/icons'

// FEAT-021: 评测运行历史页。数据由 `eval.runner --save` 写入 eval_runs 表；
// 本页只读。keep-alive 缓存下必须 onMounted + onActivated 双钩子。

const runs = ref([])
const loading = ref(true)
const loadError = ref(false)
const selectedMetric = ref('')

// 指标默认优先级：军规②最关心的检索质量指标排在前面。
const METRIC_PREFERENCE = ['recall@5', 'mrr', 'ndcg@5', 'hit@5']
const metricColumns = ['hit@1', 'recall@5', 'mrr']

const load = async () => {
  loading.value = true
  loadError.value = false
  try {
    const { data } = await evalApi.listRuns()
    runs.value = data || []
    if (!selectedMetric.value && runs.value.length) {
      selectedMetric.value = defaultMetric()
    }
  } catch (error) {
    console.error('Failed to load eval runs:', error)
    loadError.value = true
  } finally {
    loading.value = false
  }
}
// Layout caches pages in <keep-alive>: returning here fires onActivated
// without re-running onMounted — reload, or a just-saved run goes stale.
onMounted(load)
onActivated(load)

const latest = computed(() => runs.value[0] || null)

// Union of `<metric>_mean` keys across runs, minus the suffix. Elapsed time
// is excluded implicitly (it has no `_mean` form).
const metricOptions = computed(() => {
  const names = new Set()
  for (const r of runs.value) {
    for (const key of Object.keys(r.summary || {})) {
      if (key.endsWith('_mean')) names.add(key.slice(0, -'_mean'.length))
    }
  }
  return [...names].sort()
})

const defaultMetric = () => {
  const available = metricOptions.value
  for (const pref of METRIC_PREFERENCE) {
    if (available.includes(pref)) return pref
  }
  return available[0] || ''
}

// API returns id DESC; the chart renders chronological left→right.
const trendRuns = computed(() => [...runs.value].reverse())

const metricOf = (r) => r.summary?.[`${selectedMetric.value}_mean`]
const latestMetricValue = computed(() => (latest.value ? metricOf(latest.value) : null))

const barHeightPct = (value) => {
  const values = trendRuns.value.map(metricOf).filter((v) => typeof v === 'number')
  const max = Math.max(1, ...values)
  if (typeof value !== 'number' || value <= 0) return 4
  return Math.max(8, Math.round((value / max) * 100))
}

const fmtStat = (value) => {
  if (typeof value !== 'number') return '—'
  return Number.isInteger(value) ? String(value) : value.toFixed(3)
}

const fmtMetric = (r, name) => {
  const v = r.summary?.[`${name}_mean`]
  return typeof v === 'number' ? v.toFixed(3) : '-'
}

const configBadges = (r) => {
  const cfg = r.config || {}
  const badges = []
  if (cfg.use_graph_rag) badges.push('graph-rag')
  if (Array.isArray(cfg.k_values)) badges.push(`k=${cfg.k_values.join('/')}`)
  if (cfg.gold_dir) badges.push(`gold: ${cfg.gold_dir}`)
  return badges
}

const runTitle = (r) => r.label || `运行 #${r.id}`

const shortDate = (r) => {
  const d = r.created_at ? new Date(r.created_at) : null
  if (!d || Number.isNaN(d.getTime())) return `#${r.id}`
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const formatDate = (dateStr) => {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toLocaleString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch {
    return dateStr
  }
}
</script>

<style scoped>
/* 布局壳的 .main-content 不滚动——页面自带滚动容器（全站统一模式）。 */
.eval-runs-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}
.runs-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 0 2rem 2rem;
}
.run-stats {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
}
.metric-picker {
  display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.875rem;
}
.metric-label { font-size: 0.8125rem; color: var(--text-secondary); }
.metric-select {
  padding: 0.35rem 0.6rem; border: 1px solid var(--border);
  border-radius: 8px; background: var(--bg); color: var(--text-primary);
  font-size: 0.8125rem;
}
.metric-select:focus { outline: none; border-color: var(--primary); }

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
.figure-caption {
  display: flex; gap: 0.5rem; align-items: baseline;
  margin-top: 0.75rem; font-size: 0.75rem; color: var(--text-tertiary);
}
.fig-num { font-family: var(--font-mono); color: var(--text-secondary); }

.run-list {
  list-style: none; margin: 0; padding: 0;
  display: flex; flex-direction: column; gap: 0.5rem;
}
.run-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; padding: 0.75rem 0.875rem;
  border: 1px solid var(--border); border-radius: 10px;
  background: var(--surface);
}
.run-main {
  display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; flex: 1;
}
.run-title {
  font-family: var(--font-display); font-size: 0.9375rem;
  font-weight: 500; color: var(--text-primary);
}
.run-time {
  margin-left: 0.5rem; font-family: var(--font-mono);
  font-size: 0.6875rem; font-weight: 400; color: var(--text-tertiary);
}
.run-meta { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.run-metrics {
  display: flex; gap: 1rem; flex-shrink: 0;
}
.metric-cell {
  display: flex; flex-direction: column; align-items: flex-end; gap: 0.125rem;
}
.metric-name {
  font-size: 0.625rem; color: var(--text-tertiary);
}
.metric-value {
  font-family: var(--font-mono); font-size: 0.8125rem;
  color: var(--text-primary); font-variant-numeric: tabular-nums;
}
@media (max-width: 640px) {
  .run-stats { grid-template-columns: 1fr; }
  .run-row { flex-direction: column; align-items: stretch; }
  .run-metrics { justify-content: flex-start; }
}
</style>
