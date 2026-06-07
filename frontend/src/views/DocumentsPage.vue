<template>
  <div class="documents-page">
    <PageHeader
      :icon="DocumentIcon"
      title="Documents"
      subtitle="Upload and manage your documents"
    >
      <template #actions>
        <Button
          variant="secondary"
          size="sm"
          :icon="ClusterIcon"
          @click="goToClusterMap"
          title="View semantic cluster map"
        >
          Cluster Map
        </Button>
        <input
          ref="fileInput"
          type="file"
          class="file-input"
          accept=".pdf,.docx,.doc,.txt,.md,.markdown"
          @change="handleFileSelect"
          multiple
        />
        <Button
          variant="primary"
          size="sm"
          :icon="uploading ? null : UploadIcon"
          :loading="uploading"
          @click="triggerUpload"
        >
          {{ uploading ? 'Uploading...' : 'Upload Documents' }}
        </Button>
      </template>
    </PageHeader>

    <!-- Processing Progress Modal -->
    <div v-if="processingDoc" class="progress-modal-overlay">
      <div class="progress-modal">
        <div class="progress-header">
          <h3>Processing Document</h3>
          <Button
            variant="ghost"
            size="sm"
            :icon="CloseIcon"
            icon-position="only"
            @click="cancelProcessing"
          />
        </div>

        <div class="progress-doc-name">{{ processingDoc.title || processingDoc.original_filename }}</div>

        <div class="progress-stages">
          <div
            v-for="stage in stages"
            :key="stage.id"
            class="stage-item"
            :class="{ active: stage.active, completed: stage.completed, error: stage.error }"
          >
            <div class="stage-icon">
              <CheckIcon v-if="stage.completed" />
              <XCircleIcon v-else-if="stage.error" />
              <span v-else class="stage-number">{{ stage.order }}</span>
            </div>
            <div class="stage-content">
              <div class="stage-name">{{ stage.name }}</div>
              <div v-if="stage.message" class="stage-message">{{ stage.message }}</div>
              <div v-if="stage.entities && stage.entities.length" class="stage-details">
                <div class="detail-label">Extracted:</div>
                <div class="detail-items">
                  <Tag v-for="(entity, idx) in stage.entities.slice(0, 8)" :key="idx" shape="badge" tone="primary">
                    {{ entity }}
                  </Tag>
                  <span v-if="stage.entities.length > 8" class="detail-more">+{{ stage.entities.length - 8 }} more</span>
                </div>
              </div>
              <div v-if="stage.relations_sample && stage.relations_sample.length" class="stage-details">
                <div class="detail-label">Relations:</div>
                <div class="detail-items">
                  <Tag v-for="(rel, idx) in stage.relations_sample.slice(0, 5)" :key="idx" shape="badge" tone="accent">
                    {{ rel[0] }} -> {{ rel[1] }}
                  </Tag>
                </div>
              </div>
              <div v-if="stage.active || stage.completed" class="stage-progress-bar">
                <div class="stage-progress-fill" :style="{ width: stage.completed ? '100%' : stage.percent + '%' }" />
              </div>
            </div>
          </div>
        </div>

        <div v-if="processingComplete" class="progress-complete">
          <div class="complete-icon">
            <CheckCircleIcon />
          </div>
          <div class="complete-text">
            Processing complete!
            <span v-if="processingStats.duration" class="duration-badge">
              {{ processingStats.duration }}
            </span>
            <span v-if="processingStats.entityCount > 0">
              Extracted {{ processingStats.entityCount }} entities and {{ processingStats.relationCount }} relations
            </span>
          </div>
          <Button variant="primary" size="sm" @click="viewGraph">View Knowledge Graph</Button>
        </div>

        <div v-if="processingError" class="progress-error">
          <div class="error-icon">
            <XCircleIcon />
          </div>
          <div class="error-text">{{ processingError }}</div>
          <Button variant="secondary" size="sm" @click="cancelProcessing">Close</Button>
        </div>
      </div>
    </div>

    <!-- Drop overlay -->
    <div
      v-if="isDragging"
      class="drop-overlay"
      @dragenter.prevent="isDragging = true"
      @dragover.prevent
      @drop.prevent="onDrop"
    >
      <div class="drop-card">
        <UploadIcon class="drop-icon-svg" />
        <p class="drop-title">Drop files to upload</p>
        <p class="drop-hint">PDF, DOCX, TXT, MD · up to 10 MB each</p>
      </div>
    </div>

    <!-- Documents List -->
    <div
      class="documents-content"
      @dragenter.prevent="onDragEnter"
      @dragover.prevent="onDragOver"
      @dragleave.prevent="onDragLeave"
      @drop.prevent="onDrop"
    >
      <div v-if="userTags.length > 0" class="tag-filter-bar">
        <span class="tag-filter-label">Filter by tag</span>
        <div class="tag-filter-chips">
          <Tag
            shape="pill"
            clickable
            :active="activeTagFilter === null"
            @click="clearTagFilter"
          >
            All
            <span class="tag-count">{{ documents.length }}</span>
          </Tag>
          <Tag
            v-for="t in userTags"
            :key="t.tag"
            shape="pill"
            clickable
            :active="activeTagFilter === t.tag"
            @click="setTagFilter(t.tag)"
          >
            {{ t.tag }}
            <span class="tag-count">{{ t.count }}</span>
          </Tag>
        </div>
      </div>

      <LoadingState v-if="loading" message="Loading documents..." />

      <div v-else-if="documents.length === 0 && !processingDoc" class="empty-state">
        <div class="empty-icon">
          <DocumentIcon class="empty-icon-svg" />
        </div>
        <h2 v-if="activeTagFilter">No documents tagged "{{ activeTagFilter }}"</h2>
        <h2 v-else>No Documents Yet</h2>
        <p v-if="activeTagFilter">
          Try a different tag or
          <Button variant="link" size="sm" @click="clearTagFilter">show all</Button>.
        </p>
        <p v-else>Upload PDF, DOCX, TXT, or MD files to get started</p>
      </div>

      <div v-else class="documents-list documents-list-scrollable">
        <div
          v-for="doc in documents"
          :key="doc.id"
          class="document-card"
        >
            <div class="doc-icon">
              <DocumentIcon class="doc-icon-svg" />
            </div>
            <div class="doc-info">
              <h3
                class="doc-name doc-name-link"
                role="button"
                tabindex="0"
                :title="`Open detail page for ${doc.title || doc.original_filename}`"
                @click="goToDetail(doc.id)"
                @keydown.enter.prevent="goToDetail(doc.id)"
                @keydown.space.prevent="goToDetail(doc.id)"
              >{{ doc.title || doc.original_filename }}</h3>
              <p class="doc-meta">
                <span>{{ formatDate(doc.created_at) }}</span>
                <span v-if="doc.file_type"> &bull; {{ doc.file_type.toUpperCase() }}</span>
              </p>
              <div class="doc-tags">
                <Tag
                  v-for="t in (doc.tags || [])"
                  :key="t"
                  shape="pill"
                  clickable
                  removable
                  @click="setTagFilter(t)"
                  @remove="removeTag(doc.id, t)"
                >
                  {{ t }}
                </Tag>
                <input
                  v-if="editingTagDocId === doc.id"
                  :data-tag-input-for="doc.id"
                  v-model="newTagInput"
                  class="tag-input"
                  placeholder="new tag"
                  :disabled="tagBusy"
                  @keydown.enter.prevent="submitAddTag(doc.id)"
                  @keydown.esc.prevent="cancelAddTag"
                  @blur="cancelAddTag"
                />
                <Button
                  v-else
                  variant="outline-dashed"
                  size="sm"
                  @click="startAddTag(doc.id)"
                  :title="`Add tag to ${doc.title || doc.original_filename}`"
                >
                  + Add tag
                </Button>
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              :icon="ClockIcon"
              icon-position="only"
              @click="viewProgress(doc)"
              title="View Progress"
            />
            <Button
              variant="ghost"
              size="sm"
              :icon="TrashIcon"
              icon-position="only"
              @click="handleDelete(doc.id)"
              title="Delete"
            />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, reactive, h } from 'vue'
