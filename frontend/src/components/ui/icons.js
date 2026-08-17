import { h } from 'vue'

// 统一 SVG 图标集 — Lucide 风格线性图标，stroke-width 1.75。
// 用法：<component :is="FileTextIcon" /> 或 <FileTextIcon />
const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': '1.75',
  'stroke-linecap': 'round',
  'stroke-linejoin': 'round'
}

function make(...children) {
  return {
    name: 'NexusIcon',
    render() {
      return h('svg', base, children.map(c => h(c.tag, c.attrs)))
    }
  }
}

const p = (tag, attrs) => ({ tag, attrs })

// 导航图标
export const LayoutGridIcon = make(
  p('rect', { x: '3', y: '3', width: '7', height: '7', rx: '1' }),
  p('rect', { x: '14', y: '3', width: '7', height: '7', rx: '1' }),
  p('rect', { x: '14', y: '14', width: '7', height: '7', rx: '1' }),
  p('rect', { x: '3', y: '14', width: '7', height: '7', rx: '1' })
)

export const FileTextIcon = make(
  p('path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }),
  p('polyline', { points: '14 2 14 8 20 8' }),
  p('line', { x1: '16', y1: '13', x2: '8', y2: '13' }),
  p('line', { x1: '16', y1: '17', x2: '8', y2: '17' })
)

export const SearchIcon = make(
  p('circle', { cx: '11', cy: '11', r: '8' }),
  p('line', { x1: '21', y1: '21', x2: '16.65', y2: '16.65' })
)

export const Share2Icon = make(
  p('circle', { cx: '18', cy: '5', r: '3' }),
  p('circle', { cx: '6', cy: '12', r: '3' }),
  p('circle', { cx: '18', cy: '19', r: '3' }),
  p('line', { x1: '8.59', y1: '13.51', x2: '15.42', y2: '17.49' }),
  p('line', { x1: '15.41', y1: '6.51', x2: '8.59', y2: '10.49' })
)

export const MessageSquareIcon = make(
  p('path', { d: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' })
)

export const BarChartIcon = make(
  p('line', { x1: '12', y1: '20', x2: '12', y2: '10' }),
  p('line', { x1: '18', y1: '20', x2: '18', y2: '4' }),
  p('line', { x1: '6', y1: '20', x2: '6', y2: '16' })
)

export const MapIcon = make(
  p('polygon', { points: '1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6' }),
  p('line', { x1: '8', y1: '2', x2: '8', y2: '18' }),
  p('line', { x1: '16', y1: '6', x2: '16', y2: '22' })
)

export const PlayIcon = make(
  p('polygon', { points: '5 3 19 12 5 21 5 3' })
)

// 系统图标
export const SunIcon = make(
  p('circle', { cx: '12', cy: '12', r: '4' }),
  p('line', { x1: '12', y1: '2', x2: '12', y2: '4' }),
  p('line', { x1: '12', y1: '20', x2: '12', y2: '22' }),
  p('line', { x1: '4.93', y1: '4.93', x2: '6.34', y2: '6.34' }),
  p('line', { x1: '17.66', y1: '17.66', x2: '19.07', y2: '19.07' }),
  p('line', { x1: '2', y1: '12', x2: '4', y2: '12' }),
  p('line', { x1: '20', y1: '12', x2: '22', y2: '12' }),
  p('line', { x1: '4.93', y1: '19.07', x2: '6.34', y2: '17.66' }),
  p('line', { x1: '17.66', y1: '6.34', x2: '19.07', y2: '4.93' })
)

export const MoonIcon = make(
  p('path', { d: 'M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z' })
)

export const LogOutIcon = make(
  p('path', { d: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4' }),
  p('polyline', { points: '16 17 21 12 16 7' }),
  p('line', { x1: '21', y1: '12', x2: '9', y2: '12' })
)

export const ChevronLeftIcon = make(
  p('polyline', { points: '15 18 9 12 15 6' })
)

export const ChevronRightIcon = make(
  p('polyline', { points: '9 18 15 12 9 6' })
)

export const InboxIcon = make(
  p('polyline', { points: '22 12 16 12 14 15 10 15 8 12 2 12' }),
  p('path', { d: 'M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z' })
)

// 品牌 Logo（知识图谱六边形）
export const LogoIcon = make(
  p('polygon', { points: '12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5 12 2' }),
  p('line', { x1: '12', y1: '22', x2: '12', y2: '15.5' }),
  p('polyline', { points: '22 8.5 12 15.5 2 8.5' })
)
