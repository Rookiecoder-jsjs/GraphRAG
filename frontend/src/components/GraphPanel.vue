<template>
  <div class="graph-panel">
    <div class="panel-header">
      <span class="panel-title">Knowledge Graph</span>
      <div class="header-tools">
        <Button
          variant="secondary"
          size="sm"
          mono
          :loading="loading"
          @click="$emit('refresh')"
          title="Refresh"
          class="tool-btn-mono"
        >
          <RefreshIcon v-if="!loading" class="tool-icon" />
          <span>Refresh</span>
        </Button>
        <div class="export-group">
          <Button
            variant="secondary"
            size="sm"
            mono
            :disabled="!graphData"
            @click="exportSVG"
            title="Download as SVG"
            class="tool-btn-mono"
          >
            <DownloadIcon class="tool-icon" />
            <span>SVG</span>
          </Button>
          <Button
            variant="secondary"
            size="sm"
            mono
            :disabled="!graphData"
            @click="exportPNG"
            title="Download as PNG"
            class="tool-btn-mono"
          >
            <ImageIcon class="tool-icon" />
            <span>PNG</span>
          </Button>
        </div>
      </div>
    </div>

    <div class="graph-container" ref="graphContainer">
      <!-- Graph Visualization -->
      <div v-if="graphData" class="graph-view">
        <svg ref="graphSvg" class="graph-svg"></svg>

        <!-- Loading state -->
        <div v-if="loading" class="graph-state">
          <div class="loading-spinner"></div>
          <p>Loading graph...</p>
        </div>
      </div>

      <!-- Empty state -->
      <div v-else-if="!loading" class="graph-state">
        <div class="empty-icon">&#x2756;</div>
        <p class="empty-text">No graph data available</p>
      </div>
    </div>

    <!-- Legend -->
    <div v-if="graphData && entityTypes.length" class="graph-legend">
      <span class="legend-title">分类</span>
      <div class="legend-items">
        <div
          class="legend-item"
          v-for="type in entityTypes"
          :key="type.key"
          :title="categoryTooltip(type)"
        >
          <span class="legend-dot" :style="{ background: type.color }"></span>
          <div class="legend-text">
            <span class="legend-row">
              <span class="legend-label">{{ type.name }}</span>
              <span class="legend-count">{{ type.count }}</span>
            </span>
            <span class="legend-raws" v-if="topRawTypes(type).length">
              {{ topRawTypes(type).join(' · ') }}
            </span>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick, computed, h } from 'vue'
import {
  select, forceSimulation, forceLink, forceManyBody,
  forceCenter, forceCollide, forceX, forceY, zoom, drag
} from 'd3'
import { useTheme } from '../composables/useTheme'
import { CATEGORIES, CATEGORY_OTHER, CATEGORY_COLOR_TOKEN, categorize, topRawTypes } from '../utils/categorize'
import { Button, Tag } from './ui'

const RefreshIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('polyline', { points: '23 4 23 10 17 10' }),
    h('polyline', { points: '1 20 1 14 7 14' }),
    h('path', { d: 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15' })
  ])
}
const DownloadIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }),
    h('polyline', { points: '7 10 12 15 17 10' }),
    h('line', { x1: 12, y1: 15, x2: 12, y2: 3 })
  ])
}
const ImageIcon = {
  render: () => h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('rect', { x: 3, y: 3, width: 18, height: 18, rx: 2, ry: 2 }),
    h('circle', { cx: 8.5, cy: 8.5, r: 1.5 }),
    h('polyline', { points: '21 15 16 10 5 21' })
  ])
}

const props = defineProps({
  graphData: Object,
  loading: Boolean
})

const emit = defineEmits(['refresh', 'node-click', 'edge-click'])

const { theme } = useTheme()

const graphContainer = ref(null)
const graphSvg = ref(null)

// 读取 CSS 变量值（响应主题切换）
const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()

