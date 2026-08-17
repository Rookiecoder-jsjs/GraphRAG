<template>
  <div class="chat-page">
    <PageHeader
      :icon="MessageIcon"
      title="AI 对话"
      subtitle="针对你的知识图谱提问"
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
            {{ currentConversationId ? '历史会话' : '新对话' }}
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
              新对话
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
              <span class="conv-title">{{ conv.title || '未命名会话' }}</span>
            </Button>
            <div v-if="conversations.length === 0" class="dropdown-empty">
              暂无会话历史
            </div>
            <div class="dropdown-divider" />
            <Button
              variant="ghost"
              size="sm"
              block
              :icon="ClockIcon"
              icon-position="left"
              class="dropdown-manage"
              @click="goHistory"
            >
              管理全部会话
            </Button>
          </div>
        </div>
      </template>
    </PageHeader>

    <div class="chat-content">
      <div class="messages-container" ref="messagesContainer">
        <EmptyState
          v-if="messages.length === 0"
          :icon="MessageIcon"
          title="开始对话"
          description="就你的文档与知识图谱提问"
        />

        <div v-else class="messages-list">
          <div
            v-for="(msg, idx) in messages"
            :key="idx"
            class="message-item"
          >
              <div
                class="message"
                :class="msg.role"
              >
                <div class="message-avatar">
                  <UserIcon v-if="msg.role === 'user'" />
                  <BoltIcon v-else />
                </div>
                <div class="message-content">
                  <div v-if="msg.role === 'assistant' && msg.thinking" class="thinking-block">
                    <button
                      type="button"
                      class="thinking-toggle"
                      @click="msg.thinkingExpanded = !msg.thinkingExpanded"
                    >
                      <ChevronIcon class="thinking-chevron" :class="{ open: msg.thinkingExpanded }" />
                      <BoltIcon class="thinking-bolt" />
                      <span v-if="msg.streaming && !msg.content" class="thinking-status">思考中…</span>
                      <span v-else-if="msg.thinkingMs != null" class="thinking-status">
                        思考了 {{ formatSeconds(msg.thinkingMs) }}
                      </span>
                      <span v-else class="thinking-status">推理过程</span>
                    </button>
                    <pre v-show="msg.thinkingExpanded" class="thinking-content">{{ msg.thinking }}</pre>
                  </div>
                  <div class="message-text" v-html="msg.formattedHtml" />
                  <div
                    v-if="msg.role === 'assistant' && msg.timing"
                    class="timing-meta"
                    :class="{ 'timing-error': !msg.timing.running && msg.timing.error }"
                  >
                    <template v-if="msg.timing.running">
                      <span class="timing-dot" />
                      <span class="timing-live">{{ formatSeconds(msg.timing.elapsedMs) }}</span>
                    </template>
                    <template v-else>
                      <ClockIcon class="timing-icon" />
                      <template v-if="msg.timing.ttftMs != null">
                        <span>首字 {{ formatSeconds(msg.timing.ttftMs) }}</span>
                        <span class="timing-sep">·</span>
                      </template>
                      <span class="timing-total">总耗时 {{ formatSeconds(msg.timing.totalMs ?? msg.timing.elapsedMs) }}</span>
                    </template>
                  </div>
                  <div
                    v-if="msg.role === 'assistant' && msg.sources && msg.sources.length"
                    class="sources-panel"
                  >
                    <div class="sources-header">
                      <span class="sources-label">参考来源</span>
                      <span
                        v-if="msg.citation_coverage !== undefined && msg.citation_coverage !== null"
                        class="coverage-indicator"
                        :class="coverageTone(msg.citation_coverage)"
                        :title="`回答引用了 ${Math.round((msg.citation_coverage || 0) * 100)}% 的可用来源`"
                      >
                        <span class="coverage-bar">
                          <span
                            class="coverage-bar-fill"
                            :style="{ width: ((msg.citation_coverage || 0) * 100) + '%' }"
                          />
                        </span>
                        <span class="coverage-label">
                          引用 {{ Math.round((msg.citation_coverage || 0) * 100) }}%
                        </span>
                      </span>
                    </div>
                    <div class="sources-chips">
                      <Tag
                        v-for="src in msg.sources"
                        :key="src.index"
                        shape="pill"
                        clickable
                        :active="msg.expandedSourceIndex === src.index"
                        :title="`${src.title}——点击${msg.expandedSourceIndex === src.index ? '收起' : '展开'}`"
                        @click="toggleSource(msg, src)"
                      >
                        <template #dot>
                          <Dot
                            :tone="src.quality === 'high' ? 'success' : src.quality === 'medium' ? 'warning' : 'error'"
                            class="quality-dot-inline"
                          />
                        </template>
                        [{{ src.index }}] {{ src.title || '文档 ' + src.document_id?.slice(0, 8) }}
                      </Tag>
                    </div>

                    <div
                      v-for="src in msg.sources"
                      v-show="msg.expandedSourceIndex === src.index"
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
                            {{ qualityLabel(src.quality) }}
                            <span v-if="src.relevance_score != null" class="quality-score">
                              {{ Math.round((src.relevance_score || 0) * 100) }}%
                            </span>
                          </Tag>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon-position="only"
                          @click="toggleSource(msg, src)"
                          title="关闭"
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
                        摘录已截断——原文片段更长。
                      </div>
                    </div>
                  </div>

                  <div
                    v-if="msg.role === 'assistant' && msg.id"
                    class="feedback-bar"
                  >
                    <Button
                      variant="ghost"
                      size="sm"
                      :icon="ThumbsUpIcon"
                      icon-position="only"
                      :class="{ 'feedback-active': msg.rating === 'up' }"
                      :title="msg.rating === 'up' ? '取消点赞' : '回答有帮助'"
                      @click="onFeedback(msg, 'up')"
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      :icon="ThumbsDownIcon"
                      icon-position="only"
                      :class="{ 'feedback-active': msg.rating === 'down' }"
                      :title="msg.rating === 'down' ? '取消踩' : '回答无帮助'"
                      @click="onFeedback(msg, 'down')"
                    />
                    <span v-if="msg.feedbackSaving" class="feedback-status">保存中…</span>
                    <span v-else-if="msg.rating" class="feedback-status">
                      {{ msg.rating === 'up' ? '感谢反馈' : '已标记为无帮助' }}
                    </span>
                  </div>
                </div>
              </div>
          </div>

          <div v-if="loading && !(messages.length && messages[messages.length - 1].streaming)" class="message assistant">
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
            placeholder="输入你的问题……"
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
          <Switch v-model="useGraphRag" label="图谱" />
          <Switch v-model="useCompare" label="对比" />
          <Switch v-model="useThinking" label="深度思考" />
          <span v-if="useThinking" class="thinking-mode-hint">
            思考模式已开启——首字等待会明显变长
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onActivated, onDeactivated, onUnmounted, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { chatApi } from '../api/chat'
import { createSseParser } from '../utils/sse'
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

