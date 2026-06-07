<template>
  <div class="chat-page">
    <PageHeader
      :icon="MessageIcon"
      title="AI Chat"
      subtitle="Ask questions about your knowledge graph"
    >
      <template #actions>
        <div class="conversation-dropdown">
          <Button
            variant="secondary"
            size="sm"
            :icon="ClockIcon"
            icon-position="left"
            @click="toggleDropdown"
          >
            {{ currentConversationId ? 'History' : 'New Chat' }}
            <ChevronIcon class="chevron" />
          </Button>
          <div v-if="showDropdown" class="dropdown-menu">
            <Button
              variant="ghost"
              size="sm"
              block
              :icon="PlusIcon"
              icon-position="left"
              class="dropdown-new-chat"
              @click="startNewChat"
            >
              New Chat
            </Button>
            <div v-if="conversations.length > 0" class="dropdown-divider" />
            <Button
              v-for="conv in conversations"
              :key="conv.id"
              variant="ghost"
              size="sm"
              block
              :icon="MessageIcon"
              icon-position="left"
              :class="{ 'dropdown-active': conv.id === currentConversationId }"
              @click="loadConversation(conv.id)"
            >
              <span class="conv-title">{{ conv.title || 'Untitled' }}</span>
            </Button>
            <div v-if="conversations.length === 0" class="dropdown-empty">
              No conversation history
            </div>
          </div>
        </div>
      </template>
    </PageHeader>

    <div class="chat-content">
      <div class="messages-container" ref="messagesContainer">
        <EmptyState
          v-if="messages.length === 0"
          :icon="MessageIcon"
          title="Start a Conversation"
          description="Ask questions about your documents and knowledge graph"
        />

        <div v-else class="messages-list">
          <div
            class="messages-virtual"
            :style="{ height: `${totalSize}px`, position: 'relative', width: '100%' }"
          >
            <div
              v-for="vRow in virtualItems"
              :key="vRow.key"
              :ref="measureElement"
              :data-index="vRow.index"
              class="message-virtual-item"
              :style="{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${vRow.start}px)`,
                paddingBottom: '1.25rem'
              }"
            >
              <div
                class="message"
                :class="messages[vRow.index].role"
              >
                <div class="message-avatar">
                  <UserIcon v-if="messages[vRow.index].role === 'user'" />
                  <BoltIcon v-else />
                </div>
                <div class="message-content">
                  <div class="message-text" v-html="messages[vRow.index].formattedHtml" />
                  <div
                    v-if="messages[vRow.index].role === 'assistant' && messages[vRow.index].sources && messages[vRow.index].sources.length"
                    class="sources-panel"
                  >
                    <div class="sources-header">
                      <span class="sources-label">SOURCES</span>
                      <span
                        v-if="messages[vRow.index].citation_coverage !== undefined && messages[vRow.index].citation_coverage !== null"
                        class="coverage-indicator"
                        :class="coverageTone(messages[vRow.index].citation_coverage)"
                        :title="`${Math.round((messages[vRow.index].citation_coverage || 0) * 100)}% of available sources are cited in the answer`"
                      >
                        <span class="coverage-bar">
                          <span
                            class="coverage-bar-fill"
                            :style="{ width: ((messages[vRow.index].citation_coverage || 0) * 100) + '%' }"
                          />
                        </span>
                        <span class="coverage-label">
                          {{ Math.round((messages[vRow.index].citation_coverage || 0) * 100) }}% cited
                        </span>
                      </span>
                    </div>
                    <div class="sources-chips">
                      <Tag
                        v-for="src in messages[vRow.index].sources"
                        :key="src.index"
                        shape="pill"
                        clickable
                        :active="messages[vRow.index].expandedSourceIndex === src.index"
                        :title="`${src.title} — click to ${messages[vRow.index].expandedSourceIndex === src.index ? 'collapse' : 'expand'}`"
                        @click="toggleSource(messages[vRow.index], src)"
                      >
                        <template #dot>
                          <Dot
                            :tone="src.quality === 'high' ? 'success' : src.quality === 'medium' ? 'warning' : 'error'"
                            class="quality-dot-inline"
                          />
                        </template>
                        [{{ src.index }}] {{ src.title || 'Document ' + src.document_id?.slice(0, 8) }}
                      </Tag>
                    </div>

                    <div
                      v-for="src in messages[vRow.index].sources"
                      v-show="messages[vRow.index].expandedSourceIndex === src.index"
                      :key="`card-${src.index}`"
                      class="source-card"
                    >
                      <div class="source-card-header">
                        <div class="source-card-meta">
                          <span class="source-card-index">[{{ src.index }}]</span>
                          <span class="source-card-title">{{ src.title }}</span>
                          <Tag
                            v-if="src.quality"
                            shape="badge"
                            :tone="src.quality === 'high' ? 'success' : src.quality === 'medium' ? 'warning' : 'error'"
                            class="quality-badge"
                          >
                            <Dot
                              :tone="src.quality === 'high' ? 'success' : src.quality === 'medium' ? 'warning' : 'error'"
                            />
                            {{ src.quality }}
                            <span v-if="src.relevance_score != null" class="quality-score">
                              {{ Math.round((src.relevance_score || 0) * 100) }}%
                            </span>
                          </Tag>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon-position="only"
                          @click="toggleSource(messages[vRow.index], src)"
                          title="Close"
                          class="source-card-close-btn"
                        >
                          &times;
                        </Button>
                      </div>
                      <div v-if="src.hierarchy_path && src.hierarchy_path.length" class="source-card-breadcrumb">
                        <span v-for="(seg, i) in src.hierarchy_path" :key="i">
                          <span class="crumb">{{ seg }}</span>
                          <span v-if="i < src.hierarchy_path.length - 1" class="crumb-sep">›</span>
                        </span>
                      </div>
                      <pre class="source-card-content">{{ src.content }}</pre>
                      <div v-if="src.truncated" class="source-card-footnote">
                        Excerpt truncated — the full chunk is longer.
                      </div>
                    </div>
                  </div>

                  <div
                    v-if="messages[vRow.index].role === 'assistant' && messages[vRow.index].followups && messages[vRow.index].followups.length"
                    class="followups-chips"
                  >
                    <span class="followups-label">FOLLOW UP</span>
                    <Button
                      v-for="(q, qi) in messages[vRow.index].followups"
                      :key="qi"
                      variant="secondary"
                      size="sm"
                      :icon="ArrowRightIcon"
                      icon-position="left"
                      class="followup-chip-btn"
                      :title="q"
                      @click="applyFollowup(q)"
                    >
                      {{ q }}
                    </Button>
                  </div>

                  <div
                    v-if="messages[vRow.index].role === 'assistant' && messages[vRow.index].id"
                    class="feedback-bar"
                  >
                    <Button
                      variant="ghost"
                      size="sm"
                      :icon="ThumbsUpIcon"
                      icon-position="only"
                      :class="{ 'feedback-active': messages[vRow.index].rating === 'up' }"
                      :title="messages[vRow.index].rating === 'up' ? 'Remove like' : 'Helpful response'"
                      @click="onFeedback(messages[vRow.index], 'up')"
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      :icon="ThumbsDownIcon"
                      icon-position="only"
                      :class="{ 'feedback-active': messages[vRow.index].rating === 'down' }"
                      :title="messages[vRow.index].rating === 'down' ? 'Remove dislike' : 'Unhelpful response'"
                      @click="onFeedback(messages[vRow.index], 'down')"
                    />
                    <span v-if="messages[vRow.index].feedbackSaving" class="feedback-status">Saving…</span>
                    <span v-else-if="messages[vRow.index].rating" class="feedback-status">
                      {{ messages[vRow.index].rating === 'up' ? 'Thanks for the feedback' : 'Marked as unhelpful' }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-if="loading" class="message assistant">
            <div class="message-avatar">
              <BoltIcon />
            </div>
            <div class="message-content">
              <div class="message-text typing">
                <span class="dot" />
                <span class="dot" />
                <span class="dot" />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="input-area">
        <div class="input-container">
          <textarea
            v-model="inputMessage"
            class="message-input"
            placeholder="Type your message..."
            @keydown.enter.exact.prevent="sendMessage"
            :disabled="loading"
            rows="1"
          />
          <Button
            variant="primary"
            size="sm"
            :icon="SendIcon"
            icon-position="only"
            :disabled="!inputMessage.trim() || loading"
            @click="sendMessage"
            class="send-btn"
          />
        </div>
        <div class="input-toolbar">
          <Switch v-model="useGraphRag" label="Graph" />
          <Switch v-model="useCompare" label="Compare" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onActivated, onUnmounted, h } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { chatApi } from '../api/chat'
