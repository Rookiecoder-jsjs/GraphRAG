// Standalone tests for the streaming chat SSE parser (src/utils/sse.js).
//
// Plain Node, no framework. As with test_timeline_anim.cjs, the ESM helpers
// (export function ...) can't be require()'d in CommonJS, so they are
// re-implemented here line-for-line and kept in lockstep with the source.
//
// Run with:
//     node frontend/tests/test_sse.cjs
// Exits 0 on success, 1 on any failure.

function parseSseBlock(block) {
  let eventName = 'message'
  const dataLines = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (dataLines.length === 0) return null
  let payload = null
  try { payload = JSON.parse(dataLines.join('\n')) } catch { payload = null }
  return { event: eventName, data: payload }
}

function createSseParser(onEvent) {
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

let failures = 0
function check(name, cond) {
  if (cond) console.log('  PASS ' + name)
  else { failures += 1; console.error('  FAIL ' + name) }
}

// 1. default (unnamed) event carrying a chunk payload
{
  const r = parseSseBlock('data: {"chunk":"Hello"}')
  check('default event name is "message"', r.event === 'message')
  check('chunk payload parsed', r.data && r.data.chunk === 'Hello')
}

// 2. named event
{
  const r = parseSseBlock('event: sources\ndata: {"sources":[1,2]}')
  check('named event parsed',
    r.event === 'sources' && Array.isArray(r.data.sources) && r.data.sources.length === 2)
}

// 3. empty / comment-only blocks yield null
check('empty block -> null', parseSseBlock('') === null)
check('comment-only block -> null', parseSseBlock(': keepalive') === null)

// 4. invalid JSON keeps the event name but nulls the data
{
  const r = parseSseBlock('event: done\ndata: {not json')
  check('invalid JSON -> data null', r.event === 'done' && r.data === null)
}

// 5. a full stream routes events in order and reassembles chunks
{
  const events = []
  const p = createSseParser((e, d) => events.push([e, d]))
  const stream =
    'event: sources\ndata: {"sources":["s"]}\n\n' +
    'data: {"chunk":"He"}\n\n' +
    'data: {"chunk":"llo"}\n\n' +
    'event: followups\ndata: {"followups":["q?"]}\n\n' +
    'event: done\ndata: {"conversation_id":"c1","sources":["s"],"citation_coverage":0.5}\n\n'
  p.feed(stream)
  p.flush()
  check('5 events emitted', events.length === 5)
  check('first event is sources', events[0][0] === 'sources')
  const text = events.filter(([e]) => e === 'message').map(([, d]) => d.chunk).join('')
  check('chunks reassemble to "Hello"', text === 'Hello')
  const done = events.find(([e]) => e === 'done')
  check('done carries id + coverage',
    done && done[1].conversation_id === 'c1' && done[1].citation_coverage === 0.5)
}

// 6. blocks split across arbitrary feed boundaries still parse
{
  const events = []
  const p = createSseParser((e, d) => events.push([e, d]))
  p.feed('data: {"chu')            // partial block
  p.feed('nk":"wor"}\n\ndata:')    // completes #1, starts #2 mid-line
  p.feed(' {"chunk":"ld"}\n\n')
  p.flush()
  const text = events.filter(([e]) => e === 'message').map(([, d]) => d.chunk).join('')
  check('split feeds reassemble to "world"', text === 'world')
}

// 7. a trailing block with no final blank line is drained by flush()
{
  const events = []
  const p = createSseParser((e, d) => events.push([e, d]))
  p.feed('data: {"chunk":"tail"}')  // no trailing \n\n
  check('no event emitted before flush', events.length === 0)
  p.flush()
  check('flush drains trailing block', events.length === 1 && events[0][1].chunk === 'tail')
}

// 8. a chunk containing an escaped newline stays a single data line
{
  // The backend sends json.dumps, so a real newline arrives escaped as \n.
  const r = parseSseBlock('data: {"chunk":"line1\\nline2"}')
  check('escaped newline decoded to real newline', r.data.chunk === 'line1\nline2')
}

if (failures) {
  console.error('\n' + failures + ' check(s) FAILED')
  process.exit(1)
}
console.log('\nAll SSE parser checks passed.')