// Compute entity types from graph data — 按上层分类聚合，保留每个分类内的原始 type 分布
const entityTypes = computed(() => {
  if (!props.graphData?.nodes) return []
  // 触发主题响应（theme 是 readonly ref，访问后建立依赖）
  // eslint-disable-next-line no-unused-expressions
  theme.value

  const catMap = {}
  props.graphData.nodes.forEach(node => {
    // 用整个 node 传给 categorize — 内部 getNodeRawType 优先读 entity_type
    const cat = categorize(node)
    // 同步用 entity_type 优先 — 图例 hover tooltip 应显示真实实体类型 (PERSON/ORG/...)
    // 而不是节点种类 ("Entity")
    const rawType = node.entity_type || node.type || node.node_type || 'ENTITY'
    if (!catMap[cat.key]) {
      catMap[cat.key] = {
        key: cat.key,
        name: cat.label,
        count: 0,
        color: cssVar(CATEGORY_COLOR_TOKEN[cat.key] || '--graph-default'),
        rawTypes: {}
      }
    }
    catMap[cat.key].count++
    catMap[cat.key].rawTypes[rawType] = (catMap[cat.key].rawTypes[rawType] || 0) + 1
  })

  // 按 CATEGORIES 顺序输出（保持稳定顺序）
  return CATEGORIES.concat([CATEGORY_OTHER])
    .map(c => catMap[c.key])
    .filter(Boolean)
})

const categoryTooltip = (cat) => {
  const parts = Object.entries(cat.rawTypes)
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `${t} × ${n}`)
    .join(' · ')
  return `${cat.name} · 共 ${cat.count} 个 · 含：${parts}`
}

let currentSimulation = null