const route = useRoute()
const router = useRouter()

// Deep-link from the history-management page: /chat?conversation=<id> loads
// that conversation on arrival (and on re-activation, e.g. picking a different
// one from the history page while ChatPage stays cached in KeepAlive).
const handleDeepLink = () => {
  const id = route.query.conversation
  if (id && String(id) !== currentConversationId.value) {
    loadConversation(String(id))
  }
}

const goHistory = () => {
  showDropdown.value = false
  router.push('/chat/history')
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
  handleDeepLink()
  scrollToBottom()
})

onActivated(() => {
  loadConversations()
  handleDeepLink()
})

onDeactivated(() => {
  // Layout keeps this page alive in cache, so route switches fire
  // onDeactivated (NOT onUnmounted): abort the in-flight stream here or it
  // keeps pulling tokens for minutes in the background. The sendMessage
  // finally-block settles the stopwatch once the abort lands.
  abortActiveStream()
})

// Abort any in-flight stream. Called on route leave AND on in-page switches
// (loadConversation / startNewChat): without the latter, switching to
// another conversation mid-stream would let the old stream keep mutating a
// detached message object, keep `loading` stuck until the stream finishes,
// and — after "new chat" — even overwrite currentConversationId back to the
// old conversation on its `done` frame, routing the next message into the
// wrong conversation.
const abortActiveStream = () => {
  if (activeStreamAbort) {
    activeStreamAbort.abort()
    activeStreamAbort = null
  }
  if (activeResponseTimer) {
    clearInterval(activeResponseTimer)
    activeResponseTimer = null
  }
}

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  messagesContainer.value?.removeEventListener('click', onCitationClick)
  if (activeResponseTimer) {
    clearInterval(activeResponseTimer)
    activeResponseTimer = null
  }
})