import { useRouter } from 'vue-router'
import { documentApi } from '../api/documents'
import { tagApi } from '../api/tags'
import { PageHeader, Button, Tag, LoadingState } from '../components/ui'

const DocumentIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }),
    h('polyline', { points: '14 2 14 8 20 8' }),
    h('line', { x1: 16, y1: 13, x2: 8, y2: 13 }),
    h('line', { x1: 16, y1: 17, x2: 8, y2: 17 })
  ])
}
const ClusterIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 6, cy: 6, r: 2 }),
    h('circle', { cx: 18, cy: 6, r: 2 }),
    h('circle', { cx: 12, cy: 18, r: 2 })
  ])
}
const UploadIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }),
    h('polyline', { points: '17 8 12 3 7 8' }),
    h('line', { x1: 12, y1: 3, x2: 12, y2: 15 })
  ])
}
const CloseIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round' }, [
    h('line', { x1: 18, y1: 6, x2: 6, y2: 18 }),
    h('line', { x1: 6, y1: 6, x2: 18, y2: 18 })
  ])
}
const TrashIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('polyline', { points: '3 6 5 6 21 6' }),
    h('path', { d: 'M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2' })
  ])
}
const ClockIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 10 }),
    h('polyline', { points: '12 6 12 12 16 14' })
  ])
}
const CheckIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('polyline', { points: '20 6 9 17 4 12' })
  ])
}
const CheckCircleIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M22 11.08V12a10 10 0 1 1-5.93-9.14' }),
    h('polyline', { points: '22 4 12 14.01 9 11.01' })
  ])
}
const XCircleIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 12, cy: 12, r: 10 }),
    h('line', { x1: 15, y1: 9, x2: 9, y2: 15 }),
    h('line', { x1: 9, y1: 9, x2: 15, y2: 15 })
  ])
}

