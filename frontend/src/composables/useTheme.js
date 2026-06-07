import { ref, watch, readonly } from 'vue'

const STORAGE_KEY = 'theme'
const VALID_THEMES = ['light', 'dark']
const DEFAULT_THEME = 'light'

const readStored = () => {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return VALID_THEMES.includes(v) ? v : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

const apply = (theme) => {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('data-theme', theme)
}

const _theme = ref(readStored())
apply(_theme.value)

watch(_theme, (next) => {
  apply(next)
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // ignore quota / privacy mode
  }
})

export function useTheme() {
  const setTheme = (next) => {
    if (!VALID_THEMES.includes(next)) return
    _theme.value = next
  }

  const toggleTheme = () => {
    _theme.value = _theme.value === 'dark' ? 'light' : 'dark'
  }

  return {
    theme: readonly(_theme),
    setTheme,
    toggleTheme,
  }
}