const messages = ref([])
const inputMessage = ref('')
const loading = ref(false)
const useGraphRag = ref(false)
const useCompare = ref(false)
// Deep-thinking toggle: persisted so a returning user keeps their choice.
// Off by default — thinking mode trades a much slower first token for
// occasionally better answers.
const useThinking = ref(localStorage.getItem('chat.useThinking') === '1')
watch(useThinking, (v) => localStorage.setItem('chat.useThinking', v ? '1' : '0'))
const messagesContainer = ref(null)
const currentConversationId = ref(null)
const conversations = ref([])
const showDropdown = ref(false)

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
  // Abort any in-flight stream first — see abortActiveStream().
  abortActiveStream()
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
  // Abort any in-flight stream first — see abortActiveStream().
  abortActiveStream()
  messages.value = []
  currentConversationId.value = null
  showDropdown.value = false
}


const coverageTone = (ratio) => {
  const r = Number(ratio) || 0
  if (r >= 0.5) return 'coverage-good'
  if (r >= 0.25) return 'coverage-okay'
  return 'coverage-low'
}

const QUALITY_LABELS = { high: '高', medium: '中', low: '低' }
const qualityLabel = (q) => QUALITY_LABELS[q] || q
const qualityTitle = (src) => {
  if (!src || !src.quality) return ''
  const label = QUALITY_LABELS[src.quality] || src.quality
  const score = src.relevance_score
  if (score === null || score === undefined) {
    return `质量：${label}（无相关度评分）`
  }
  return `质量：${label}（相关度 ${Math.round(score * 100)}%）`
}

const toggleDropdown = () => {
  showDropdown.value = !showDropdown.value
  if (showDropdown.value) {
    loadConversations()
  }
}