const router = useRouter()

const documents = ref([])
const loading = ref(false)
const uploading = ref(false)
const fileInput = ref(null)
const isDragging = ref(false)
let dragDepth = 0

const userTags = ref([])
const activeTagFilter = ref(null)
const editingTagDocId = ref(null)
const newTagInput = ref('')
const tagBusy = ref(false)

const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.doc', '.txt', '.md', '.markdown']
const MAX_FILE_SIZE = 10 * 1024 * 1024

const isAllowedFile = (file) => {
  const name = (file?.name || '').toLowerCase()
  return ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext))
}

const onDragEnter = (event) => {
  if (!event.dataTransfer?.types?.includes('Files')) return
  dragDepth += 1
  isDragging.value = true
}

const onDragOver = (event) => {
  if (!event.dataTransfer?.types?.includes('Files')) return
  event.dataTransfer.dropEffect = 'copy'
}

const onDragLeave = (event) => {
  if (!event.dataTransfer?.types?.includes('Files')) return
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) isDragging.value = false
}

const onDrop = (event) => {
  dragDepth = 0
  isDragging.value = false
  const files = Array.from(event.dataTransfer?.files || [])
  if (files.length === 0) return
  uploadFiles(files)
}

const onPaste = (event) => {
  const files = Array.from(event.clipboardData?.files || [])
  if (files.length === 0) return
  uploadFiles(files)
}

const processingDoc = ref(null)
const processingComplete = ref(false)
const processingError = ref(null)
const processingStats = ref({ entityCount: 0, relationCount: 0, duration: '' })
let eventSource = null

