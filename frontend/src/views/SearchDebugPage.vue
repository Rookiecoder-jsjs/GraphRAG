<template>
  <div class="search-debug-page">
    <PageHeader
      :icon="SearchIcon"
      kicker="工作区 · 检索"
      title="搜索调试"
      subtitle="检索管线逐阶段透视——改写、通道召回、融合、重排序、扩展全程可见"
    />

    <div class="debug-content">
      <Card>
        <div class="debug-form">
          <input
            v-model="query"
            type="text"
            class="debug-input"
            placeholder="输入要透视的查询…"
            @keyup.enter="run"
            :disabled="loading"
          />
          <select v-model.number="topK" class="debug-topk" :disabled="loading" aria-label="返回条数">
            <option v-for="n in 20" :key="n" :value="n">{{ n }} 条</option>
          </select>
          <label class="debug-toggle">
            <input v-model="useGraphRag" type="checkbox" :disabled="loading" />
            图谱通道
          </label>
          <Button variant="primary" :loading="loading" :disabled="!query.trim()" @click="run">
            调试
          </Button>
        </div>
        <p class="debug-note">
          每次调试都是一次全价真实检索（绕过缓存，计入限流与并发配额），阶段计时即真实耗时。
        </p>
      </Card>

      <LoadingState v-if="loading" message="运行检索管线…" />

      <ErrorState
        v-else-if="runError"
        title="调试运行失败"
        description="检索管线执行时出了点问题。"
        @retry="run"
      />

      <EmptyState
        v-else-if="!result"
        :icon="SearchIcon"
        title="输入查询开始调试"
        description="每次运行返回管线的完整中间状态：哪个通道召回了什么、融合怎么排序、重排怎么取舍。"
      />

      <template v-else>
        <!-- ① 查询预处理 -->
        <Card title="① 查询预处理" :meta="`${debug.final_queries?.length ?? 0} 条有效查询`">
          <div class="rewrite-row">
            <span class="q-chip q-raw">{{ debug.raw_query }}</span>
            <span class="rewrite-arrow">→</span>
            <span class="q-chip q-rewritten">{{ debug.rewritten }}</span>
            <Tag shape="pill" :tone="debug.rewrite_applied ? 'primary' : 'muted'">
              {{ debug.rewrite_applied ? '改写生效' : '未改写' }}
            </Tag>
          </div>
          <div v-if="debug.variants?.length" class="chip-row">
            <span class="chip-row-label">多查询变体</span>
            <span v-for="v in debug.variants" :key="v" class="q-chip q-variant">{{ v }}</span>
          </div>
          <div v-if="debug.query_entities?.length" class="chip-row">
            <span class="chip-row-label">查询实体</span>
            <Tag v-for="e in debug.query_entities" :key="e.name" shape="pill" tone="cool">
              {{ e.name }}
            </Tag>
          </div>
        </Card>

        <!-- ② 通道召回 -->
        <Card title="② 通道召回" :meta="`${debug.channels?.length ?? 0} 个通道`">
          <div v-for="ch in debug.channels" :key="ch.label" class="channel-block">
            <div class="channel-head">
              <Tag shape="pill" tone="primary">{{ kindLabel(ch.kind) }}</Tag>
              <span class="channel-label">{{ ch.label }}</span>
              <span class="channel-count">{{ ch.hits.length }} 条命中</span>
            </div>
            <ul class="hit-list">
              <li v-for="(h, i) in ch.hits" :key="h.chunk_id + i" class="hit-row">
                <span class="hit-rank">{{ i + 1 }}</span>
                <span class="hit-main">
                  <span class="hit-doc">{{ docLabel(h) }}</span>
                  <span class="hit-preview">{{ h.preview || '（无内容）' }}</span>
                </span>
                <span v-if="h.score != null" class="hit-metric">score {{ fmtNum(h.score) }}</span>
                <span v-else-if="h.distance != null" class="hit-metric">dist {{ fmtNum(h.distance) }}</span>
              </li>
            </ul>
          </div>
          <EmptyState v-if="!debug.channels?.length" title="无通道输出" description="所有通道都没有召回。" />
        </Card>

        <!-- ③ RRF 融合 -->
        <Card title="③ RRF 融合" :meta="`${debug.fused?.length ?? 0} 条进入重排`">
          <ul class="hit-list">
            <li v-for="f in debug.fused" :key="f.chunk_id" class="hit-row">
              <span class="hit-rank">#{{ f.rank }}</span>
              <span class="hit-main">
                <span class="hit-doc">{{ docLabel(f) }}</span>
                <span class="hit-preview">{{ f.preview || '（无内容）' }}</span>
              </span>
              <span class="source-pills">
                <Tag v-for="s in f.sources" :key="s" shape="pill" tone="muted">{{ kindLabel(s) }}</Tag>
              </span>
              <span class="hit-metric">rrf {{ fmtNum(f.rrf_score) }}</span>
            </li>
          </ul>
        </Card>

        <!-- ④ 重排序 -->
        <Card title="④ 重排序" :meta="`取前 ${debug.seeds?.length ?? 0} 条为种子`">
          <div v-if="hasDegraded('rerank_fallback')" class="degraded-inline">
            重排序失败，已按 RRF 顺序兜底（无 relevance_score）。
          </div>
          <ul class="hit-list">
            <li v-for="s in debug.seeds" :key="s.chunk_id" class="hit-row">
              <span class="hit-rank">{{ s.rank }}</span>
              <span class="hit-main">
                <span class="hit-doc">{{ docLabel(s) }}</span>
                <span class="hit-preview">{{ s.preview || '（无内容）' }}</span>
              </span>
              <span v-if="s.relevance_score != null" class="hit-metric">
                rel {{ fmtNum(s.relevance_score) }}
              </span>
            </li>
          </ul>
        </Card>

        <!-- ⑤ 扩展 -->
        <Card title="⑤ 上下文扩展" :meta="`${debug.expanded?.length ?? 0} 条进入最终上下文`">
          <ul class="hit-list">
            <li v-for="(e, i) in debug.expanded" :key="e.chunk_id + i" class="hit-row">
              <Tag shape="pill" :tone="e.provenance === 'seed' ? 'primary' : 'muted'">
                {{ provLabel(e.provenance) }}
              </Tag>
              <span class="hit-main">
                <span class="hit-doc">{{ docLabel(e) }}</span>
                <span class="hit-preview">{{ e.preview || '（无内容）' }}</span>
              </span>
              <span v-if="e.relevance_score != null" class="hit-metric">
                rel {{ fmtNum(e.relevance_score) }}
              </span>
            </li>
          </ul>
        </Card>

        <!-- ⑥ 诊断 -->
        <Card title="⑥ 诊断" :meta="`总耗时 ${fmtNum(debug.diagnostics?.timing_s?.total ?? 0)}s`">
          <ul class="timing-list">
            <li v-for="(v, k) in debug.diagnostics?.timing_s" :key="k" class="timing-row" v-show="k !== 'total'">
              <span class="timing-name">{{ timingLabel(k) }}</span>
              <span class="timing-bar-track">
                <span class="timing-bar" :style="{ width: timingPct(v) + '%' }" />
              </span>
              <span class="timing-value">{{ fmtNum(v) }}s</span>
            </li>
          </ul>
          <div v-if="degraded.length" class="degraded-block">
            <span class="chip-row-label">降级事件</span>
            <Tag v-for="d in degraded" :key="d" shape="pill" tone="warm">{{ d }}</Tag>
          </div>
          <div class="config-line">
            top_k={{ debug.config?.top_k }} · 图谱通道={{ debug.config?.use_graph_rag ? '开' : '关' }}
            · 图谱模式={{ debug.config?.graph_mode }} · recall_k={{ debug.config?.recall_k }}
          </div>
        </Card>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { h } from 'vue'
