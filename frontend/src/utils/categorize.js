// 上层语义分类（前端自动归类）— 把后端散乱的 type 字符串归并为 6 大类
// 用于 GraphPanel（节点着色 + 图例）和 GraphPage（实体编辑面板的 Type 显示）
//
// 重要：节点的 type 信息可能存放在 `node.type`（具体类型，如 'PERSON'）或
// `node.node_type`（节点种类，如 'Entity' / 'Chunk'）。`getNodeRawType` 会按
// 优先级合并两个字段，避免只读 `type` 时退化成 'ENTITY' 字符串。

export const CATEGORIES = [
  {
    key: 'org', label: '组织',
    keywords: [
      'org', 'organization', 'company', 'corp', 'team', 'group', 'institution',
      'business', 'enterprise', 'department', 'bureau', 'agency', 'association',
      'union', 'committee', 'council', 'foundation', 'society', 'board',
      '公司', '机构', '部门', '组织', '委员会', '协会', '基金会'
    ]
  },
  {
    key: 'person', label: '人物',
    keywords: [
      'person', 'people', 'user', 'author', 'human', 'individual', 'figure',
      'executive', 'manager', 'ceo', 'cto', 'cfo', 'founder', 'worker',
      'employee', 'member', 'leader', 'chairman', 'president', 'director',
      '人物', '用户', '作者', '创始人', '经理', '总裁', '董事'
    ]
  },
  {
    key: 'place', label: '地点',
    keywords: [
      'place', 'location', 'city', 'country', 'region', 'address', 'venue',
      'site', 'area', 'zone', 'province', 'state', 'building', 'headquarters',
      'office', 'store', 'factory', 'street', 'district',
      '城市', '国家', '地区', '地点', '地址', '总部', '办公室'
    ]
  },
  {
    key: 'concept', label: '概念',
    keywords: [
      'concept', 'topic', 'idea', 'theory', 'field', 'domain', 'subject',
      'category', 'method', 'technology', 'tech', 'tool', 'framework',
      'language', 'system', 'platform', 'api', 'service', 'product', 'app',
      'library', 'model', 'algorithm', 'protocol', 'standard', 'project',
      'document', 'doc', 'chunk', 'text', 'data', 'dataset', 'file', 'asset',
      '概念', '主题', '技术', '工具', '框架', '理论', '系统', '平台', '项目',
      '文档', '文本', '数据'
    ]
  },
  {
    key: 'event', label: '事件',
    keywords: [
      'event', 'date', 'time', 'occurrence', 'incident', 'action', 'activity',
      'meeting', 'conference', 'launch', 'release', 'announcement',
      '事件', '会议', '活动', '日期', '发布会', '公告'
    ]
  },
]
export const CATEGORY_OTHER = { key: 'other', label: '其他', keywords: [] }

export const CATEGORY_COLOR_TOKEN = {
  org:     '--graph-doc',
  person:  '--graph-person',
  place:   '--graph-place',
  concept: '--graph-concept-strong',
  event:   '--graph-default',
  other:   '--graph-other',
}

// 从节点（任意对象）抽出用于归类的原始 type 字符串
// 优先级：entity_type（后端 LLM 抽取的真实实体类型）→ type/node_type（节点种类）→ 'ENTITY'
// 之前只读 type/node_type 时，所有搜索结果都是 "Entity"（节点种类）→ 全部分到 "other"，图例失效
export const getNodeRawType = (nodeOrString) => {
  if (typeof nodeOrString === 'string') return nodeOrString
  if (!nodeOrString || typeof nodeOrString !== 'object') return 'ENTITY'
  return nodeOrString.entity_type
      || nodeOrString.type
      || nodeOrString.node_type
      || 'ENTITY'
}

// 接受字符串或节点对象 — 内部统一调 getNodeRawType
export const categorize = (input) => {
  const rawType = getNodeRawType(input)
  const lower = String(rawType).toLowerCase()
  for (const cat of CATEGORIES) {
    if (cat.keywords.some(k => lower.includes(k.toLowerCase()))) return cat
  }
  return CATEGORY_OTHER
}

// 兼容旧 API：仍然接受字符串
export const categoryLabel = (input) => categorize(input).label
export const categoryColorToken = (input) =>
  CATEGORY_COLOR_TOKEN[categorize(input).key] || '--graph-default'

// 图例用：返回分类下原始 type 分布中数量最多的前 2 个
export const topRawTypes = (cat) => {
  if (!cat?.rawTypes) return []
  return Object.entries(cat.rawTypes)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([t]) => t)
}
