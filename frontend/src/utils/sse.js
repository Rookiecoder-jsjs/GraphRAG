// Minimal Server-Sent Events parser for the streaming chat endpoint.
//
// Pure, framework-free helpers so the framing logic can be unit-tested in
// plain Node (see tests/test_sse.cjs, which mirrors these line-for-line).
//
// The backend (app/api/chat.py) emits standard SSE: named events via
// `event: <name>` plus one or more `data: <json>` lines, blocks separated by
// a blank line (`\n\n`). The default (unnamed) event carries body tokens as
// `data: {"chunk": "..."}`.

/**
 * Parse a single SSE block (the text between blank-line separators).
 * Returns `{ event, data }` where `event` defaults to "message" and `data`
 * is the JSON-parsed payload (or null if the block has no data / invalid
 * JSON). Returns null for empty/heartbeat blocks.
 */
export function parseSseBlock(block) {
  let eventName = 'message'
  const dataLines = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (dataLines.length === 0) return null
  let payload = null
  try {
    payload = JSON.parse(dataLines.join('\n'))
  } catch {
    payload = null
  }
  return { event: eventName, data: payload }
}

/**
 * Create a streaming parser that buffers arbitrary text chunks (which may
 * split SSE blocks mid-frame) and invokes `onEvent(event, data)` for each
 * complete block. Call `flush()` once the stream ends to drain any trailing
 * block that lacked a final blank line.
 */
export function createSseParser(onEvent) {
  let buffer = ''

  const emit = (block) => {
    const parsed = parseSseBlock(block)
    if (parsed) onEvent(parsed.event, parsed.data)
  }

  return {
    feed(text) {
      buffer += text
      let sep
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        emit(buffer.slice(0, sep))
        buffer = buffer.slice(sep + 2)
      }
    },
    flush() {
      if (buffer.trim()) emit(buffer)
      buffer = ''
    },
  }
}
