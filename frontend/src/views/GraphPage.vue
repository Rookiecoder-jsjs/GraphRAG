<template>
  <div class="graph-page">
    <PageHeader
      :icon="GraphIcon"
      kicker="Graph · Live"
      title="Knowledge Graph"
      subtitle="Interactive network visualization"
    >
      <template #actions>
        <div class="stats">
          <Stat variant="inline" :value="stats.entities" label="ENTITIES" />
          <div class="stat-divider" />
          <Stat variant="inline" :value="stats.relations" label="RELATIONS" />
        </div>
        <Button
          variant="secondary"
          size="sm"
          :icon="ClockIcon"
          icon-position="left"
          @click="goToTimelineAnimation"
          title="Watch entities appear over time"
        >
          Timeline Animation
        </Button>
      </template>
    </PageHeader>

    <div class="search-section">
      <div class="search-bar">
        <div class="search-input-wrapper">
          <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            v-model="query"
            type="text"
            class="search-input"
            placeholder="Search entities or concepts..."
            @keyup.enter="handleSearch"
          />
        </div>
        <Button
          variant="primary"
          size="sm"
          :icon="BoltIcon"
          :loading="loading"
          @click="handleSearch"
          class="search-btn-trigger"
        >
          Analyze
        </Button>
        <Button
          variant="ghost"
          size="sm"
          :icon="GlobeIcon"
          icon-position="only"
          @click="handleReset"
          title="Show full graph"
        />
      </div>
    </div>

    <div class="graph-content">
      <EmptyState
        v-if="nodes.length === 0 && !loading"
        :icon="GraphIcon"
        title="No Graph Data"
        description="Upload documents to initialize the knowledge graph"
        decor="contour"
        decor-tone="primary"
      />

      <template v-else>
        <div class="graph-panel-container">
          <GraphPanel
            :graph-data="graphData"
            :loading="loading"
            @refresh="loadFullGraph"
            @node-click="handleNodeClick"
            @edge-click="handleEdgeClick"
          />
        </div>

        <div v-if="selectedEdge" class="relationship-panel">
          <div class="panel-header">
            <h3 class="panel-title">Relationship Details</h3>
            <Button variant="ghost" size="sm" icon-position="only" @click="closeEdgePanel" class="panel-close-btn">&times;</Button>
          </div>
          <div class="panel-content">
            <div class="relation-header">
              <span class="source-name">{{ selectedEdge.source_label || selectedEdge.source }}</span>
              <span class="relation-arrow">&rarr;</span>
              <span class="target-name">{{ selectedEdge.target_label || selectedEdge.target }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Type:</span>
              <span class="detail-value">{{ selectedEdge.type || 'RELATED_TO' }}</span>
            </div>
            <div class="detail-row" v-if="selectedEdge.label">
              <span class="detail-label">Label:</span>
              <span class="detail-value">{{ selectedEdge.label }}</span>
            </div>
            <div class="detail-row" v-if="selectedEdge.id">
              <span class="detail-label">Edge ID:</span>
              <span class="detail-value uuid">{{ selectedEdge.id }}</span>
            </div>
          </div>
        </div>

        <div v-if="selectedEntity" class="entity-edit-panel">
          <div class="panel-header">
            <h3 class="panel-title">Edit Entity</h3>
            <Button variant="ghost" size="sm" icon-position="only" @click="closeEntityPanel" class="panel-close-btn">&times;</Button>
          </div>

          <Button
            variant="secondary"
            size="sm"
            block
            :icon="ExternalLinkIcon"
            icon-position="left"
            @click="goToEntityDetail(selectedEntity)"
            class="detail-link-btn"
          >
            Open full detail page
          </Button>

          <div class="entity-hero">
            <div class="entity-name-row">
              <div class="entity-name">{{ selectedEntity.name }}</div>
              <span
                class="category-badge"
                :style="{ background: `var(${categoryColorToken(selectedEntity.entity_type || selectedEntity.type || selectedEntity.node_type)})` }"
              >{{ categoryLabel(selectedEntity.entity_type || selectedEntity.type || selectedEntity.node_type) }}</span>
            </div>
            <div class="entity-meta">
              <div class="meta-row">
                <span class="meta-label">类型</span>
                <span class="meta-value">
                  <span v-if="selectedEntity.entity_type || selectedEntity.type" class="type-chip">{{ selectedEntity.entity_type || selectedEntity.type }}</span>
                  <span v-else class="type-chip empty">未设置 — 在下方下拉框中选择</span>
                </span>
              </div>
              <div class="meta-row meta-row-col">
                <span class="meta-label">描述</span>
                <span class="meta-value description" v-if="selectedEntity.description || selectedEntity.properties?.description">{{ selectedEntity.description || selectedEntity.properties?.description }}</span>
                <span class="meta-value description empty" v-else>无描述 — 在下方文本框中添加</span>
              </div>
            </div>
          </div>

          <div class="entity-section">
            <div class="entity-section-title">Basics</div>

            <div class="form-group">
              <label class="form-label">Type</label>
              <select v-model="editingEntityType" class="form-input">
                <option v-for="opt in ENTITY_TYPE_OPTIONS" :key="opt" :value="opt">
                  {{ opt }}
                </option>
              </select>
            </div>

            <div class="form-group">
              <label class="form-label">Description</label>
              <textarea
                v-model="editingEntityDescription"
                class="form-input"
                rows="3"
                placeholder="(no description)"
              />
            </div>

            <div v-if="entityEditError" class="error-message">{{ entityEditError }}</div>
            <div v-if="entityEditSuccess" class="success-message">{{ entityEditSuccess }}</div>

            <div class="actions-row">
              <Button variant="secondary" size="sm" @click="closeEntityPanel">Cancel</Button>
              <Button
                variant="primary"
                size="sm"
                :loading="entityEditSaving"
                :disabled="!entityEditDirty"
                @click="saveEntityEdits"
              >Save changes</Button>
            </div>

            <div class="merge-toggle-row">
              <Button
                variant="outline-dashed"
                size="sm"
                block
                @click="merging = !merging"
                :title="merging ? 'Cancel merge' : 'Merge this entity into another'"
              >
                {{ merging ? '× Cancel merge' : '⌄ Merge into another entity…' }}
              </Button>
            </div>
          </div>

          <div v-if="merging" class="entity-section">
            <div class="entity-section-title">Merge</div>
            <div class="merge-hint">Combine this entity with another. The other one will be removed and all its edges will be redirected.</div>

            <div class="form-group">
              <label class="form-label">Target entity</label>
              <input
                v-model="mergeTargetName"
                class="form-input"
                placeholder="Target entity name (must exist)"
              />
            </div>

            <div v-if="mergeError" class="error-message">{{ mergeError }}</div>

            <div v-if="mergePill" class="merge-target-pill">
              <span class="pill-label">Will merge into:</span>
              <span class="pill-name">{{ mergePill.name }}</span>
              <Button variant="ghost" size="sm" icon-position="only" @click="clearMergePill" class="pill-clear-btn">&times;</Button>
            </div>

            <Button
              variant="primary"
              size="sm"
              :loading="mergeSaving"
              :disabled="!mergePill || !mergePill.name || mergePill.name === selectedEntity?.name"
              @click="confirmMerge"
              block
            >
              Merge into "{{ mergePill?.name || mergeTargetName }}"
            </Button>
          </div>

          <div class="entity-section danger">
            <div class="entity-section-title">Danger zone</div>
            <p class="danger-text">Deleting this entity removes it from the graph and severs all its relationships. This cannot be undone.</p>
            <Button variant="outline-danger" size="sm" @click="requestDelete">Delete entity</Button>
          </div>
        </div>

        <div v-if="confirmDelete" class="confirm-overlay" @click.self="cancelDelete">
          <div class="confirm-dialog">
            <h3 class="confirm-title">Delete "{{ selectedEntity?.name }}"?</h3>
            <p class="confirm-text">This permanently removes the entity and all its relationships from your knowledge graph.</p>
            <div class="confirm-actions">
              <Button variant="secondary" size="sm" @click="cancelDelete">Cancel</Button>
              <Button
                variant="danger"
                size="sm"
                :loading="deleteSaving"
                @click="confirmDeleteEntity"
              >Yes, delete</Button>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, computed, h } from 'vue'