import { searchApi } from '../api/search'
import { PageHeader, Card, Tag, Button, EmptyState, LoadingState, ErrorState } from '../components/ui'

// FEAT-024: 检索调试台。一次调试 = 一次绕过缓存的全价管线运行，
// 返回 POST /api/search/debug 的 debug 字典（形状由后端
// app/services/retrieval/debug.py 拥有）。页面只读渲染，无初始加载。

const SearchIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 11, cy: 11, r: 8 }),
    h('line', { x1: 21, y1: 21, x2: 16.65, y2: 16.65 })
  ])
}

const route = useRoute()

const query = ref('')
const topK = ref(5)
const useGraphRag = ref(false)
const loading = ref(false)
const runError = ref(false)
const result = ref(null)

const debug = computed(() => result.value?.debug || {})
const degraded = computed(() => debug.value.diagnostics?.degraded || [])
const hasDegraded = (key) => degraded.value.includes(key)
// FEAT-026: 来自 /search?tag= 的范围限定（只读透传）。
const routeTag = computed(() => String(route.query.tag || '').trim() || null)

const run = async () => {
  const q = query.value.trim()
  if (!q || loading.value) return
  loading.value = true
  runError.value = false
  try {
    // FEAT-026: 搜索页带标签跳转时透传，调试的才是"同一条检索"。
    const { data } = await searchApi.searchDebug(q, topK.value, useGraphRag.value, routeTag.value)
    result.value = data
  } catch (error) {
    console.error('Search debug failed:', error)
    runError.value = true
    result.value = null
  } finally {
    loading.value = false
  }
}

