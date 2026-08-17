<template>
  <EmptyState
    variant="error"
    :title="title"
    :description="description"
    :action-label="retryLabel"
    :icon="ErrorIcon"
    @action="$emit('retry')"
  />
</template>

<script setup>
import { h } from 'vue'
import EmptyState from './EmptyState.vue'

defineProps({
  title: { type: String, default: 'Something went wrong' },
  description: { type: String, default: 'The request failed. Please try again.' },
  retryLabel: { type: String, default: 'Retry' }
})

defineEmits(['retry'])

const ErrorIcon = {
  render() {
    return h('svg', {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '1.75',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round'
    }, [
      h('circle', { cx: '12', cy: '12', r: '10' }),
      h('line', { x1: '12', y1: '8', x2: '12', y2: '12' }),
      h('line', { x1: '12', y1: '16', x2: '12.01', y2: '16' })
    ])
  }
}
</script>
