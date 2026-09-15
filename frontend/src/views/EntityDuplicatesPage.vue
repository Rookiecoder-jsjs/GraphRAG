<template>
  <div class="entity-duplicates-page">
    <PageHeader
      :icon="Share2Icon"
      kicker="分析 · 图谱"
      title="实体查重"
      subtitle="疑似重复实体建议——大小写/标点变体分组，合并需人工确认"
    />

    <div class="dup-content">
      <LoadingState v-if="loading" message="扫描实体…" />

      <ErrorState
        v-else-if="loadError"
        title="加载失败"
        description="扫描疑似重复实体时出了点问题。"
        @retry="load"
      />

      <EmptyState
        v-else-if="!groups.length"
        :icon="Share2Icon"
        title="未发现疑似重复"
        description="当前图谱里没有按大小写/标点规则聚出的重复组。上传更多文档后可以再扫一次。"
      />

      <template v-else>
        <div class="dup-summary">
          <Tag shape="pill" tone="warm">{{ groups.length }} 组建议</Tag>
          <span class="dup-summary-text">共扫描 {{ scanned }} 个实体 · 合并会自动把旧名记为别名</span>
        </div>

        <Card
          v-for="g in groups"
          :key="g.key"
          :title="`${g.members.length} 个候选：${g.members.map(m => m.name).join(' / ')}`"
          :meta="reasonLabel(g.reason)"
        >
          <div class="member-row">
            <label
              v-for="m in g.members"
              :key="m.name"
              class="member-card"
              :class="{ chosen: picks[g.key] === m.name }"
            >
              <input
                v-model="picks[g.key]"
                type="radio"
                :name="`group-${g.key}`"
                :value="m.name"
              />
              <span class="member-name">{{ m.name }}</span>
              <span class="member-meta">
                {{ typeLabel(m.type) }} · {{ m.mention_count }} 提及 · {{ m.doc_count }} 文档
              </span>
            </label>
          </div>
          <div class="group-actions">
            <span v-if="mergeError[g.key]" class="group-error">{{ mergeError[g.key] }}</span>
            <Button
              variant="primary"
              size="sm"
              :loading="merging[g.key]"
              :disabled="!picks[g.key]"
              @click="mergeGroup(g)"
            >
              合并其余到所选
            </Button>
          </div>
        </Card>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onActivated, h } from 'vue'
import { graphApi } from '../api/graph'
import { PageHeader, Card, Tag, Button, EmptyState, LoadingState, ErrorState } from '../components/ui'
import { useToast } from '../composables/toast'

// FEAT-025: 查重建议页。组由后端 case/punct 规则算出（仅建议）；合并
// 逐个调用既有 POST /api/graph/entities/merge（复用契约，不加批量 API），
// 每次 merge 成功后后端自动把被并名记为别名。keep-alive 下双钩子刷新。

const Share2Icon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 18, cy: 5, r: 3 }),
    h('circle', { cx: 6, cy: 12, r: 3 }),
    h('circle', { cx: 18, cy: 19, r: 3 }),
    h('line', { x1: 8.59, y1: 13.51, x2: 15.42, y2: 17.49 }),
    h('line', { x1: 15.41, y1: 6.51, x2: 8.59, y2: 10.49 })
  ])
}

const toast = useToast()

const groups = ref([])
const scanned = ref(0)
const loading = ref(true)
const loadError = ref(false)

// 每组选中的主实体名（key 为组 key；radio v-model 需要独立可写对象）
const picks = reactive({})
const merging = reactive({})
const mergeError = reactive({})

const load = async () => {
  loading.value = true
  loadError.value = false
  try {
    const { data } = await graphApi.getDuplicates()
    groups.value = data?.groups || []
    scanned.value = data?.scanned || 0
    // 默认把提及数最多的成员选为主实体（后端已按总提及降序，取组内首位）。
    for (const g of groups.value) {
      if (!picks[g.key]) {
        const top = [...g.members].sort((a, b) => b.mention_count - a.mention_count)[0]
        if (top) picks[g.key] = top.name
      }
    }
  } catch (error) {
    console.error('Failed to load duplicates:', error)
    loadError.value = true
  } finally {
    loading.value = false
  }
}

// Layout caches pages in <keep-alive>: returning here fires onActivated
// without re-running onMounted — reload so fresh merges are reflected.
onMounted(load)
onActivated(load)

const remainingGroups = computed(() => groups.value.length)

const mergeGroup = async (g) => {
  const keep = picks[g.key]
  if (!keep) return
  merging[g.key] = true
  mergeError[g.key] = ''
  const others = g.members.map(m => m.name).filter(n => n !== keep)
  try {
    for (const source of others) {
      try {
        await graphApi.mergeEntities(source, keep)
      } catch (e) {
        // 部分失败后重试：已被并掉的源再并会 404——视为已完成
        if (e?.response?.status !== 404) throw e
      }
    }
    toast.success(`已合并 ${others.length} 个实体到「${keep}」`)
    groups.value = groups.value.filter(x => x.key !== g.key)
    if (!groups.value.length && remainingGroups.value === 0) {
      toast.success('所有建议都已处理完毕。')
    }
  } catch (error) {
    console.error('Merge failed:', error)
    // 500 未被 FastAPI 处理时 body 是纯文本（无 detail 字段），兜底显示原文
    const data = error?.response?.data
    mergeError[g.key] =
      (typeof data?.detail === 'string' && data.detail) ||
      (typeof data === 'string' && data.trim()) ||
      error?.message ||
      '合并失败，请重试。'
  } finally {
    merging[g.key] = false
  }
}

const reasonLabel = (reason) =>
  reason === 'case' ? '仅大小写不同' : reason === 'punct' ? '仅空格/标点不同' : reason

const TYPE_LABELS = {
  PERSON: '人物', ORGANIZATION: '组织', LOCATION: '地点', CONCEPT: '概念',
  EVENT: '事件', TIME: '时间', OTHER: '其他',
}
const typeLabel = (t) => TYPE_LABELS[t] || t || '未知'
</script>

<style scoped>
/* 同 SearchDebugPage：布局壳不滚动，页面自带滚动容器。 */
.entity-duplicates-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}
.dup-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 0 2rem 2rem;
}

.dup-summary {
  display: flex; align-items: center; gap: 0.625rem; flex-wrap: wrap;
}
.dup-summary-text { font-size: 0.8125rem; color: var(--text-tertiary); }

.member-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 0.625rem;
}
.member-card {
  display: flex; flex-direction: column; gap: 0.25rem;
  padding: 0.625rem 0.75rem; border: 1px solid var(--border);
  border-radius: 10px; background: var(--surface); cursor: pointer;
  transition: border-color var(--transition-fast);
}
.member-card:hover { border-color: var(--primary-light); }
.member-card.chosen { border-color: var(--primary); box-shadow: 0 0 0 1px var(--primary); }
.member-card input { accent-color: var(--primary); }
.member-name {
  font-family: var(--font-display); font-size: 0.9375rem;
  font-weight: 500; color: var(--text-primary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.member-meta { font-size: 0.75rem; color: var(--text-tertiary); }

.group-actions {
  display: flex; align-items: center; justify-content: flex-end; gap: 0.625rem;
  margin-top: 0.875rem;
}
.group-error { font-size: 0.8125rem; color: var(--danger, #b91c1c); }

@media (max-width: 640px) {
  .member-row { grid-template-columns: 1fr; }
}
</style>