// 从搜索页"调试模式"入口带查询跳转过来时自动跑一次。
onMounted(() => {
  const q = String(route.query.q || '').trim()
  if (q) {
    query.value = q
    run()
  }
})

// titles: document_id → 显示名（后端映射；bm25-only 命中无 document_id，
// 兜底显示 chunk_id）。
const docLabel = (entry) => {
  const t = result.value?.titles?.[entry.document_id]
  return t || entry.chunk_id || '（未知 chunk）'
}

const KIND_LABELS = {
  vector: '向量', bm25: '关键词', graph: '图谱',
  vector_1: '向量·变体1', vector_2: '向量·变体2', vector_3: '向量·变体3',
  bm25_1: '关键词·变体1', bm25_2: '关键词·变体2', bm25_3: '关键词·变体3',
}
const kindLabel = (kindOrLabel) =>
  KIND_LABELS[kindOrLabel] || (kindOrLabel.startsWith('vector_') ? '向量·变体'
    : kindOrLabel.startsWith('bm25_') ? '关键词·变体' : kindOrLabel)

const PROV_LABELS = { seed: '种子', neighbour: '邻居', sibling: '同节' }
const provLabel = (p) => PROV_LABELS[p] || p

const TIMING_LABELS = {
  queue: '排队', rewrite: '预处理', embed: '嵌入', retrieve: '召回+融合',
  rerank: '重排序', expand: '扩展', enrich: '富集',
}
const timingLabel = (k) => TIMING_LABELS[k] || k

const timingPct = (value) => {
  const total = debug.value.diagnostics?.timing_s?.total || 0
  if (!total || typeof value !== 'number' || value <= 0) return 0
  return Math.max(1, Math.min(100, Math.round((value / total) * 100)))
}

const fmtNum = (v) => {
  if (typeof v !== 'number') return '—'
  return Number.isInteger(v) ? String(v) : v.toFixed(3)
}
</script>

<style scoped>
/* 布局壳的 .main-content 是 overflow:hidden——每个页面自带滚动容器
   （SearchPage/DocumentsPage 同款）：根 height:100% + 内容区
   flex:1 + overflow-y:auto，缺一个内容超屏就会被裁掉滚不动。 */
.search-debug-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}
.debug-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 0 2rem 2rem;
}