import { PageHeader, Button, Tag, Dot, Switch, EmptyState } from '../components/ui'

const MessageIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' })
  ])
}
const ClockIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z' })
  ])
}
const ChevronIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('polyline', { points: '6 9 12 15 18 9' })
  ])
}
const PlusIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('line', { x1: 12, y1: 5, x2: 12, y2: 19 }),
    h('line', { x1: 5, y1: 12, x2: 19, y2: 12 })
  ])
}
const UserIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2' }),
    h('circle', { cx: 12, cy: 7, r: 4 })
  ])
}
const BoltIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('polygon', { points: '13 2 3 14 12 14 11 22 21 10 12 10 13 2' })
  ])
}
const SendIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('line', { x1: 22, y1: 2, x2: 11, y2: 13 }),
    h('polygon', { points: '22 2 15 22 11 13 2 9 22 2' })
  ])
}
const ArrowRightIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('polyline', { points: '9 18 15 12 9 6' })
  ])
}
const ThumbsUpIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M7 10v12' }),
    h('path', { d: 'M15 5.88L14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H7V10l4.34-7.06A1 1 0 0 1 13 3.34L15 5.88z' })
  ])
}
const ThumbsDownIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M17 14V2' }),
    h('path', { d: 'M9 18.12L10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H17v12l-4.34 7.06A1 1 0 0 1 11 20.66L9 18.12z' })
  ])
}

