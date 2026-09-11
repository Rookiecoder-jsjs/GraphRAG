// FEAT-023: 把当前对话导出为 Markdown 文件（纯前端，无新 API）。
// 纯函数拆出便于日后单测；exportConversationMarkdown 负责浏览器下载。

/**
 * 解析会话标题：优先取会话列表里的标题；新会话可能还没刷进列表
 * （loadConversations 只在挂载/激活/打开下拉时跑），此时回退到首条
 * 用户消息截 30 字，再不行用「未命名会话」。
 */
export const resolveConversationTitle = (conversations, currentId, messages) => {
  const fromList = currentId
    ? conversations.find((c) => c.id === currentId)?.title
    : null
  if (fromList && fromList.trim()) return fromList.trim()
  const firstUser = messages.find((m) => m.role === 'user')
  const fallback = (firstUser?.content || '').replace(/\s+/g, ' ').trim().slice(0, 30)
  return fallback || '未命名会话'
}

/**
 * 逐轮拼 Markdown。用原始 content（不取 formattedHtml，避免带渲染产物）；
 * assistant 内容为空（流式占位/光标帧）时跳过；sources 只在流式回答上存在，
 * 历史会话 sources 为空数组，自然不渲染来源段。
 */
export const buildConversationMarkdown = (messages, title) => {
  const lines = [`# ${title}`, '']
  for (const msg of messages) {
    const content = (msg.content || '').trim()
    if (!content) continue
    lines.push(`## ${msg.role === 'user' ? '用户' : '助手'}`, '', content, '')
    const sources = msg.sources || []
    if (msg.role === 'assistant' && sources.length > 0) {
      lines.push('**参考来源：**', '')
      for (const src of sources) {
        const label = src.index != null ? `[${src.index}] ` : ''
        const titleText = src.title || src.document_id || '未命名来源'
        lines.push(`- ${label}${titleText}`)
      }
      lines.push('')
    }
  }
  return lines.join('\n').replace(/\n{3,}$/g, '\n')
}

// C0/C1 控制字符 + bidi 标记（LRM/RLM/嵌入/隔离符）——用码点区间判断，
// 避免在源码里写转义序列（会引入不可见字面控制符）。
const isControlOrBidi = (code) =>
  (code >= 0x00 && code <= 0x1f) ||
  (code >= 0x7f && code <= 0x9f) ||
  (code >= 0x200e && code <= 0x200f) ||
  (code >= 0x202a && code <= 0x202e) ||
  (code >= 0x2066 && code <= 0x2069)

const stripControlAndBidi = (text) =>
  Array.from(text)
    .filter((ch) => !isControlOrBidi(ch.codePointAt(0)))
    .join('')

/**
 * Windows/通用文件名安全化：剥非法字符、控制符与双向文本标记，
 * 去尾部点/空格（Windows 会静默吞掉），压缩空白，限 60 字符。
 */
export const sanitizeFilename = (raw) => {
  const cleaned = stripControlAndBidi(String(raw || ''))
    .replace(/[\\/:*?"<>|]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[. ]+$/g, '')
    .slice(0, 60)
    .replace(/[. ]+$/g, '')
  return cleaned || '对话导出'
}

/** 组装文件名并触发浏览器下载（Blob + a[download]，用完即回收）。 */
export const exportConversationMarkdown = (messages, conversations, currentConversationId) => {
  const title = resolveConversationTitle(conversations, currentConversationId, messages)
  const markdown = buildConversationMarkdown(messages, title)

  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`
  const filename = `${sanitizeFilename(title)}-${stamp}.md`

  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