.debug-form { display: flex; gap: 0.625rem; align-items: center; flex-wrap: wrap; }
.debug-input {
  flex: 1; min-width: 12rem; padding: 0.5rem 0.875rem;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg); color: var(--text-primary); font-size: 0.875rem;
}
.debug-input:focus { outline: none; border-color: var(--primary); }
.debug-topk {
  padding: 0.5rem 0.5rem; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg); color: var(--text-primary); font-size: 0.8125rem;
}
.debug-toggle {
  display: flex; align-items: center; gap: 0.375rem;
  font-size: 0.8125rem; color: var(--text-secondary);
  user-select: none; cursor: pointer;
}
.debug-toggle input { accent-color: var(--primary); }
.debug-note {
  margin-top: 0.625rem; font-size: 0.75rem; color: var(--text-tertiary);
}

.rewrite-row { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.rewrite-arrow { color: var(--text-tertiary); font-size: 0.875rem; }
.q-chip {
  padding: 0.25rem 0.625rem; border-radius: 8px; font-size: 0.8125rem;
  max-width: 100%;
}
.q-raw { background: var(--surface-2, var(--surface)); border: 1px dashed var(--border); color: var(--text-secondary); }
.q-rewritten { background: var(--primary-light); color: var(--text-primary); border: 1px solid var(--primary); }
.q-variant { background: var(--surface); border: 1px solid var(--border); color: var(--text-secondary); }

.chip-row {
  display: flex; align-items: center; gap: 0.375rem; flex-wrap: wrap;
  margin-top: 0.625rem;
}
.chip-row-label {
  font-size: 0.6875rem; color: var(--text-tertiary); margin-right: 0.25rem;
}

.channel-block { padding: 0.5rem 0; }
.channel-block + .channel-block { border-top: 1px solid var(--border); }
.channel-head { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem; }
.channel-label { font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-secondary); }
.channel-count { font-size: 0.6875rem; color: var(--text-tertiary); margin-left: auto; }

.hit-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.375rem; }
.hit-row {
  display: flex; align-items: center; gap: 0.625rem;
  padding: 0.375rem 0.5rem; border-radius: 8px;
  background: var(--surface);
}
.hit-rank {
  font-family: var(--font-mono); font-size: 0.6875rem;
  color: var(--text-tertiary); min-width: 1.75rem; text-align: right;
  font-variant-numeric: tabular-nums;
}
.hit-main { display: flex; flex-direction: column; gap: 0.125rem; min-width: 0; flex: 1; }
.hit-doc {
  font-size: 0.75rem; color: var(--text-secondary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.hit-preview {
  font-size: 0.8125rem; color: var(--text-primary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.hit-metric {
  font-family: var(--font-mono); font-size: 0.6875rem;
  color: var(--text-tertiary); flex-shrink: 0; font-variant-numeric: tabular-nums;
}
.source-pills { display: flex; gap: 0.25rem; flex-shrink: 0; }

.degraded-inline {
  font-size: 0.8125rem; color: var(--warning, #b45309);
  margin-bottom: 0.625rem;
}
.degraded-block {
  display: flex; align-items: center; gap: 0.375rem; flex-wrap: wrap;
  margin-top: 0.875rem;
}

.timing-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.375rem; }
.timing-row { display: flex; align-items: center; gap: 0.625rem; }
.timing-name { width: 5.5rem; font-size: 0.75rem; color: var(--text-secondary); flex-shrink: 0; }
.timing-bar-track {
  flex: 1; height: 8px; border-radius: 4px;
  background: var(--surface-2, var(--surface)); overflow: hidden;
}
.timing-bar {
  display: block; height: 100%; border-radius: 4px;
  background: linear-gradient(90deg, var(--primary) 0%, var(--primary-dark, var(--primary)) 100%);
  min-width: 2px; transition: width var(--transition-fast);
}
.timing-value {
  font-family: var(--font-mono); font-size: 0.6875rem;
  color: var(--text-tertiary); width: 3.5rem; text-align: right;
  font-variant-numeric: tabular-nums;
}

.config-line {
  margin-top: 0.875rem; font-family: var(--font-mono);
  font-size: 0.6875rem; color: var(--text-tertiary);
}

@media (max-width: 640px) {
  .hit-row { flex-wrap: wrap; }
  .source-pills { order: 3; }
  .timing-name { width: 4rem; }
}
</style>