const renderGraph = () => {
  if (!graphSvg.value || !props.graphData) return

  // Stop previous simulation
  if (currentSimulation) {
    currentSimulation.stop()
  }

  // 读取当前主题色板
  const C = {
    center:   cssVar('--graph-center'),
    related:  cssVar('--graph-related'),
    edge:     cssVar('--graph-edge'),
    textPrimary: cssVar('--text-primary'),
    textTertiary: cssVar('--text-tertiary'),
    bgPrimary: cssVar('--bg-primary'),
    primary:  cssVar('--primary'),
    accent:   cssVar('--accent'),
  }

  const container = graphContainer.value
  const width = container.clientWidth
  const height = container.clientHeight

  const svg = select(graphSvg.value)
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', `0 0 ${width} ${height}`)

  svg.selectAll('*').remove()

  const nodesData = props.graphData.nodes || []
  const edgesData = props.graphData.edges || []

  if (nodesData.length === 0) return

  // Create node map for quick lookup
  const nodeMap = {}
  nodesData.forEach(n => {
    nodeMap[n.id] = n
  })

  // Process nodes
  const nodes = nodesData.map(n => ({
    id: n.id,
    name: n.label || n.name || 'Unnamed',
    type: n.type || 'ENTITY',
    rawData: n
  }))

  const nodeIds = new Set(nodes.map(n => n.id))

  // Process edges
  const edgePairCount = {}
  const processedSelfLoopNodes = new Set()

  const tempEdges = edgesData.filter(e =>
    nodeIds.has(e.source) && nodeIds.has(e.target)
  )

  // Count edges between node pairs
  tempEdges.forEach(e => {
    if (e.source === e.target) {
      // Self loop
      if (!processedSelfLoopNodes.has(e.source)) {
        processedSelfLoopNodes.add(e.source)
      }
    } else {
      const pairKey = [e.source, e.target].sort().join('_')
      edgePairCount[pairKey] = (edgePairCount[pairKey] || 0) + 1
    }
  })

  const edgePairIndex = {}

  const edges = tempEdges.map(e => {
    const isSelfLoop = e.source === e.target
    let curvature = 0

    if (!isSelfLoop) {
      const pairKey = [e.source, e.target].sort().join('_')
      const totalCount = edgePairCount[pairKey] || 1
      const currentIndex = edgePairIndex[pairKey] || 0
      edgePairIndex[pairKey] = currentIndex + 1

      if (totalCount > 1) {
        const curvatureRange = Math.min(1.2, 0.6 + totalCount * 0.15)
        curvature = ((currentIndex / (totalCount - 1)) - 0.5) * curvatureRange * 2

        const isReversed = e.source > e.target
        if (isReversed) {
          curvature = -curvature
        }
      }
    }

    return {
      source: e.source,
      target: e.target,
      type: e.type || 'RELATED',
      name: e.label || e.type || 'RELATED',
      curvature,
      isSelfLoop,
      rawData: {
        ...e,
        source_label: nodeMap[e.source]?.label,
        target_label: nodeMap[e.target]?.label
      }
    }
  })

  // Color scale — 按上层分类着色（同一分类内所有原始 type 共享一个颜色）
  const colorMap = {}
  entityTypes.value.forEach(t => { colorMap[t.key] = t.color })
  const getColor = (rawType) => {
    const cat = categorize(rawType)
    return colorMap[cat.key] || cssVar('--graph-default')
  }

  // Force simulation
  const simulation = forceSimulation(nodes)
    .force('link', forceLink(edges)
      .id(d => d.id)
      .distance(d => {
        const baseDistance = 150
        const edgeCount = d.pairTotal || 1
        return baseDistance + (edgeCount - 1) * 50
      }))
    .force('charge', forceManyBody().strength(-400))
    .force('center', forceCenter(width / 2, height / 2))
    .force('collide', forceCollide(50))
    .force('x', forceX(width / 2).strength(0.04))
    .force('y', forceY(height / 2).strength(0.04))

  currentSimulation = simulation

  const g = svg.append('g')

  // Zoom behavior
  svg.call(zoom()
    .extent([[0, 0], [width, height]])
    .scaleExtent([0.1, 4])
    .on('zoom', (event) => {
      g.attr('transform', event.transform)
    }))

  // Links group
  const linkGroup = g.append('g').attr('class', 'links')

  // Generate curved path
  const getLinkPath = (d) => {
    const sx = d.source.x
    const sy = d.source.y
    const tx = d.target.x
    const ty = d.target.y

    if (d.isSelfLoop) {
      const loopRadius = 30
      const x1 = sx + 8
      const y1 = sy - 4
      const x2 = sx + 8
      const y2 = sy + 4
      return `M${x1},${y1} A${loopRadius},${loopRadius} 0 1,1 ${x2},${y2}`
    }

    if (d.curvature === 0) {
      return `M${sx},${sy} L${tx},${ty}`
    }

    const dx = tx - sx
    const dy = ty - sy
    const dist = Math.sqrt(dx * dx + dy * dy)
    const pairTotal = d.pairTotal || 1
    const offsetRatio = 0.25 + pairTotal * 0.05
    const baseOffset = Math.max(35, dist * offsetRatio)
    const offsetX = -dy / dist * d.curvature * baseOffset
    const offsetY = dx / dist * d.curvature * baseOffset
    const cx = (sx + tx) / 2 + offsetX
    const cy = (sy + ty) / 2 + offsetY

    return `M${sx},${sy} Q${cx},${cy} ${tx},${ty}`
  }

  // Links
  // Get highlighted node ids for edge opacity
  // d3.forceLink().id() 会把 d.source/d.target 替换成节点对象 — 用 id 比对而不是 name
  const highlightedIds = new Set()
  nodes.forEach(n => {
    const raw = n.rawData || n
    if (raw.is_center || raw.is_highlighted) {
      highlightedIds.add(raw.id)
    }
  })
  // Check if we're in search mode (any highlighted nodes)
  const isSearchMode = highlightedIds.size > 0

  const link = linkGroup.selectAll('path')
    .data(edges)
    .enter()
    .append('path')
    .attr('stroke', C.edge)
    .attr('stroke-width', 1.25)
    .attr('fill', 'none')
    .style('cursor', 'pointer')
    .style('opacity', d => {
      // Dim edges not connected to highlighted nodes when searching
      if (!isSearchMode) return 0.85
      const sourceId = typeof d.source === 'object' ? d.source.id : d.source
      const targetId = typeof d.target === 'object' ? d.target.id : d.target
      if (highlightedIds.has(sourceId) || highlightedIds.has(targetId)) return 1
      return 0.18
    })
    .on('click', (event, d) => {
      event.stopPropagation()
      linkGroup.selectAll('path').attr('stroke', C.edge).attr('stroke-width', 1.25)
      select(event.target).attr('stroke', C.primary).attr('stroke-width', 2.5)

      emit('edge-click', d.rawData)
    })

  // Edge labels group
  const edgeLabelGroup = g.append('g').attr('class', 'edge-labels')

  // Calculate label position for edge
  const getLabelPosition = (d) => {
    if (d.isSelfLoop) {
      return { x: d.source.x + 40, y: d.source.y - 15 }
    }

    const sx = d.source.x
    const sy = d.source.y
    const tx = d.target.x
    const ty = d.target.y

    if (d.curvature === 0) {
      return { x: (sx + tx) / 2, y: (sy + ty) / 2 }
    }

    const dx = tx - sx
    const dy = ty - sy
    const dist = Math.sqrt(dx * dx + dy * dy)
    const pairTotal = d.pairTotal || 1
    const offsetRatio = 0.25 + pairTotal * 0.05
    const baseOffset = Math.max(35, dist * offsetRatio)
    const offsetX = -dy / dist * d.curvature * baseOffset
    const offsetY = dx / dist * d.curvature * baseOffset
    const cx = (sx + tx) / 2 + offsetX
    const cy = (sy + ty) / 2 + offsetY

    return { x: cx, y: cy }
  }

  // Edge labels
  const edgeLabel = edgeLabelGroup.selectAll('text')
    .data(edges)
    .enter()
    .append('text')
    .text(d => d.type || d.label || '')
    .attr('font-size', '9px')
    .attr('fill', C.textTertiary)
    .attr('font-weight', '500')
    .attr('text-anchor', 'middle')
    .attr('dy', -3)
    .style('pointer-events', 'none')
    .style('font-family', 'var(--font-mono), monospace')
    .style('opacity', d => {
      if (!isSearchMode) return 0.7
      const sourceId = typeof d.source === 'object' ? d.source.id : d.source
      const targetId = typeof d.target === 'object' ? d.target.id : d.target
      if (highlightedIds.has(sourceId) || highlightedIds.has(targetId)) return 0.85
      return 0.15
    })

  // Nodes group
  const nodeGroup = g.append('g').attr('class', 'nodes')

  // Node color based on highlight state
  const getNodeColor = (d) => {
    // Check rawData for highlight properties (set by search)
    const raw = d.rawData || d
    if (raw.is_center) {
      return C.center  // 中心高亮
    }
    if (raw.is_highlighted) {
      return C.related // 相关高亮
    }
    // 传 rawData 让 categorize 走 type || node_type 兜底，避免被错误归类
    return getColor(raw)
  }

  // Node circles
  const node = nodeGroup.selectAll('circle')
    .data(nodes)
    .enter()
    .append('circle')
    .attr('r', 9)
    .attr('fill', d => getNodeColor(d))
    .attr('stroke', d => {
      const raw = d.rawData || d
      if (raw.is_center) return C.center
      if (raw.is_highlighted) return C.related
      return C.bgPrimary
    })
    .attr('stroke-width', d => {
      const raw = d.rawData || d
      if (raw.is_center || raw.is_highlighted) return 3
      return 2
    })
    .style('opacity', d => {
      const raw = d.rawData || d
      // Dim non-highlighted nodes when searching
      if (!isSearchMode) return 1
      if (raw.is_center || raw.is_highlighted) return 1
      return 0.3
    })
    .style('cursor', 'pointer')
    .call(drag()
      .on('start', (event, d) => {
        d.fx = d.x
        d.fy = d.y
        d._dragStartX = event.x
        d._dragStartY = event.y
        d._isDragging = false
      })
      .on('drag', (event, d) => {
        const dx = event.x - d._dragStartX
        const dy = event.y - d._dragStartY
        const distance = Math.sqrt(dx * dx + dy * dy)

        if (!d._isDragging && distance > 3) {
          d._isDragging = true
          simulation.alphaTarget(0.3).restart()
        }

        if (d._isDragging) {
          d.fx = event.x
          d.fy = event.y
        }
      })
      .on('end', (event, d) => {
        if (d._isDragging) {
          simulation.alphaTarget(0)
        }
        d.fx = null
        d.fy = null
        d._isDragging = false
      }))
    .on('click', (event, d) => {
      event.stopPropagation()

      // Reset all nodes to their highlight colors, not white
      node.each(function(n) {
        const raw = n.rawData || n
        const el = select(this)
        el.attr('stroke', () => {
          if (raw.is_center) return C.center
          if (raw.is_highlighted) return C.related
          return C.bgPrimary
        })
        el.attr('stroke-width', () => {
          if (raw.is_center || raw.is_highlighted) return 3
          return 2
        })
      })

      linkGroup.selectAll('path').attr('stroke', C.edge).attr('stroke-width', 1.25)
      select(event.target).attr('stroke', C.accent).attr('stroke-width', 3.5)

      link.filter(l => l.source.id === d.id || l.target.id === d.id)
        .attr('stroke', C.accent)
        .attr('stroke-width', 2)

      // rawData 里只有 label 没有 name（API 响应形状），补一个 name 字段
      // 让 GraphPage 的 selectedEntity.name 始终能拿到值
      emit('node-click', { ...d.rawData, name: d.name })
    })
    .on('mouseenter', (event, d) => {
      select(event.target).attr('stroke', C.textPrimary).attr('stroke-width', 3)
    })
    .on('mouseleave', (event, d) => {
      const raw = d.rawData || d
      if (raw.is_center) {
        select(event.target).attr('stroke', C.center).attr('stroke-width', 3)
      } else if (raw.is_highlighted) {
        select(event.target).attr('stroke', C.related).attr('stroke-width', 3)
      } else {
        select(event.target).attr('stroke', C.bgPrimary).attr('stroke-width', 2)
      }
    })

  // Node labels
  const nodeLabels = nodeGroup.selectAll('text')
    .data(nodes)
    .enter()
    .append('text')
    .text(d => d.name.length > 10 ? d.name.substring(0, 10) + '…' : d.name)
    .attr('font-size', '11px')
    .attr('fill', d => {
      const raw = d.rawData || d
      if (!isSearchMode) return C.textPrimary
      if (highlightedIds.has(raw.id)) return C.textPrimary
      return C.textTertiary
    })
    .attr('font-weight', '500')
    .attr('dx', 13)
    .attr('dy', 4)
    .style('pointer-events', 'none')
    .style('font-family', 'var(--font-display), Georgia, serif')
    .style('opacity', d => {
      const raw = d.rawData || d
      if (!isSearchMode) return 0.95
      if (highlightedIds.has(raw.id)) return 1
      return 0.35
    })

  // Simulation tick
  simulation.on('tick', () => {
    link.attr('d', getLinkPath)

    node
      .attr('cx', d => d.x)
      .attr('cy', d => d.y)

    nodeLabels
      .attr('x', d => d.x)
      .attr('y', d => d.y)

    edgeLabel
      .attr('x', d => getLabelPosition(d).x)
      .attr('y', d => getLabelPosition(d).y)
  })

  // Click on empty space to deselect
  svg.on('click', () => {
    node.attr('stroke', d => {
      const raw = d.rawData || d
      if (raw.is_center) return C.center
      if (raw.is_highlighted) return C.related
      return C.bgPrimary
    }).attr('stroke-width', d => {
      const raw = d.rawData || d
      if (raw.is_center || raw.is_highlighted) return 3
      return 2
    })
    linkGroup.selectAll('path').attr('stroke', C.edge).attr('stroke-width', 1.25)
  })
}

