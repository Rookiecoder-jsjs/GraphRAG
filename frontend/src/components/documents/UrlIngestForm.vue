<template>
  <div class="url-ingest-form">
    <input
      v-model="url"
      class="url-input"
      type="url"
      placeholder="粘贴网页链接，例如 https://example.com/article"
      :disabled="busy"
      @keydown.enter.prevent="submit"
    />
    <Button variant="primary" size="sm" :loading="busy" @click="submit">
      {{ busy ? '抓取中...' : '抓取入库' }}
    </Button>
  </div>
</template>

<script setup>
// FEAT-019: URL 摄取表单。后端复用文档上传的整条摄取管线（切块/向量/图谱/
// 实体提取 + SSE 进度），因此成功后直接把返回的文档行交给父组件的
// startProgressTracking 即可复用进度弹窗。
import { ref } from 'vue'
import { Button } from '../ui'
import { documentApi } from '../../api/documents'

const emit = defineEmits(['ingested', 'error'])
const url = ref('')
const busy = ref(false)

const submit = async () => {
  if (busy.value) return
  const target = url.value.trim()
  if (!/^https?:\/\//i.test(target)) {
    emit('error', '请输入以 http:// 或 https:// 开头的链接')
    return
  }
  busy.value = true
  try {
    const response = await documentApi.ingestUrl(target)
    emit('ingested', response.data)
    url.value = ''
  } catch (error) {
    console.error('URL ingest failed:', error)
    emit('error', error?.response?.data?.detail || '抓取失败，请重试。')
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.url-ingest-form {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 0.75rem;
  margin-bottom: 1rem;
  border: 1px dashed var(--border);
  border-radius: 10px;
  background: var(--surface);
}
.url-input {
  flex: 1;
  min-width: 0;
  padding: 0.4rem 0.65rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--text-primary);
  font-size: 0.875rem;
}
.url-input:focus {
  outline: none;
  border-color: var(--primary);
}
.url-input:disabled {
  opacity: 0.6;
}
</style>
