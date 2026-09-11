# FEAT-025: 实体别名与查重

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/graph/duplicates`（EntityDuplicatesPage.vue；EntityDetailPage.vue 增别名声）
- API: `/api/graph` 前缀下 `GET /entities/duplicates`、`GET /entities/{name}/aliases`、`POST /aliases/delete`（另 merge 自动记别名）
- 数据表: `entity_aliases`（第 14 张）

## 功能说明

实体合并（FEAT-007 的合并面板）之后旧名会被遗忘——`create_entities_batch`
以精确 name 为身份 MERGE，新文档再提到旧名就重新分裂出新节点。本功能闭环：

1. **合并自动记别名**：`POST /api/graph/entities/merge` 成功后把
   `source → target` 写入 `entity_aliases`（best-effort，失败不影响合并）。
2. **摄取解析**：`_run_ingest_pipeline` 在 `canonicalize_extraction_results`
   之后用 `apply_aliases_to_extraction` 把命中别名的抽取实体改写为 canonical
   ——旧名不再复活。canonicalize 管文档内 case 归一（first-seen），alias 管
   跨文档映射，两层正交。
3. **检索解析**：retriever 图通道把查询实体过 `resolve_names`——合并后旧名
   节点已不存在，不解析则精确名查找静默 miss。MCP server 与 eval runner 都
   接 `retrieve`，自动受益。
4. **查重建议**：`GET /entities/duplicates` 按 case（仅大小写不同）/ punct
   （仅空格标点不同）两规则分组（显式 `limit=2000` 扫描——client 默认 200；
   排除已是别名的名字防重复合并），页面选主实体后逐个调既有 merge API，
   不新增批量契约。

## 关联

- 依赖功能: FEAT-007/008（图谱浏览/实体详情）、FEAT-017 所在摄取管线
- 实现要点（改代码前必读）：
  - `entity_alias.py::record_alias`：写时链式解析使别名环不可达；**大小写
    变体可录**（节点身份是精确名，"openai→OpenAI" 是有效映射）；已存在
    别名重绑定到不同目标会被拒绝（先解绑）。
  - `apply_aliases_to_extraction` 是**纯重建式**——`canonicalize_extraction_results`
    返回的 dataclass 在 entities/chunk_entities 间共享引用，禁止原地改写；
    重映射后 source==target 自环丢弃。
  - `POST /aliases/delete` 用 JSON body 而非 DELETE query param：实体名来自
    无约束 LLM 抽取，query string 里 `+` 会解码成空格。
  - `GET /entities/duplicates` 与 `PATCH/DELETE /entities/{name:path}` 共存：
    Starlette 的 PARTIAL 匹配不短路扫描（`/entities/merge` 是活证）；静态段
    "duplicates" 被占用（该名实体仍可走 `/detail` 后缀路由）。
  - 解绑别名不拆已合并的图，只停止后续解析。
  - 测试: `tests/test_entity_alias.py`（DDL/链式防环/纯函数重映射/分组规则/
    merge 记别名/删清别名/detail 别名/端点）。摄取链路的集成不单测（全 stub
    过重），由 B6 纯函数覆盖 + 冒烟清单兜底。

## 手工冒烟清单

1. GraphPage 合并面板（C0 修复后）选两个实体合并成功。
2. `/graph/duplicates` 出建议组 → 选主 → "合并其余到所选" → 组消失。
3. 上传一篇含旧名的新文档 → 图谱中不再出现旧名节点。
4. EntityDetailPage 显示别名声 → ×解绑 → 刷新后消失。
