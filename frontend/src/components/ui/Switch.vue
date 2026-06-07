<template>
  <label class="switch" :class="{ disabled }">
    <input
      v-bind="$attrs"
      type="checkbox"
      class="switch-input"
      :checked="modelValue"
      :disabled="disabled"
      @change="$emit('update:modelValue', $event.target.checked)"
    >
    <span v-if="label" class="switch-label">{{ label }}</span>
    <span class="switch-track" :class="{ on: modelValue }">
      <span class="switch-thumb" />
    </span>
  </label>
</template>

<script setup>
defineOptions({ inheritAttrs: false })
defineProps({
  modelValue: { type: Boolean, default: false },
  label: { type: String, default: '' },
  disabled: { type: Boolean, default: false }
})
defineEmits(['update:modelValue'])
</script>

<style scoped>
.switch {
  display: inline-flex;
  align-items: center;
  gap: 0.625rem;
  cursor: pointer;
  user-select: none;
  font-size: 0.8125rem;
  color: var(--text-secondary);
}
.switch.disabled { cursor: not-allowed; opacity: 0.5; }
.switch-input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}
.switch-track {
  position: relative;
  width: 32px;
  height: 18px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 999px;
  transition: background-color var(--transition), border-color var(--transition);
  flex-shrink: 0;
}
.switch-track.on {
  background: var(--primary);
  border-color: var(--primary);
}
.switch-thumb {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 14px;
  height: 14px;
  background: var(--bg-primary);
  border-radius: 50%;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
  transition: transform var(--transition);
}
.switch-track.on .switch-thumb {
  transform: translateX(14px);
}
.switch-label {
  font-weight: 500;
}
</style>
