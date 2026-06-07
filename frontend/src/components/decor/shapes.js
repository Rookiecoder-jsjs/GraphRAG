/* Hand-drawn decorative shapes for empty-state hero.
   Each export is an array of element descriptors rendered by Decor.vue.
   Coordinates assume a 200×200 viewBox; all positions hand-placed for an
   intentionally imperfect, "pencil" feel — not pixel-perfect. */

const contourLines = [
  // Outermost ridge — long dashes
  { tag: 'path', d: 'M 100,15 C 140,18 180,40 185,75 C 188,110 175,150 140,170 C 105,185 65,182 35,165 C 12,150 8,110 15,80 C 22,45 60,12 100,15 Z', dash: '6 4' },
  // Second ridge — solid
  { tag: 'path', d: 'M 100,40 C 130,42 160,58 162,90 C 165,120 150,148 120,160 C 95,168 65,165 50,148 C 30,130 28,100 38,75 C 48,55 75,38 100,40 Z', dash: null },
  // Third — short dashes
  { tag: 'path', d: 'M 100,65 C 120,67 145,80 145,105 C 145,128 130,148 105,150 C 85,153 65,148 58,128 C 50,108 55,85 70,72 C 80,67 90,65 100,65 Z', dash: '3 2' },
  // Fourth — solid, slightly off-center for "drawn freehand"
  { tag: 'path', d: 'M 102,86 C 116,88 130,100 127,114 C 124,127 109,134 95,131 C 82,128 76,116 80,103 C 84,92 92,86 102,86 Z', dash: null },
  // Innermost — small irregular cap
  { tag: 'path', d: 'M 103,108 C 112,110 116,118 111,123 C 106,128 96,126 93,120 C 91,114 96,108 103,108 Z', dash: '2 2' },
  // Survey X marks
  { tag: 'g', children: [
    { tag: 'line', x1: 60, y1: 58, x2: 64, y2: 62 },
    { tag: 'line', x1: 64, y1: 58, x2: 60, y2: 62 }
  ]},
  { tag: 'g', children: [
    { tag: 'line', x1: 148, y1: 142, x2: 152, y2: 146 },
    { tag: 'line', x1: 152, y1: 142, x2: 148, y2: 146 }
  ]},
  // Peak dot — small filled circle
  { tag: 'circle', cx: 102, cy: 116, r: 2, fill: true }
]

const geometricPattern = [
  // 4 concentric circles — outer dashed, alternating solid/dashed
  { tag: 'circle', cx: 100, cy: 100, r: 95, dash: '4 3' },
  { tag: 'circle', cx: 100, cy: 100, r: 75, dash: null },
  { tag: 'circle', cx: 100, cy: 100, r: 50, dash: '2 2' },
  { tag: 'circle', cx: 100, cy: 100, r: 28, dash: null },
  // Cross-hair through center — dashed to feel "blueprint"
  { tag: 'line', x1: 12, y1: 100, x2: 188, y2: 100, dash: '3 4' },
  { tag: 'line', x1: 100, y1: 12, x2: 100, y2: 188, dash: '3 4' },
  // Center mark
  { tag: 'circle', cx: 100, cy: 100, r: 2, fill: true },
  // 4 orbit points on r=80 — 2 primary, 2 accent (amber)
  { tag: 'circle', cx: 165, cy: 130, r: 3, fill: true, tone: 'primary' },
  { tag: 'circle', cx: 50,  cy: 70,  r: 3, fill: true, tone: 'accent'  },
  { tag: 'circle', cx: 170, cy: 80,  r: 2.5, fill: true, tone: 'primary' },
  { tag: 'circle', cx: 40,  cy: 130, r: 3, fill: true, tone: 'accent'  },
  // A small surveyor mark — tiny cross top-right
  { tag: 'g', children: [
    { tag: 'line', x1: 96,  y1: 18,  x2: 104, y2: 26 },
    { tag: 'line', x1: 104, y1: 18,  x2: 96,  y2: 26 }
  ]}
]

const paperGrain = [
  // 42 hand-placed dots — fill: true = solid dot, fill: false = outline.
  // Coordinates chosen to feel "stippled" — 3-4 loose clusters, sparse middles.
  { x: 25,  y: 30,  r: 1,   fill: true  },
  { x: 38,  y: 22,  r: 0.6, fill: false },
  { x: 18,  y: 45,  r: 0.8, fill: true  },
  { x: 50,  y: 35,  r: 0.5, fill: false },
  { x: 32,  y: 50,  r: 1.2, fill: true  },
  { x: 165, y: 28,  r: 0.7, fill: true  },
  { x: 180, y: 42,  r: 1,   fill: false },
  { x: 152, y: 38,  r: 0.5, fill: true  },
  { x: 175, y: 60,  r: 0.8, fill: false },
  { x: 80,  y: 15,  r: 0.5, fill: true  },
  { x: 110, y: 20,  r: 0.8, fill: false },
  { x: 130, y: 12,  r: 0.6, fill: true  },
  { x: 95,  y: 95,  r: 0.6, fill: true  },
  { x: 105, y: 100, r: 0.5, fill: false },
  { x: 100, y: 110, r: 0.7, fill: true  },
  { x: 188, y: 100, r: 0.8, fill: true  },
  { x: 185, y: 120, r: 0.5, fill: false },
  { x: 155, y: 160, r: 1,   fill: true  },
  { x: 170, y: 175, r: 0.6, fill: false },
  { x: 165, y: 150, r: 0.7, fill: true  },
  { x: 180, y: 165, r: 0.5, fill: true  },
  { x: 145, y: 175, r: 0.8, fill: false },
  { x: 30,  y: 155, r: 0.7, fill: true  },
  { x: 50,  y: 168, r: 0.5, fill: false },
  { x: 20,  y: 170, r: 0.9, fill: true  },
  { x: 40,  y: 182, r: 0.6, fill: true  },
  { x: 85,  y: 188, r: 0.5, fill: false },
  { x: 115, y: 185, r: 0.7, fill: true  },
  { x: 130, y: 190, r: 0.6, fill: false },
  { x: 12,  y: 95,  r: 0.5, fill: true  },
  { x: 15,  y: 110, r: 0.7, fill: false },
  { x: 20,  y: 125, r: 0.6, fill: true  },
  { x: 188, y: 90,  r: 0.5, fill: true  },
  { x: 60,  y: 80,  r: 0.6, fill: true  },
  { x: 70,  y: 100, r: 0.4, fill: false },
  { x: 50,  y: 110, r: 0.8, fill: true  },
  { x: 140, y: 90,  r: 0.5, fill: false },
  { x: 130, y: 105, r: 0.7, fill: true  },
  { x: 150, y: 110, r: 0.4, fill: false },
  { x: 75,  y: 140, r: 0.6, fill: true  },
  { x: 90,  y: 150, r: 0.5, fill: false },
  { x: 65,  y: 160, r: 0.7, fill: true  }
]

export const shapes = {
  contour: contourLines,
  geometric: geometricPattern,
  paper: paperGrain
}