import { useRouter } from 'vue-router'
import { graphApi } from '../api/graph'
import { categoryLabel, categoryColorToken, CATEGORY_COLOR_TOKEN } from '../utils/categorize'
import { PageHeader, Button, Tag, Stat, EmptyState } from '../components/ui'
import GraphPanel from '../components/GraphPanel.vue'

const GraphIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 18, cy: 5, r: 3 }),
    h('circle', { cx: 6, cy: 12, r: 3 }),
    h('circle', { cx: 18, cy: 19, r: 3 }),
    h('line', { x1: 8.59, y1: 13.51, x2: 15.42, y2: 17.49 }),
    h('line', { x1: 15.41, y1: 6.51, x2: 8.59, y2: 10.49 })
  ])
}
const ClockIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 10 }),
    h('polyline', { points: '12 6 12 12 16 14' })
  ])
}
const BoltIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('polygon', { points: '13 2 3 14 12 14 11 22 21 10 12 10 13 2' })
  ])
}
const GlobeIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 10 }),
    h('line', { x1: 2, y1: 12, x2: 22, y2: 12 }),
    h('path', { d: 'M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z' })
  ])
}
const ExternalLinkIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6' }),
    h('polyline', { points: '15 3 21 3 21 9' }),
    h('line', { x1: 10, y1: 14, x2: 21, y2: 3 })
  ])
}

