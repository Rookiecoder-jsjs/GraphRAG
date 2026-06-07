<template>
  <div class="search-page">
    <header class="page-header">
      <div class="header-content">
        <PageHeader
          :icon="SearchIcon"
          title="Semantic Search"
          subtitle="Find relevant content using natural language"
        />
      </div>

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
            placeholder="Search your documents..."
            @keyup.enter="handleSearch"
            :disabled="loading"
          />
        </div>
        <Button
          variant="primary"
          :loading="loading"
          :disabled="!query.trim()"
          @click="handleSearch"
          class="search-btn-trigger"
        >
          Search
        </Button>
      </div>
    </header>

    <div class="results-content">
      <LoadingState v-if="loading" message="Searching..." />

      <EmptyState
        v-else-if="results.length === 0 && hasSearched"
        :icon="SearchIcon"
        title="No Results Found"
        description="Try different keywords or upload more documents"
        decor="geometric"
        decor-tone="primary"
      />

      <EmptyState
        v-else-if="results.length === 0"
        variant="initial"
        :icon="SearchIcon"
        title="Search Your Knowledge Base"
        description="Enter a query to find relevant information from your documents"
      />

      <div v-else class="results-list">
        <div class="results-header">
          <span class="results-count">{{ results.length }} results found</span>
        </div>

        <div
          v-for="(result, index) in results"
          :key="index"
          class="result-card"
        >
          <div class="result-header">
            <h3 class="result-title">{{ result.title || result.filename || 'Document' }}</h3>
            <Tag shape="score">{{ (result.score || result.similarity || 0).toFixed(2) }}</Tag>
          </div>

          <div v-if="result.chunk" class="result-content">
            <p>{{ result.chunk }}</p>
          </div>

          <div v-if="result.metadata" class="result-meta">
            <span v-if="result.metadata.source" class="meta-item">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              {{ result.metadata.source }}
            </span>
            <span v-if="result.metadata.page" class="meta-item">
              Page {{ result.metadata.page }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, h } from 'vue'
import { searchApi } from '../api/search'
import { PageHeader, EmptyState, LoadingState, Button, Tag } from '../components/ui'

const SearchIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('circle', { cx: 11, cy: 11, r: 8 }),
    h('line', { x1: 21, y1: 21, x2: 16.65, y2: 16.65 })
  ])
}

const query = ref('')
const loading = ref(false)
const results = ref([])
const hasSearched = ref(false)

const handleSearch = async () => {
  if (!query.value.trim() || loading.value) return

  loading.value = true
  hasSearched.value = true

  try {
    const { data } = await searchApi.search(query.value, 10, true)
    const searchResults = data.chunks || data.results || []
    results.value = searchResults.map(r => ({
      chunk: r.content || r.chunk,
      title: r.metadata?.title || r.metadata?.source || r.filename || 'Document',
      score: 1 - (r.distance || 0),
      similarity: 1 - (r.distance || 0),
      metadata: r.metadata
    }))
  } catch (error) {
    console.error('Search error:', error)
    results.value = []
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.search-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
}

.page-header {
  padding: 1.75rem 2rem 1.5rem;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
}

.header-content {
  max-width: 1200px;
  margin: 0 auto 1.25rem;
}

.search-bar {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 0.875rem;
  padding-bottom: 0.25rem;
}

.search-input-wrapper {
  flex: 1;
  position: relative;
  border-bottom: 2px solid var(--border-strong);
  transition: border-color var(--transition);
}

.search-input-wrapper:focus-within { border-bottom-color: var(--primary); }

.search-icon {
  position: absolute;
  left: 0.25rem;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  color: var(--text-tertiary);
}

.search-input {
  width: 100%;
  padding: 0.75rem 0.5rem 0.75rem 2rem;
  border: none;
  font-family: var(--font-display);
  font-size: 1.125rem;
  color: var(--text-primary);
  background: transparent;
}

.search-input:focus { outline: none; }
.search-input::placeholder {
  color: var(--text-tertiary);
  font-style: italic;
}

.search-btn-trigger { min-width: 100px; }

.results-content {
  flex: 1;
  padding: 2rem;
  overflow-y: auto;
}

.results-list {
  max-width: 880px;
  margin: 0 auto;
  counter-reset: result-counter;
}

.results-header {
  margin-bottom: 1.25rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--border);
}

.results-count {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.result-card {
  position: relative;
  padding: 1.25rem 1.25rem 1.25rem 3rem;
  margin-bottom: 0;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  counter-increment: result-counter;
  transition: background-color var(--transition-fast);
}

.result-card:hover { background: var(--bg-tertiary); }
.result-card:last-child { border-bottom: none; }

.result-card::before {
  content: counter(result-counter, decimal-leading-zero);
  position: absolute;
  left: 1rem;
  top: 1.4rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  font-weight: 500;
}

.result-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.5rem;
}

.result-title {
  font-family: var(--font-display);
  font-size: 1.0625rem;
  font-weight: 500;
  color: var(--text-primary);
  letter-spacing: -0.005em;
  line-height: 1.3;
}

.result-content { margin-bottom: 0.5rem; }
.result-content p {
  font-size: 0.9375rem;
  color: var(--text-secondary);
  line-height: 1.6;
  border-left: 2px solid var(--border);
  padding-left: 0.875rem;
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 1rem;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 0.375rem;
}
.meta-item svg { width: 12px; height: 12px; }
</style>