const handleResize = () => {
  nextTick(renderGraph)
}

// ---------- Export ----------
// Strategy: clone the live <svg>, inline computed styles + theme variables
// (otherwise the exported file renders with the browser's default colors),
// then write it out. PNG goes through a canvas to rasterize.
const buildStandaloneSVG = () => {
  const original = graphSvg.value
  if (!original) return null

  const clone = original.cloneNode(true)
  // Adopt the live bbox so the exported file isn't cropped to the viewBox
  // after the user has panned/zoomed.
  const bbox = original.getBoundingClientRect()
  const width = Math.round(bbox.width)
  const height = Math.round(bbox.height)
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')

  // Inline the theme background so PNG/SVG both render correctly outside
  // the app (where --graph-bg / --text-* aren't defined).
  const bg = cssVar('--graph-bg') || '#ffffff'
  const textColor = cssVar('--text-primary') || '#141413'

  const styleEl = document.createElementNS('http://www.w3.org/2000/svg', 'style')
  styleEl.textContent = `
    .export-root { background: ${bg}; }
    text { font-family: var(--font-display, Georgia, serif); fill: ${textColor}; }
  `
  clone.insertBefore(styleEl, clone.firstChild)

  // Wrap in a background rect so PNG (which has no page background) is usable.
  const bgRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
  bgRect.setAttribute('width', '100%')
  bgRect.setAttribute('height', '100%')
  bgRect.setAttribute('fill', bg)
  clone.insertBefore(bgRect, clone.firstChild)

  return { svg: clone, width, height }
}

const triggerDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // Defer revoke so the browser has time to start the download
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const exportSVG = () => {
  const built = buildStandaloneSVG()
  if (!built) return
  const serializer = new XMLSerializer()
  const source = serializer.serializeToString(built.svg)
  // Prepend the XML declaration so the file is a valid standalone SVG
  const blob = new Blob(
    ['<?xml version="1.0" encoding="UTF-8"?>\n', source],
    { type: 'image/svg+xml;charset=utf-8' }
  )
  triggerDownload(blob, `knowledge-graph-${Date.now()}.svg`)
}

const exportPNG = async () => {
  const built = buildStandaloneSVG()
  if (!built) return
  const serializer = new XMLSerializer()
  const source = serializer.serializeToString(built.svg)
  // 2x scale for retina-friendly output; clamp to keep memory reasonable.
  const scale = Math.min(2, Math.max(1, window.devicePixelRatio || 1))
  const blob = new Blob([source], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(blob)

  try {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    await new Promise((resolve, reject) => {
      img.onload = resolve
      img.onerror = () => reject(new Error('SVG image failed to load'))
      img.src = url
    })

    const canvas = document.createElement('canvas')
    canvas.width = built.width * scale
    canvas.height = built.height * scale
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = cssVar('--graph-bg') || '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)

    const pngBlob = await new Promise((resolve, reject) => {
      canvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error('Canvas toBlob returned null'))),
        'image/png'
      )
    })
    triggerDownload(pngBlob, `knowledge-graph-${Date.now()}.png`)
  } finally {
    URL.revokeObjectURL(url)
  }
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
  if (props.graphData) {
    nextTick(renderGraph)
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (currentSimulation) {
    currentSimulation.stop()
  }
})