const router = useRouter()

const ENTITY_TYPE_OPTIONS = ['PERSON', 'ORGANIZATION', 'LOCATION', 'CONCEPT', 'EVENT', 'TIME', 'OTHER']

const query = ref('')
const loading = ref(false)
const nodes = ref([])
const edges = ref([])
const stats = ref({ entities: 0, relations: 0 })
const isShowingFullGraph = ref(true)

const selectedEdge = ref(null)
const selectedEntity = ref(null)
const editingEntityType = ref('')
const editingEntityDescription = ref('')
const entityEditError = ref('')
const entityEditSuccess = ref('')
const entityEditSaving = ref(false)
const originalEntityType = ref('')
const originalEntityDescription = ref('')

const merging = ref(false)
const mergeTargetName = ref('')
const mergePill = ref(null)
const mergeError = ref('')
const mergeSaving = ref(false)

const confirmDelete = ref(false)
const deleteSaving = ref(false)

let mergeLookupTimer = null

const graphData = computed(() => ({ nodes: nodes.value, edges: edges.value }))

const loadFullGraph = async () => {
  loading.value = true
  try {
    const { data } = await graphApi.getFullGraph()
    nodes.value = data.nodes || []
    edges.value = data.edges || []
    // 后端 stats 字段缺失或为 0 时，用 nodes/edges 长度兜底
    const backendStats = data.stats || {}
    stats.value = (backendStats.entities > 0 || backendStats.relations > 0)
      ? backendStats
      : { entities: nodes.value.length, relations: edges.value.length }
    isShowingFullGraph.value = true
  } catch (err) {
    console.error('Failed to load full graph:', err)
    nodes.value = []
    edges.value = []
    stats.value = { entities: 0, relations: 0 }
  } finally {
    loading.value = false
  }
}