const stages = reactive([
  { id: 'document_created', name: 'Creating document', order: 1, active: false, completed: false, error: false, message: '', percent: 0, entities: [], relations_sample: [] },
  { id: 'chunking', name: 'Chunking content', order: 2, active: false, completed: false, error: false, message: '', percent: 0, entities: [], relations_sample: [] },
  { id: 'embedding', name: 'Creating embeddings', order: 3, active: false, completed: false, error: false, message: '', percent: 0, entities: [], relations_sample: [] },
  { id: 'stored', name: 'Storing in database', order: 4, active: false, completed: false, error: false, message: '', percent: 0, entities: [], relations_sample: [] },
  { id: 'graph', name: 'Building knowledge graph', order: 5, active: false, completed: false, error: false, message: '', percent: 0, entities: [], relations_sample: [] },
  { id: 'entity_extraction', name: 'Extracting entities (LLM)', order: 6, active: false, completed: false, error: false, message: '', percent: 0, entities: [], relations_sample: [] },
  { id: 'entities', name: 'Saving entities', order: 7, active: false, completed: false, error: false, message: '', percent: 0, entities: [], relations_sample: [] },
  { id: 'relations', name: 'Creating relationships', order: 8, active: false, completed: false, error: false, message: '', percent: 0, entities: [], relations_sample: [] }
])

const resetStages = () => {
  stages.forEach(s => {
    s.active = false
    s.completed = false
    s.error = false
    s.message = ''
    s.percent = 0
    s.entities = []
    s.relations_sample = []
  })
}

const loadDocuments = async () => {
  loading.value = true
  try {
    const { data } = await documentApi.list(activeTagFilter.value || undefined)
    documents.value = data.documents || data || []
  } catch (error) {
    console.error('Failed to load documents:', error)
    documents.value = []
  } finally {
    loading.value = false
  }
}

const loadUserTags = async () => {
  try {
    const { data } = await tagApi.listAll()
    userTags.value = data || []
  } catch (error) {
    console.error('Failed to load tags:', error)
    userTags.value = []
  }
}

const setTagFilter = (tag) => {
  if (activeTagFilter.value === tag) return
  activeTagFilter.value = tag
  loadDocuments()
}

const clearTagFilter = () => {
  if (activeTagFilter.value === null) return
  activeTagFilter.value = null
  loadDocuments()
}

const startAddTag = (docId) => {
  editingTagDocId.value = docId
  newTagInput.value = ''
  setTimeout(() => {
    const el = document.querySelector(`[data-tag-input-for="${docId}"]`)
    if (el) el.focus()
  }, 0)
}

const cancelAddTag = () => {
  editingTagDocId.value = null
  newTagInput.value = ''
}

const submitAddTag = async (docId) => {
  const value = newTagInput.value.trim()
  if (!value || tagBusy.value) return
  tagBusy.value = true
  try {
    const { data } = await tagApi.addDocTag(docId, value)
    applyTagsFromServer(docId, data)
    cancelAddTag()
    loadUserTags()
  } catch (error) {
    console.error('Failed to add tag:', error)
    const msg = error?.response?.data?.detail || 'Could not add tag.'
    alert(msg)
  } finally {
    tagBusy.value = false
  }
}

const removeTag = async (docId, tag) => {
  if (tagBusy.value) return
  tagBusy.value = true
  try {
    const { data } = await tagApi.removeDocTag(docId, tag)
    applyTagsFromServer(docId, data)
    loadUserTags()
  } catch (error) {
    console.error('Failed to remove tag:', error)
  } finally {
    tagBusy.value = false
  }
}

const applyTagsFromServer = (docId, tags) => {
  const doc = documents.value.find(d => d.id === docId)
  if (doc) doc.tags = tags || []
}

const triggerUpload = () => {
  fileInput.value?.click()
}

const handleFileSelect = async (event) => {
  const files = event.target.files
  if (!files || files.length === 0) return
  await uploadFiles(Array.from(files))
  event.target.value = ''
}