// Watch for graph data 或 主题切换 → 重渲染
watch([() => props.graphData, theme], () => {
  nextTick(renderGraph)
}, { deep: true })
</script>

<style scoped>
.graph-panel {
  position: relative;
  width: 100%;
  height: 100%;
  background-color: var(--graph-bg);
  background-image:
    linear-gradient(var(--graph-grid) 1px, transparent 1px),
    linear-gradient(90deg, var(--graph-grid) 1px, transparent 1px);
  background-size: 28px 28px;
  overflow: hidden;
}

.panel-header {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  padding: 1rem 1.25rem;
  z-index: 10;
  display: flex;
  justify-content: space-between;
  align-items: center;
  pointer-events: none;
}

.panel-title {
  font-family: var(--font-display);
  font-size: 0.9375rem;
  font-weight: 500;
  color: var(--text-primary);
  pointer-events: auto;
  background: var(--bg-primary);
  padding: 0.25rem 0.625rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.header-tools {
  pointer-events: auto;
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.export-group {
  display: flex;
  gap: 4px;
  padding-left: 4px;
  margin-left: 4px;
  border-left: 1px solid var(--border);
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.graph-container {
  width: 100%;
  height: 100%;
}

.graph-view,
.graph-svg {
  width: 100%;
  height: 100%;
  display: block;
}

.graph-state {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: var(--text-tertiary);
}

.empty-icon {
  font-size: 2.5rem;
  margin-bottom: 0.875rem;
  opacity: 0.25;
}

.empty-text {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 0.9375rem;
}

/* ---- Legend ---- */
.graph-legend {
  position: absolute;
  bottom: 1.25rem;
  left: 1.25rem;
  background: var(--bg-primary);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  z-index: 10;
}

.legend-title {
  display: block;
  font-family: var(--font-mono);
  font-size: 0.625rem;
  font-weight: 700;
  color: var(--text-tertiary);
  margin-bottom: 0.5rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.legend-items {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.875rem;
  max-width: 280px;
}

.legend-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.legend-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
}

.legend-text {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.legend-row {
  display: flex;
  align-items: baseline;
  gap: 0.375rem;
}

.legend-raws {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

/* Loading spinner */
.loading-spinner {
  width: 36px;
  height: 36px;
  border: 2px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
  margin: 0 auto 0.875rem;
}
</style>