const scrollToBottom = async () => {
  await nextTick()
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

// Blinking caret appended to the assistant bubble while tokens stream in.
const CURSOR_HTML = '<span class="stream-cursor"></span>'

// Two stream-failure classes drive sendMessage's fallback decision:
//   - StreamConnectError (retryable): the backend generator NEVER started
//     (fetch threw or the HTTP response wasn't ok), so the user message was
//     NOT saved — falling back to the non-streaming endpoint is safe.
//   - StreamInterruptedError (non-retryable): the stream WAS established
//     (HTTP 200 + body) but died before producing content — the backend
//     already saved the user message, so a fallback would double-save it.
class StreamConnectError extends Error {
  constructor(message) {
    super(message)
    this.name = 'StreamConnectError'
    this.retryable = true
  }
}
class StreamInterruptedError extends Error {
  constructor(message) {
    super(message)
    this.name = 'StreamInterruptedError'
    this.retryable = false
  }
}

// Parse the SSE byte stream from /api/chat/stream into typed callbacks.
// Protocol (see backend app/api/chat.py):
//   event: sources   -> data: {"sources": [...]}   (sent FIRST, before text)
//   event: thinking  -> data: {"text": "..."}       (reasoning, Deep Think on)
//   (message)        -> data: {"chunk": "..."}      (streamed body tokens)
//   event: error     -> data: {"error": "..."}      (terminal, provider failed)
//   event: done      -> data: {"conversation_id","sources","citation_coverage"}
const streamChat = async (body, handlers, signal) => {
  let response
  try {
    response = await chatApi.stream(
      body.message, body.conversationId, body.useGraphRag, body.compareMode, body.enableThinking, signal
    )
  } catch (error) {
    // fetch itself failed (network down, server unreachable, ...) — the
    // backend generator never started, so the user message was NOT saved.
    // Tag it retryable so sendMessage can safely fall back.
    throw new StreamConnectError(`Connection failed: ${error?.message || error}`)
  }
  if (!response.ok || !response.body) {
    // HTTP error before any SSE frame — same as above: the backend never ran.
    throw new StreamConnectError(`Stream request failed with status ${response.status}`)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  // Frame parsing lives in utils/sse.js (unit-tested); here we just route the
  // typed events to our handlers. Track whether we ever saw real content or a
  // done frame so an established-but-empty stream can be detected below.
  let sawContent = false
  let sawDone = false
  let sawError = false
  const parser = createSseParser((event, payload) => {
    if (event === 'sources') handlers.onSources(payload?.sources)
    else if (event === 'thinking') handlers.onThinking?.(payload?.text)
    else if (event === 'error') {
      sawError = true
      handlers.onError?.(payload?.error)
    }
    else if (event === 'done') {
      sawDone = true
      handlers.onDone(payload)
    }
    else if (payload && typeof payload.chunk === 'string') {
      if (payload.chunk) sawContent = true
      handlers.onChunk(payload.chunk)
    }
  })

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }))
    }
    parser.flush()
  } catch (error) {
    // The stream WAS established (backend already saved the user message)
    // but the connection dropped mid-stream. NOT retryable — falling back to
    // the non-streaming endpoint would double-save the user message.
    throw new StreamInterruptedError(`Stream interrupted: ${error?.message || error}`)
  }

  // Established stream that ended without content AND without a done frame:
  // the backend saved the user turn but produced nothing. Same rule as above.
  // A terminal `event: error` frame is an exception: it IS the backend's
  // typed end-of-stream, and sendMessage's streamErred branch owns the UX.
  if (!sawContent && !sawDone && !sawError) {
    throw new StreamInterruptedError('Stream ended before producing content')
  }
}

// Coalesce per-token scrolls into one per animation frame so fast streams
// don't thrash the virtualizer.
let scrollScheduled = false
const scheduleScroll = () => {
  if (scrollScheduled) return
  scrollScheduled = true
  requestAnimationFrame(() => {
    scrollScheduled = false
    scrollToBottom()
  })
}

// ---------------------------------------------------------------------------
// Response timing chip: a client-side stopwatch attached to the assistant
// bubble. Starts the moment the message is sent, marks time-to-first-token on
// the first streamed chunk, and freezes the instant the answer completes
// (the `done` event — trailing follow-up chips don't count toward it).
// Measured with performance.now(); purely presentational, never persisted.
// ---------------------------------------------------------------------------
let activeResponseTimer = null
// AbortController of the in-flight chat stream (if any) - aborted when the
// page is deactivated so a navigated-away chat stops pulling tokens.
let activeStreamAbort = null