const handleClickOutside = (event) => {
  if (showDropdown.value && !event.target.closest('.conversation-dropdown')) {
    showDropdown.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
  messagesContainer.value?.addEventListener('click', onCitationClick)
  loadConversations()
  scrollToBottom()
})

onActivated(() => {
  loadConversations()
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  messagesContainer.value?.removeEventListener('click', onCitationClick)
})

const messages = ref([])
const inputMessage = ref('')
const loading = ref(false)
const useGraphRag = ref(false)
const useCompare = ref(false)
const messagesContainer = ref(null)
const currentConversationId = ref(null)
const conversations = ref([])
const showDropdown = ref(false)

const rowVirtualizer = useVirtualizer({
  count: computed(() => messages.value.length),
  getScrollElement: () => messagesContainer.value,
  estimateSize: () => 120,
  overscan: 6
})

const virtualItems = computed(() => rowVirtualizer.value.getVirtualItems())
const totalSize = computed(() => rowVirtualizer.value.getTotalSize())

const measureElement = (el) => {
  if (el) rowVirtualizer.value.measureElement(el)
}

const loadConversations = async () => {
  try {
    const { data } = await chatApi.getConversations()
    conversations.value = data || []
  } catch (error) {
    console.error('Failed to load conversations:', error)
    conversations.value = []
  }
}

const loadConversation = async (conversationId) => {
  try {
    const { data: msgs } = await chatApi.getMessages(conversationId)
    const hydrated = (msgs || []).map(m => ({
      id: m.id ?? null,
      role: m.role,
      content: m.content,
      formattedHtml: formatMessage(m.content),
      rating: null,
      feedbackSaving: false,
      sources: [],
      followups: [],
      expandedSourceIndex: null,
    }))
    messages.value = hydrated
    currentConversationId.value = conversationId

    const assistantMsgs = hydrated.filter(m => m.role === 'assistant' && m.id)
    await Promise.all(
      assistantMsgs.map(async (m) => {
        try {
          const { data } = await chatApi.getFeedback(m.id)
          m.rating = data?.rating || null
        } catch (err) {
          if (err?.response?.status && err.response.status !== 404) {
            console.warn('Feedback fetch failed:', err)
          }
        }
      })
    )
  } catch (error) {
    console.error('Failed to load conversation messages:', error)
    messages.value = []
  }
  showDropdown.value = false
  scrollToBottom()
}