const handleSearch = async () => {
  const q = query.value.trim()
  if (!q) return
  loading.value = true
  try {
    const { data } = await graphApi.search(q)
    nodes.value = data.nodes || []
    edges.value = data.edges || []
    const backendStats = data.stats || {}
    stats.value = (backendStats.entities > 0 || backendStats.relations > 0)
      ? backendStats
      : { entities: nodes.value.length, relations: edges.value.length }
    isShowingFullGraph.value = false
  } catch (err) {
    console.error('Search failed:', err)
  } finally {
    loading.value = false
  }
}

const handleReset = () => {
  query.value = ''
  selectedEntity.value = null
  selectedEdge.value = null
  loadFullGraph()
}

const handleNodeClick = (node) => {
  if (!node) return
  // Chunks 不是可编辑实体 — 其它节点都允许进入编辑面板
  if (node.node_type === 'Chunk' || node.type === 'Chunk') return
  openEntityEditor(node)
}

const handleEdgeClick = (edge) => {
  selectedEdge.value = edge
}

const closeEdgePanel = () => {
  selectedEdge.value = null
}

const openEntityEditor = (entity) => {
  selectedEntity.value = entity
  selectedEdge.value = null
  // 优先读 entity_type（真实实体类型），entity.type 是节点种类 "Entity" 字面量
  editingEntityType.value = entity.entity_type || entity.type || 'OTHER'
  // 描述可能在顶层（修复后）或 properties.description（旧数据）里
  editingEntityDescription.value = entity.description || entity.properties?.description || ''
  originalEntityType.value = editingEntityType.value
  originalEntityDescription.value = editingEntityDescription.value
  entityEditError.value = ''
  entityEditSuccess.value = ''
  merging.value = false
  mergeTargetName.value = ''
  mergePill.value = null
  mergeError.value = ''
}

const closeEntityPanel = () => {
  selectedEntity.value = null
}

const entityEditDirty = computed(() =>
  editingEntityType.value !== originalEntityType.value ||
  editingEntityDescription.value !== originalEntityDescription.value
)

const saveEntityEdits = async () => {
  if (!selectedEntity.value) return
  entityEditSaving.value = true
  entityEditError.value = ''
  entityEditSuccess.value = ''
  try {
    const { data } = await graphApi.updateEntity(selectedEntity.value.name, {
      entity_type: editingEntityType.value,
      description: editingEntityDescription.value,
    })
    entityEditSuccess.value = data?.message || 'Saved.'
    originalEntityType.value = editingEntityType.value
    originalEntityDescription.value = editingEntityDescription.value
    loadFullGraph()
  } catch (err) {
    entityEditError.value = err?.response?.data?.detail || 'Failed to save changes.'
  } finally {
    entityEditSaving.value = false
  }
}

const goToEntityDetail = (entity) => {
  if (!entity?.name) return
  router.push({ name: 'EntityDetail', params: { name: encodeURIComponent(entity.name) } })
}

const onMergeTargetInput = (val) => {
  mergePill.value = null
  mergeError.value = ''
  if (mergeLookupTimer) clearTimeout(mergeLookupTimer)
  if (!val || val === selectedEntity.value?.name) return
  mergeLookupTimer = setTimeout(async () => {
    try {
      const { data } = await graphApi.lookupEntity(val)
      if (data?.found && data.entity) {
        mergePill.value = data.entity
      } else {
        mergeError.value = `No entity named "${val}" found.`
      }
    } catch (err) {
      mergeError.value = 'Lookup failed.'
    }
  }, 350)
}

const clearMergePill = () => {
  mergePill.value = null
  mergeTargetName.value = ''
  mergeError.value = ''
}

const confirmMerge = async () => {
  if (!selectedEntity.value || !mergePill.value) return
  mergeSaving.value = true
  mergeError.value = ''
  try {
    await graphApi.mergeEntities({
      source: selectedEntity.value.name,
      target: mergePill.value.name,
    })
    merging.value = false
    clearMergePill()
    closeEntityPanel()
    loadFullGraph()
  } catch (err) {
    mergeError.value = err?.response?.data?.detail || 'Merge failed.'
  } finally {
    mergeSaving.value = false
  }
}

