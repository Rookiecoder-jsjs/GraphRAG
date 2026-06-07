<template>
  <svg
    :class="['decor', `decor-${kind}`, `tone-${tone}`]"
    viewBox="0 0 200 200"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <template v-for="(el, i) in items" :key="i">
      <path
        v-if="el.tag === 'path'"
        :d="el.d"
        :stroke-dasharray="el.dash || undefined"
      />
      <circle
        v-else-if="el.tag === 'circle'"
        :cx="el.cx"
        :cy="el.cy"
        :r="el.r"
        :class="{ 'dot-accent': el.tone === 'accent' && el.fill }"
        :fill="el.fill ? (el.tone === 'accent' ? undefined : 'currentColor') : 'none'"
        :stroke="el.fill ? 'none' : 'currentColor'"
        :stroke-dasharray="el.dash || undefined"
      />
      <line
        v-else-if="el.tag === 'line'"
        :x1="el.x1"
        :y1="el.y1"
        :x2="el.x2"
        :y2="el.y2"
        :stroke-dasharray="el.dash || undefined"
      />
      <g v-else-if="el.tag === 'g'">
        <line
          v-for="(sub, j) in el.children"
          :key="j"
          :x1="sub.x1"
          :y1="sub.y1"
          :x2="sub.x2"
          :y2="sub.y2"
        />
      </g>
    </template>
  </svg>
</template>

<script setup>
import { computed } from 'vue'
import { shapes } from './shapes.js'

const props = defineProps({
  kind: { type: String, default: '' },   // 'contour' | 'geometric' | 'paper'
  tone: { type: String, default: 'muted' } // 'primary' | 'accent' | 'muted'
})

const items = computed(() => (props.kind && shapes[props.kind]) || [])
</script>

<style scoped>
.dot-accent { fill: var(--accent); }
</style>