const onFeedback = async (msg, rating) => {
  if (!msg.id || msg.feedbackSaving) return
  const previous = msg.rating
  const next = previous === rating ? null : rating
  msg.rating = next
  msg.feedbackSaving = true
  try {
    if (next === null) {
      await chatApi.deleteFeedback(msg.id)
    } else {
      await chatApi.submitFeedback(msg.id, next)
    }
  } catch (error) {
    console.error('Feedback submit failed:', error)
    msg.rating = previous
  } finally {
    msg.feedbackSaving = false
  }
}

const startNewChat = () => {
  messages.value = []
  currentConversationId.value = null
  showDropdown.value = false
}

const applyFollowup = (question) => {
  if (!question) return
  inputMessage.value = question
  nextTick(() => {
    const ta = document.querySelector('.message-input')
    if (ta) {
      ta.focus()
      const len = ta.value.length
      ta.setSelectionRange(len, len)
    }
  })
}

const coverageTone = (ratio) => {
  const r = Number(ratio) || 0
  if (r >= 0.5) return 'coverage-good'
  if (r >= 0.25) return 'coverage-okay'
  return 'coverage-low'
}

const qualityTitle = (src) => {
  if (!src || !src.quality) return ''
  const score = src.relevance_score
  if (score === null || score === undefined) {
    return `Quality: ${src.quality} (no score available)`
  }
  return `Quality: ${src.quality} (relevance ${Math.round(score * 100)}%)`
}

const toggleDropdown = () => {
  showDropdown.value = !showDropdown.value
  if (showDropdown.value) {
    loadConversations()
  }
}

const scrollToBottom = async () => {
  await nextTick()
  if (rowVirtualizer.value && messages.value.length > 0) {
    rowVirtualizer.value.scrollToIndex(messages.value.length - 1, { align: 'end' })
  } else if (messagesContainer.value) {
    const container = messagesContainer.value.querySelector('.messages-container')
    if (container) {
      container.scrollTop = container.scrollHeight
    }
  }
}

const sendMessage = async () => {
  const message = inputMessage.value.trim()
  if (!message || loading.value) return

  messages.value.push({
    role: 'user',
    content: message,
    formattedHtml: formatMessage(message),
    sources: [],
    expandedSourceIndex: null,
  })
  inputMessage.value = ''
  loading.value = true
  await scrollToBottom()

  try {
    const { data } = await chatApi.send(message, currentConversationId.value, true, useGraphRag.value, useCompare.value)
    if (data.conversation_id && !currentConversationId.value) {
      currentConversationId.value = data.conversation_id
    }
    messages.value.push({
      role: 'assistant',
      content: data.message || 'No response',
      formattedHtml: formatMessage(data.message || 'No response'),
      sources: data.sources || [],
      followups: Array.isArray(data.followups) ? data.followups : [],
      citation_coverage: typeof data.citation_coverage === 'number'
        ? data.citation_coverage : 0,
      expandedSourceIndex: null,
    })
  } catch (error) {
    console.error('Chat error:', error)
    messages.value.push({
      role: 'assistant',
      content: 'Sorry, I encountered an error. Please try again.',
      formattedHtml: formatMessage('Sorry, I encountered an error. Please try again.'),
      sources: [],
      followups: [],
      citation_coverage: 0,
      expandedSourceIndex: null,
    })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

const CITATION_RE = /(^|\s)\[(\d{1,3})\]/g

const formatMessage = (content) => {
  const escape = (s) => s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  const escaped = escape(content || '')

  let html = escaped
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')

  html = html.replace(CITATION_RE, (match, ws, num) => {
    return `${ws}<a class="citation-chip" data-citation-index="${num}" href="#cite-${num}">[${num}]</a>`
  })

  return html
}

const toggleSource = (msg, src) => {
  const next = msg.expandedSourceIndex === src.index ? null : src.index
  msg.expandedSourceIndex = next
  if (next !== null) {
    nextTick(() => {
      const container = messagesContainer.value
      if (!container) return
      const el = container.querySelector(`[data-citation-index="${src.index}"]`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('flash')
        setTimeout(() => el.classList.remove('flash'), 1200)
      }
    })
  }
}

const onCitationClick = (event) => {
  const target = event.target
  if (!target.classList?.contains('citation-chip')) return
  event.preventDefault()
  const index = Number(target.dataset.citationIndex)
  if (!Number.isFinite(index)) return

  const messageEl = target.closest('.message.assistant')
  if (!messageEl) return
  const messageIndex = Array.from(
    messagesContainer.value.querySelectorAll('.message.assistant')
  ).indexOf(messageEl)
  if (messageIndex < 0) return

  let assistantCount = -1
  const msg = messages.value.find((m) => {
    if (m.role !== 'assistant') return false
    assistantCount += 1
    return assistantCount === messageIndex
  })
  if (!msg) return

  const src = (msg.sources || []).find((s) => s.index === index)
  if (!src) return
  toggleSource(msg, src)
}
</script>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.conversation-dropdown { position: relative; }
.conversation-dropdown :deep(.btn) { gap: 0.5rem; }
.conversation-dropdown :deep(.btn) .chevron { width: 12px; height: 12px; }

.dropdown-menu {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 0.5rem;
  min-width: 260px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-md);
  z-index: 100;
  max-height: 320px;
  overflow-y: auto;
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.dropdown-menu :deep(.btn) { justify-content: flex-start; }
.dropdown-new-chat :deep(.btn) { color: var(--primary); font-weight: 600; }
.dropdown-active :deep(.btn) { background: var(--primary-light); color: var(--primary); }
.conv-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}
.dropdown-divider {
  height: 1px;
  background: var(--border);
  margin: 4px 0;
}
.dropdown-empty {
  padding: 0.75rem;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 0.8125rem;
  font-style: italic;
}

.chat-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 2rem;
}