const requestDelete = () => {
  confirmDelete.value = true
}

const cancelDelete = () => {
  confirmDelete.value = false
}

const confirmDeleteEntity = async () => {
  if (!selectedEntity.value) return
  deleteSaving.value = true
  try {
    await graphApi.deleteEntity(selectedEntity.value.name)
    confirmDelete.value = false
    closeEntityPanel()
    loadFullGraph()
  } catch (err) {
    console.error('Delete failed:', err)
  } finally {
    deleteSaving.value = false
  }
}

const goToTimelineAnimation = () => {
  router.push({ name: 'EntityTimelineAnimation' })
}

watch(mergeTargetName, onMergeTargetInput)

onMounted(() => {
  loadFullGraph()
})

onUnmounted(() => {
  if (mergeLookupTimer) clearTimeout(mergeLookupTimer)
})
</script>

<style scoped>
.graph-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.stats {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.stat-divider {
  width: 1px;
  height: 18px;
  background: var(--border);
  margin: 0 0.5rem;
}

.search-section {
  padding: 1rem 2rem;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}
.search-input-wrapper {
  flex: 1;
  position: relative;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  transition: border-color var(--transition-fast);
}
.search-input-wrapper:focus-within { border-color: var(--primary); }
.search-icon {
  position: absolute;
  left: 0.625rem;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  color: var(--text-tertiary);
}
.search-input {
  width: 100%;
  padding: 0.5rem 0.75rem 0.5rem 2.125rem;
  border: none;
  background: transparent;
  font-size: 0.875rem;
  color: var(--text-primary);
  font-family: var(--font-sans);
}
.search-input:focus { outline: none; }
.search-input::placeholder { color: var(--text-tertiary); }
.search-btn-trigger { min-width: 110px; }

.graph-content {
  flex: 1;
  position: relative;
  overflow: hidden;
}

.graph-panel-container {
  position: absolute;
  inset: 0;
}

/* ---- Relationship panel ---- */
.relationship-panel,
.entity-edit-panel {
  position: absolute;
  top: 1rem;
  right: 1rem;
  width: 320px;
  max-height: calc(100vh - 200px);
  overflow-y: auto;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-md);
  z-index: 20;
  display: flex;
  flex-direction: column;
}

.entity-edit-panel { width: 420px; }

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-light);
  border-top-left-radius: var(--radius);
  border-top-right-radius: var(--radius);
  position: sticky;
  top: 0;
  z-index: 1;
}
.panel-title {
  font-family: var(--font-display);
  font-size: 0.9375rem;
  font-weight: 500;
  color: var(--text-primary);
  margin: 0;
}
.panel-close-btn :deep(.btn) { font-size: 1.25rem; line-height: 1; padding: 0 0.5rem; height: 28px; }

.panel-content {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.relation-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-primary);
  padding: 0.5rem 0.75rem;
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
  flex-wrap: wrap;
}
.source-name, .target-name {
  font-family: var(--font-display);
  font-size: 0.9375rem;
  font-weight: 500;
  color: var(--text-primary);
}
.relation-arrow {
  color: var(--text-tertiary);
  font-size: 1rem;
}

.detail-row {
  display: flex;
  gap: 0.5rem;
  font-size: 0.8125rem;
  align-items: baseline;
}
.detail-label {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  min-width: 4.5rem;
}
.detail-value {
  font-size: 0.8125rem;
  color: var(--text-primary);
  word-break: break-word;
}
.detail-value.uuid {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
}

/* ---- Entity edit panel ---- */
.detail-link-btn { margin: 0.75rem 1rem 0; }

.entity-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.75rem 1rem 1rem;
  border-bottom: 1px solid var(--border-light);
}
.entity-name {
  font-family: var(--font-display);
  font-size: 1.125rem;
  font-weight: 500;
  color: var(--text-primary);
  word-break: break-word;
  overflow-wrap: anywhere;
}