const formatSeconds = (ms) => `${((ms ?? 0) / 1000).toFixed(2)}s`

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

  // Placeholder assistant bubble filled token-by-token. Grab the reactive
  // proxy from the array (not the raw literal) so in-place mutation re-renders.
  messages.value.push({
    role: 'assistant',
    content: '',
    formattedHtml: CURSOR_HTML,
    sources: [],
    citation_coverage: 0,
    expandedSourceIndex: null,
    streaming: true,
    // Deep-thinking trace (empty unless Deep Think is on and the model
    // actually produced reasoning). thinkingMs freezes when the answer
    // body starts; expanded auto-opens while thinking, auto-collapses after.
    thinking: '',
    thinkingMs: null,
    thinkingExpanded: false,
    timing: { running: true, elapsedMs: 0, ttftMs: null, totalMs: null, error: false },
  })
  const assistantMsg = messages.value[messages.value.length - 1]
  await scrollToBottom()

  // Live stopwatch: tick every 100ms so the chip visibly counts up.
  const t0 = performance.now()
  const timer = setInterval(() => {
    assistantMsg.timing.elapsedMs = performance.now() - t0
  }, 100)
  activeResponseTimer = timer

  // Freeze the chip the moment the turn is logically done. Idempotent —
  // onDone calls it first; the finally block calls it again as a safety
  // net for error paths, and no-ops if already settled.
  const settleTimer = (errored) => {
    if (!assistantMsg.timing.running) return
    clearInterval(timer)
    if (activeResponseTimer === timer) activeResponseTimer = null
    assistantMsg.timing.running = false
    assistantMsg.timing.totalMs = performance.now() - t0
    assistantMsg.timing.error = Boolean(errored)
  }

  const render = () => {
    assistantMsg.formattedHtml =
      formatMessage(assistantMsg.content) + (assistantMsg.streaming ? CURSOR_HTML : '')
    scheduleScroll()
  }

  let gotChunk = false
  let hadError = false
  // Typed provider-error frame from the backend (event: error): a FINAL
  // state - don't retry via the non-streaming endpoint, it would re-bill
  // and likely fail the same way.
  let streamErred = false
  const streamAbort = new AbortController()
  activeStreamAbort = streamAbort
  try {
    await streamChat(
      {
        message,
        conversationId: currentConversationId.value,
        useGraphRag: useGraphRag.value,
        compareMode: useCompare.value,
        enableThinking: useThinking.value,
      },
      {
        onSources: (sources) => { assistantMsg.sources = sources || [] },
        onThinking: (text) => {
          if (typeof text !== 'string' || !text) return
          // Auto-expand while the model is reasoning so the user can watch
          // it work; the first answer chunk collapses it again (below).
          if (!assistantMsg.thinking) assistantMsg.thinkingExpanded = true
          assistantMsg.thinking += text
          scheduleScroll()
        },
        onChunk: (chunk) => {
          if (!gotChunk) {
            gotChunk = true
            // Time-to-first-token: how long the send→retrieval→first-byte
            // path took. Anchored to the first ANSWER chunk (not reasoning)
            // so Deep Think on/off numbers are directly comparable. Pairs
            // with the backend's build_rag_context timing log for A/Bs.
            assistantMsg.timing.ttftMs = performance.now() - t0
            if (assistantMsg.thinking) {
              assistantMsg.thinkingMs = performance.now() - t0
              assistantMsg.thinkingExpanded = false
            }
          }
          assistantMsg.content += chunk
          render()
        },
        onDone: (d) => {
          if (d?.conversation_id && !currentConversationId.value) {
            currentConversationId.value = d.conversation_id
          }
          if (Array.isArray(d?.sources) && d.sources.length) assistantMsg.sources = d.sources
          if (typeof d?.citation_coverage === 'number') {
            assistantMsg.citation_coverage = d.citation_coverage
          }
          // The answer is fully streamed and saved — stop the clock NOW so
          // trailing follow-up generation doesn't pad the number.
          settleTimer(false)
        },
        onError: (err) => {
          console.error('Chat stream error:', err)
          streamErred = true
          hadError = true
        },
      },
      streamAbort.signal
    )
    if (streamErred) {
      // Terminal provider error: keep whatever partial answer rendered.
      if (!gotChunk) assistantMsg.content = '抱歉，生成回答失败，请重试。'
    }
  } catch (error) {
    if (streamAbort.signal.aborted) {
      // Page deactivated mid-stream: intentional, keep whatever rendered
      // and do NOT retry via the non-streaming endpoint.
      hadError = true
      if (!gotChunk) assistantMsg.content = '回答已中断。'
    } else if (error && error.retryable) {
      // Connection/HTTP failure BEFORE the backend generator started — the
      // user message was NOT saved, so falling back to the non-streaming
      // endpoint is safe (and needed so the user isn't left staring at an
      // empty bubble).
      try {
        const { data } = await chatApi.send(
          message, currentConversationId.value, true, useGraphRag.value, useCompare.value
        )
        if (data.conversation_id && !currentConversationId.value) {
          currentConversationId.value = data.conversation_id
        }
        assistantMsg.content = data.message || '（无回答）'
        assistantMsg.sources = data.sources || []
        assistantMsg.citation_coverage =
          typeof data.citation_coverage === 'number' ? data.citation_coverage : 0
      } catch (fallbackError) {
        console.error('Chat error:', fallbackError)
        assistantMsg.content = '抱歉，出了点问题，请重试。'
        assistantMsg.sources = []
        hadError = true
      }
    } else if (!gotChunk) {
      // The stream WAS established (backend already saved the user message)
      // but died before any content arrived. A non-streaming fallback would
      // double-save the user message into history — surface the interruption
      // and let the user resend instead.
      console.error('Chat stream interrupted:', error)
      hadError = true
      assistantMsg.content = '网络连接中断，请重新发送。'
    } else {
      // Mid-stream failure after partial content: keep whatever already
      // rendered.
      console.error('Chat stream interrupted:', error)
      hadError = true
    }
  } finally {
    if (activeStreamAbort === streamAbort) activeStreamAbort = null
    settleTimer(hadError)
    assistantMsg.streaming = false
    assistantMsg.formattedHtml = formatMessage(assistantMsg.content)
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

/* Blinking caret shown at the end of the assistant bubble while streaming. */
.stream-cursor {
  display: inline-block;
  width: 0.5rem;
  height: 1.05em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--primary, #6366f1);
  border-radius: 1px;
  animation: streamCursorBlink 1s steps(2, start) infinite;
}
@keyframes streamCursorBlink {
  to { visibility: hidden; }
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Live response-time chip under the assistant bubble: a pulsing dot and a
   counting stopwatch while streaming, settling into quiet mono microcopy
   afterwards — same visual register as the SOURCES / coverage meta. */
.timing-meta {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 0.375rem;
  padding-left: 0.125rem;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  letter-spacing: 0.02em;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  animation: fadeIn 0.25s ease;
}
.timing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
  animation: timingPulse 0.9s ease-in-out infinite;
}
@keyframes timingPulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50%      { transform: scale(0.5); opacity: 0.4; }
}
.timing-live { color: var(--primary); font-weight: 600; }
.timing-icon { width: 11px; height: 11px; flex-shrink: 0; }
.timing-sep { opacity: 0.5; }
.timing-total { color: var(--text-secondary); font-weight: 600; }
.timing-meta.timing-error .timing-total,
.timing-meta.timing-error .timing-icon { color: var(--error, #ef4444); }

/* Deep-thinking trace: a collapsible block above the answer body. Auto-opens
   while reasoning streams in, collapses to a one-line "Thought for X.XXs"
   summary when the answer starts. Same quiet microcopy register as the
   timing chip — present, but never louder than the answer. */
.thinking-block {
  margin-bottom: 0.5rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  overflow: hidden;
  animation: fadeIn 0.25s ease;
}
.thinking-toggle {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  width: 100%;
  padding: 0.375rem 0.625rem;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 0.75rem;
  color: var(--text-tertiary);
  text-align: left;
  transition: color 0.15s ease;
}
.thinking-toggle:hover { color: var(--text-secondary); }
.thinking-chevron {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  transition: transform 0.15s ease;
}
.thinking-chevron.open { transform: rotate(180deg); }
.thinking-bolt { width: 11px; height: 11px; flex-shrink: 0; }
.thinking-status { font-weight: 600; }
.thinking-content {
  margin: 0;
  padding: 0.5rem 0.75rem 0.625rem 1.75rem;
  border-top: 1px dashed var(--border);
  font-size: 0.75rem;
  line-height: 1.55;
  color: var(--text-tertiary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 15rem;
  overflow-y: auto;
}

/* Right-aligned nudge in the input toolbar when Deep Think is on. */
.thinking-mode-hint {
  margin-left: auto;
  font-size: 0.75rem;
  color: var(--text-tertiary);
  animation: fadeIn 0.25s ease;
}

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
.coverage-indicator.coverage-okay .coverage-bar-fill { background: var(--warning); }
.coverage-indicator.coverage-okay .coverage-label { color: var(--warning); }
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