.messages-list {
  max-width: 880px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}
.messages-virtual {
  width: 100%;
  contain: strict;
}
.message-virtual-item {
  contain: layout style;
}

.message {
  display: flex;
  gap: 0.75rem;
  animation: fadeIn 0.25s ease;
}
.message.user { flex-direction: row-reverse; }

.message-avatar {
  width: 32px;
  height: 32px;
  padding: 7px;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  border: 1px solid var(--border);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.message.user .message-avatar { background: var(--primary-light); color: var(--primary); border-color: transparent; }
.message.assistant .message-avatar { background: var(--accent-light); color: var(--accent); border-color: transparent; }
.message-avatar :deep(svg) { width: 100%; height: 100%; }

.message-content { max-width: 72%; }
.message.user .message-content { text-align: right; }

.message-text {
  padding: 0.875rem 1.125rem;
  border-radius: var(--radius);
  font-size: 0.9375rem;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--border);
  text-align: left;
}
.message.user .message-text {
  background: var(--primary-light);
  border-color: transparent;
  color: var(--text-primary);
}
.message.assistant .message-text {
  background: var(--bg-primary);
  color: var(--text-primary);
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

.followups-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.875rem;
  padding: 0.625rem 0.75rem;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
}
.followups-label {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  letter-spacing: 0.05em;
  margin-right: 0.125rem;
}
.followup-chip-btn :deep(.btn) { font-size: 0.8125rem; max-width: 100%; }
.followup-chip-btn :deep(.btn) .btn-icon { width: 12px; height: 12px; }

.feedback-bar {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  margin-top: 0.5rem;
  padding-left: 0.125rem;
}
.feedback-bar :deep(.btn) {
  width: 28px;
  height: 28px;
  padding: 0;
}
.feedback-bar :deep(.btn) .btn-icon { width: 14px; height: 14px; }
.feedback-active :deep(.btn) {
  color: var(--primary-fg);
  background: var(--primary);
  border-color: var(--primary);
}
.feedback-status {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  margin-left: 0.375rem;
  letter-spacing: 0.02em;
}

.message-text :deep(code) {
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 0.875em;
}
.message-text :deep(strong) { color: var(--text-primary); font-weight: 600; }
.message-text :deep(em) {
  font-family: var(--font-display);
  font-style: italic;
}

.message-text :deep(.citation-chip) {
  display: inline-block;
  padding: 0 4px;
  margin: 0 1px;
  font-family: var(--font-mono);
  font-size: 0.75em;
  font-weight: 600;
  color: var(--primary);
  background: var(--primary-light);
  border: 1px solid transparent;
  border-radius: 3px;
  text-decoration: none;
  vertical-align: 1px;
  line-height: 1.4;
  transition: background-color var(--transition-fast), border-color var(--transition-fast), transform var(--transition-fast);
  cursor: pointer;
}
.message-text :deep(.citation-chip:hover) {
  border-color: var(--primary);
  background: var(--bg-primary);
}
.message-text :deep(.citation-chip.flash) {
  background: var(--accent);
  color: var(--primary-fg);
  transform: scale(1.18);
}

