<template>
  <div class="paste-text-form">
    <div class="paste-row">
      <input
        v-model="title"
        class="title-input"
        type="text"
        placeholder="标题（可选，默认取首个 # 一级标题）"
        maxlength="200"
        :disabled="busy"
      />
      <Button variant="primary" size="sm" :loading="busy" @click="submit">
        {{ busy ? '入库中...' : '入库' }}
      </Button>
    </div>
    <textarea
      v-model="content"
      class="content-input"
      rows="8"
      placeholder="粘贴 Markdown 或纯文本，走与上传相同的切块/向量/图谱管线"
      :maxlength="MAX_CHARS"
      :disabled="busy"
    />
    <div class="char-counter" :class="{ over: content.length > MAX_CHARS }">
      {{ content.length }} / {{ MAX_CHARS }}
    </div>
  </div>
</template>

<script setup>
// FEAT-022: 粘贴文本入库表单。后端把内容落盘为 .md 并复用整条摄取管线
// （切块/向量/图谱 + SSE 进度），成功后把文档行交给父组件的
// startProgressTracking 即可复用进度弹窗。MAX_CHARS 只是前端镜像的提示值，
// 服务端 TEXT_INGEST_MAX_CHARS 才是权威上限。
import { ref } from 'vue'
import { Button } from '../ui'
import { documentApi } from '../../api/documents'

const MAX_CHARS = 200000

const emit = defineEmits(['ingested', 'error'])
const title = ref('')
const content = ref('')
const busy = ref(false)

const submit = async () => {
  if (busy.value) return
  if (!content.value.trim()) {
    emit('error', '请粘贴文档内容')
    return
  }
  busy.value = true
  try {
    const response = await documentApi.ingestText(title.value.trim(), content.value)
    emit('ingested', response.data)
    title.value = ''
    content.value = ''
  } catch (error) {
    console.error('Text ingest failed:', error)
    emit('error', error?.response?.data?.detail || '入库失败，请重试。')
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.paste-text-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.625rem 0.75rem;
  margin-bottom: 1rem;
  border: 1px dashed var(--border);
  border-radius: 10px;
  background: var(--surface);
}
.paste-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.title-input,
.content-input {
  min-width: 0;
  padding: 0.4rem 0.65rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--text-primary);
  font-size: 0.875rem;
  font-family: inherit;
}
.title-input {
  flex: 1;
}
.content-input {
  width: 100%;
  resize: vertical;
  line-height: 1.5;
}
.title-input:focus,
.content-input:focus {
  outline: none;
  border-color: var(--primary);
}
.title-input:disabled,
.content-input:disabled {
  opacity: 0.6;
}
.char-counter {
  align-self: flex-end;
  font-size: 0.75rem;
  color: var(--text-secondary);
}
.char-counter.over {
  color: var(--error);
}
</style>