const uploadFiles = async (files) => {
  const accepted = []
  const rejected = []

  for (const file of files) {
    if (!isAllowedFile(file)) {
      rejected.push({ name: file.name, reason: 'unsupported type' })
      continue
    }
    if (file.size > MAX_FILE_SIZE) {
      rejected.push({
        name: file.name,
        reason: `too large (${(file.size / 1024 / 1024).toFixed(1)} MB > 10 MB)`,
      })
      continue
    }
    accepted.push(file)
  }

  if (rejected.length > 0) {
    console.warn('Skipped files:', rejected)
  }

  if (accepted.length === 0) return

  uploading.value = true
  try {
    for (const file of accepted) {
      const response = await documentApi.upload(file)
      const docData = response.data
      startProgressTracking(docData)
    }
  } catch (error) {
    console.error('Upload failed:', error)
    uploading.value = false
  }
}

const startProgressTracking = (docData) => {
  processingDoc.value = docData
  processingComplete.value = false
  processingError.value = null
  processingStats.value = { entityCount: 0, relationCount: 0 }
  resetStages()
  connectProgressStream(docData.id)
}

const handleProgressEvent = (e) => {
  try {
    const event = JSON.parse(e.data)
    if (event.type === 'keepalive') return

    if (event.type === 'error') {
      const message = event.error || event.message || 'Processing failed'
      processingError.value = message
      const stage = stages.find(s => s.active)
      if (stage) {
        stage.error = true
        stage.active = false
      }
      return
    }

    if (event.type === 'complete') {
      processingComplete.value = true
      processingStats.value = {
        entityCount: event.data?.entity_count || 0,
        relationCount: event.data?.relation_count || 0,
        duration: event.data?.duration || ''
      }
      stages.forEach(s => {
        s.completed = true
        s.active = false
      })
      localStorage.setItem('graph_refresh', Date.now())
      return
    }

    const stageData = event.data
    if (!stageData || !stageData.stage) return

    const stage = stages.find(s => s.id === stageData.stage)
    if (!stage) return

    const stageIndex = stages.indexOf(stage)
    stages.slice(0, stageIndex).forEach(s => {
      if (!s.completed && !s.error) s.completed = true
    })

    stage.active = true
    stage.completed = false
    stage.message = event.message
    stage.percent = stageData.percent || 0

    if (stageData.entities) stage.entities = stageData.entities
    if (stageData.relations_sample) stage.relations_sample = stageData.relations_sample
  } catch (err) {
    console.error('Failed to parse progress event:', err)
  }
}

const handleProgressError = () => {
  if (processingComplete.value || processingError.value) return
  processingError.value = 'Connection lost. Close and reopen the progress dialog to check the latest status.'
}

const connectProgressStream = (docId) => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  const token = localStorage.getItem('token')
  eventSource = new EventSource(`/api/progress/${docId}?token=${token}`)
  eventSource.onmessage = handleProgressEvent
  eventSource.onerror = handleProgressError
}

const cancelProcessing = () => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  processingDoc.value = null
  processingComplete.value = false
  processingError.value = null
  uploading.value = false
  loadDocuments()
}

const viewGraph = () => {
  cancelProcessing()
  router.push('/graph')
}

const goToDetail = (docId) => {
  router.push({ name: 'DocumentDetail', params: { id: docId } })
}

const goToClusterMap = () => {
  router.push({ name: 'DocumentClusterMap' })
}

const viewProgress = async (doc) => {
  processingDoc.value = { id: doc.id, title: doc.title, original_filename: doc.original_filename }
  processingComplete.value = false
  processingError.value = null
  resetStages()

  let alreadyTerminal = false
  try {
    const historyResponse = await documentApi.getProgressHistory(doc.id)
    const history = historyResponse.data?.history || []
    if (history.length > 0) {
      const lastEvent = history[history.length - 1]
      if (lastEvent.is_complete) {
        const durationMatch = lastEvent.message ? lastEvent.message.match(/\(([^)]+)\)/) : null
        processingComplete.value = true
        processingStats.value = {
          entityCount: lastEvent.entity_count || 0,
          relationCount: lastEvent.relation_count || 0,
          duration: durationMatch ? durationMatch[1] : ''
        }
        stages.forEach(s => {
          s.completed = true
          s.active = false
        })
        alreadyTerminal = true
      } else if (lastEvent.is_error) {
        processingError.value = lastEvent.error_message || 'Processing failed'
        const stage = stages.find(s => s.active)
        if (stage) {
          stage.error = true
          stage.active = false
        }
        alreadyTerminal = true
      } else {
        for (const event of history) {
          const stage = stages.find(s => s.id === event.stage)
          if (!stage) continue
          stage.message = event.message
          stage.percent = event.percent || 0
          if (event === lastEvent) {
            stage.active = true
            stage.completed = false
          } else {
            stage.active = false
            stage.completed = true
          }
        }
      }
    }
  } catch (err) {
    console.log('No history found, connecting to live stream', err)
  }

  if (alreadyTerminal) return
  connectProgressStream(doc.id)
}