.sources-panel {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  margin-top: 0.625rem;
  padding: 0.625rem 0.75rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.sources-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.625rem;
}
.sources-label {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  font-weight: 700;
  color: var(--text-tertiary);
  letter-spacing: 0.1em;
}
.coverage-indicator {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  letter-spacing: 0.02em;
  color: var(--text-tertiary);
}
.coverage-bar {
  display: inline-block;
  width: 60px;
  height: 4px;
  border-radius: 2px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  overflow: hidden;
  position: relative;
}
.coverage-bar-fill {
  display: block;
  height: 100%;
  background: var(--text-tertiary);
  transition: width var(--transition), background-color var(--transition);
}
.coverage-indicator.coverage-good .coverage-bar-fill { background: var(--success, #10b981); }
.coverage-indicator.coverage-good .coverage-label { color: var(--success, #10b981); }
.coverage-indicator.coverage-okay .coverage-bar-fill { background: #f59e0b; }
.coverage-indicator.coverage-okay .coverage-label { color: #f59e0b; }
.coverage-indicator.coverage-low .coverage-bar-fill { background: var(--error, #ef4444); }
.coverage-indicator.coverage-low .coverage-label { color: var(--error, #ef4444); }

.sources-chips { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.quality-dot-inline { width: 6px !important; height: 6px !important; }

.source-card {
  margin-top: 0.625rem;
  padding: 0.875rem 1rem;
  background: var(--bg-primary);
  border: 1px solid var(--primary);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-md);
}
.source-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.source-card-meta {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  min-width: 0;
  flex: 1;
  flex-wrap: wrap;
}
.source-card-index {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--primary);
  flex-shrink: 0;
}
.source-card-title {
  font-family: var(--font-display);
  font-size: 0.9375rem;
  font-weight: 500;
  color: var(--text-primary);
  word-break: break-word;
}
.source-card-close-btn :deep(.btn) { font-size: 1.25rem; line-height: 1; padding: 0 0.5rem; height: 28px; }
.quality-badge { gap: 0.375rem !important; }
.quality-score {
  color: var(--text-tertiary);
  font-weight: 500;
  padding-left: 0.375rem;
  border-left: 1px solid var(--border);
}

.source-card-breadcrumb {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  margin-bottom: 0.625rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border-light);
}
.crumb-sep { margin: 0 0.25rem; color: var(--text-tertiary); }
.source-card-content {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  line-height: 1.6;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 360px;
  overflow-y: auto;
  padding: 0.5rem 0.75rem;
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
}
.source-card-footnote {
  margin-top: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  font-style: italic;
}

.message-text.typing { display: flex; gap: 4px; align-items: center; }
.message-text.typing .dot {
  width: 6px;
  height: 6px;
  background: var(--text-tertiary);
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out;
}
.message-text.typing .dot:nth-child(1) { animation-delay: 0s; }
.message-text.typing .dot:nth-child(2) { animation-delay: 0.2s; }
.message-text.typing .dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); }
  40% { transform: translateY(-6px); }
}

.input-area {
  padding: 1rem 2rem 1.5rem;
  background: var(--bg-primary);
  border-top: 1px solid var(--border);
}
.input-container {
  max-width: 880px;
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 0.625rem;
  padding: 0.625rem 0.625rem 0.625rem 0.875rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  transition: border-color var(--transition-fast);
}
.input-container:focus-within { border-color: var(--primary); }

.message-input {
  flex: 1;
  padding: 0.375rem 0;
  border: none;
  background: transparent;
  font-size: 0.9375rem;
  color: var(--text-primary);
  resize: none;
  max-height: 150px;
  line-height: 1.5;
  font-family: var(--font-sans);
}
.message-input:focus { outline: none; }
.message-input::placeholder { color: var(--text-tertiary); }
.send-btn :deep(.btn) { width: 36px; height: 36px; padding: 9px; }
.send-btn :deep(.btn) .btn-icon { width: 100%; height: 100%; }

.input-toolbar {
  max-width: 880px;
  margin: 0.625rem auto 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
</style>