.entity-name-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.625rem;
}
.entity-name-row .entity-name { flex: 1; min-width: 0; }

.entity-type-badges {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.25rem;
  flex-shrink: 0;
}
.category-badge {
  padding: 2px 10px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.type-raw {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.entity-meta {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  padding-top: 0.5rem;
  border-top: 1px dashed var(--border-light);
}
.meta-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.8125rem;
}
.meta-row-col { flex-direction: column; align-items: stretch; gap: 0.25rem; }
.meta-label {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-tertiary);
  min-width: 2.5rem;
  flex-shrink: 0;
}
.meta-value {
  color: var(--text-primary);
  flex: 1;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.type-chip {
  display: inline-block;
  padding: 1px 8px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.04em;
}
.type-chip.empty {
  background: transparent;
  border-style: dashed;
  color: var(--text-tertiary);
  font-weight: 400;
  font-style: italic;
}
.description {
  font-size: 0.8125rem;
  line-height: 1.45;
  color: var(--text-secondary);
  white-space: pre-wrap;
}
.description.empty {
  color: var(--text-tertiary);
  font-style: italic;
}

.entity-section {
  padding: 1rem;
  border-bottom: 1px solid var(--border-light);
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.entity-section.danger {
  background: var(--error-light);
  position: relative;
}
.entity-section.danger::before {
  content: '';
  position: absolute;
  left: 0;
  top: 6px;
  bottom: 6px;
  width: 3px;
  background: var(--error);
  border-radius: 0 2px 2px 0;
}
.entity-section.danger .entity-section-title { color: var(--error); }
.danger-text {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  line-height: 1.5;
  margin: 0;
}

.entity-section-title {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-tertiary);
  font-weight: 600;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}
.form-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.form-input {
  padding: 0.5rem 0.625rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 0.8125rem;
  color: var(--text-primary);
  background: var(--bg-primary);
  font-family: var(--font-sans);
  transition: border-color var(--transition-fast);
}
.form-input:focus { outline: none; border-color: var(--primary); }

.error-message {
  padding: 0.5rem 0.625rem;
  background: var(--error-light);
  border: 1px solid var(--error);
  border-radius: var(--radius-sm);
  color: var(--error);
  font-size: 0.75rem;
}
.success-message {
  padding: 0.5rem 0.625rem;
  background: var(--success-light);
  border: 1px solid var(--success);
  border-radius: var(--radius-sm);
  color: var(--success);
  font-size: 0.75rem;
}

.actions-row {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

/* 触发 Merge 面板的次级按钮 — 视觉上与 Save/Cancel 分离，提示这是次要操作 */
.merge-toggle-row {
  padding-top: 0.25rem;
  border-top: 1px dashed var(--border-light);
  margin-top: 0.25rem;
}

.merge-hint {
  font-size: 0.75rem;
  color: var(--text-secondary);
  line-height: 1.5;
  margin: 0;
}
.merge-target-pill {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.625rem;
  background: var(--primary-light);
  color: var(--primary);
  border-radius: 999px;
  font-size: 0.8125rem;
}
.pill-label {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--primary-dark);
}
.pill-name {
  font-weight: 500;
}
.pill-clear-btn :deep(.btn) { font-size: 1rem; line-height: 1; padding: 0 0.375rem; height: 22px; }

/* ---- Confirm dialog ---- */
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 20, 19, 0.45);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.confirm-dialog {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1.5rem;
  max-width: 420px;
  width: 90%;
  box-shadow: var(--shadow-lg);
}
.confirm-title {
  font-family: var(--font-display);
  font-size: 1.125rem;
  font-weight: 500;
  color: var(--text-primary);
  margin: 0 0 0.5rem;
}
.confirm-text {
  font-size: 0.875rem;
  color: var(--text-secondary);
  line-height: 1.5;
  margin: 0 0 1.25rem;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