const handleDelete = async (id) => {
  if (!confirm('Are you sure you want to delete this document?')) return

  try {
    await documentApi.delete(id)
    await loadDocuments()
    loadUserTags()
  } catch (error) {
    console.error('Delete failed:', error)
  }
}

const formatDate = (dateStr) => {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    })
  } catch {
    return dateStr
  }
}

const formatSize = (bytes) => {
  if (!bytes) return ''
  const kb = bytes / 1024
  if (kb < 1024) return `${kb.toFixed(1)} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

onMounted(() => {
  loadDocuments()
  loadUserTags()
  document.addEventListener('paste', onPaste)
})

onUnmounted(() => {
  if (eventSource) {
    eventSource.close()
  }
  document.removeEventListener('paste', onPaste)
  isDragging.value = false
  dragDepth = 0
})
</script>

<style scoped>
.documents-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.file-input { display: none; }

.documents-content {
  flex: 1;
  padding: 2rem;
  overflow-y: auto;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-tertiary);
  text-align: center;
}
.empty-icon {
  width: 64px;
  height: 64px;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-tertiary);
  margin-bottom: 1.25rem;
  display: flex;
  align-items: center;
  justify-content: center;
}
.empty-icon-svg { width: 100%; height: 100%; }
.empty-state h2 {
  font-family: var(--font-display);
  font-size: 1.375rem;
  color: var(--text-secondary);
  margin-bottom: 0.375rem;
}
.empty-state p { font-size: 0.875rem; }

.tag-filter-bar {
  max-width: 1000px;
  margin: 0 auto 1rem;
  padding: 0.75rem 1rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.tag-filter-label {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.tag-filter-chips { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.tag-count {
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  margin-left: 0.25rem;
}

.doc-tags {
  margin-top: 0.4375rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.3125rem;
  min-height: 1.5rem;
}
.tag-input {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  padding: 0.1875rem 0.5rem;
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--primary);
  border-radius: 999px;
  outline: none;
  width: 7rem;
  line-height: 1.4;
}
.tag-input::placeholder { color: var(--text-tertiary); }

.documents-list {
  max-width: 1000px;
  margin: 0 auto;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-primary);
  overflow: hidden;
}
.documents-list-scrollable {
  max-height: 70vh;
  overflow-y: auto;
}
.document-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
  background: transparent;
  transition: background-color var(--transition-fast);
}
.document-card:last-child { border-bottom: none; }
.document-card:hover { background: var(--bg-tertiary); }

.doc-icon {
  width: 32px;
  height: 32px;
  padding: 7px;
  color: var(--text-tertiary);
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.doc-icon-svg { width: 100%; height: 100%; }

.doc-info { flex: 1; min-width: 0; }
.doc-name {
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 0.125rem;
  letter-spacing: -0.005em;
}
.doc-name-link {
  cursor: pointer;
  border-radius: var(--radius-sm);
  padding: 2px 4px;
  margin: -2px -4px;
  transition: background-color var(--transition-fast), color var(--transition-fast);
}
.doc-name-link:hover { background: var(--primary-light); color: var(--primary); }
.doc-name-link:focus { outline: 2px solid var(--primary); outline-offset: 1px; }
.doc-meta {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

.drop-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(20, 20, 19, 0.35);
  backdrop-filter: blur(2px);
  pointer-events: auto;
}
.drop-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 2.5rem 3rem;
  background: var(--bg-primary);
  border: 2px dashed var(--primary);
  border-radius: var(--radius-lg);
  color: var(--primary);
  box-shadow: var(--shadow-lg);
}
.drop-icon-svg { width: 48px; height: 48px; }
.drop-title {
  font-family: var(--font-display);
  font-size: 1.125rem;
  font-weight: 500;
  color: var(--text-primary);
}
.drop-hint {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

/* Progress Modal */
.progress-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 20, 19, 0.45);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.progress-modal {
  width: 90%;
  max-width: 600px;
  min-width: 380px;
  max-height: 80vh;
  overflow-y: auto;
  padding: 1.5rem;
  border-radius: var(--radius-lg);
  background: var(--bg-primary);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-lg);
}
.progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}
.progress-header h3 {
  font-family: var(--font-display);
  font-size: 1.25rem;
  font-weight: 500;
  color: var(--text-primary);
}
.progress-doc-name {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 1.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  word-break: break-all;
}
.progress-stages {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.stage-item {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  border: 1px solid transparent;
  transition: background-color var(--transition), border-color var(--transition);
}
.stage-item.active { background: var(--primary-light); border-color: var(--primary); }
.stage-item.completed { background: var(--success-light); }
.stage-item.error { background: var(--error-light); }
.stage-icon {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-primary);
  border: 1px solid var(--border-strong);
  flex-shrink: 0;
  color: var(--text-secondary);
}
.stage-item.active .stage-icon { background: var(--primary); border-color: var(--primary); color: var(--primary-fg); }
.stage-item.completed .stage-icon { background: var(--success); border-color: var(--success); color: white; }
.stage-item.error .stage-icon { background: var(--error); border-color: var(--error); color: white; }
.stage-icon :deep(svg) { width: 12px; height: 12px; }
.stage-number {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  font-weight: 600;
  color: var(--text-secondary);
}
.stage-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.stage-name { font-size: 0.875rem; font-weight: 500; color: var(--text-primary); }
.stage-message { font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem; }
.stage-details {
  margin-top: 0.5rem;
  padding: 0.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.detail-label {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  text-transform: uppercase;
  color: var(--text-tertiary);
  margin-bottom: 0.25rem;
  letter-spacing: 0.05em;
}
.detail-items { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.detail-more {
  font-size: 0.625rem;
  color: var(--text-tertiary);
  padding: 2px 4px;
}
.stage-progress-bar {
  width: 100%;
  height: 3px;
  background: var(--bg-primary);
  border-radius: 2px;
  margin-top: 0.625rem;
  overflow: hidden;
}
.stage-progress-fill {
  height: 100%;
  background: var(--primary);
  border-radius: 2px;
  transition: width 0.3s ease;
}
.stage-item.completed .stage-progress-fill { background: var(--success); }

.progress-complete,
.progress-error {
  margin-top: 1.5rem;
  padding: 1.25rem;
  border-radius: var(--radius);
  text-align: center;
  border: 1px solid;
}
.progress-complete { background: var(--success-light); border-color: var(--success); }
.progress-error { background: var(--error-light); border-color: var(--error); }
.complete-icon,
.error-icon {
  width: 40px;
  height: 40px;
  margin: 0 auto 0.75rem;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.complete-icon { background: var(--success); color: white; }
.error-icon { background: var(--error); color: white; }
.complete-icon :deep(svg),
.error-icon :deep(svg) { width: 20px; height: 20px; }
.complete-text,
.error-text {
  font-size: 0.875rem;
  color: var(--text-primary);
  margin-bottom: 1rem;
}
.complete-text span { display: block; font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem; }
.duration-badge {
  display: inline-block;
  margin-left: 0.5rem;
  padding: 1px 8px;
  background: var(--primary-light);
  color: var(--primary);
  border-radius: 4px;
  font-size: 0.6875rem;
  font-weight: 600;
  font-family: var(--font-mono);
}
</style>
