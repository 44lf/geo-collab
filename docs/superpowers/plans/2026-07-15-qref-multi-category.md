# 高质量库多对多问题类型关联 · 实现计划

> 对抗评审质量门（`docs/superpowers/plans/2026-07-14-adversarial-review-quality-gate.md` Task 1-9，已实现）的**增量**。

**Goal:** 把 `quality_reference` 的单值 `category` 列改成多对多子表 `quality_reference_category`，并存每类型的问题词（`question_texts`）。一篇高质量文章可关联多个问题类型（own + external 皆可）。

## 铁律（不可违背）
- `content_hash` UNIQUE / `article_id` UNIQUE **原样不动**。多关联**只能靠子表表达**，绝不给 `quality_reference` 加行（加行会撞 `content_hash` 被幂等复活静默吞掉）。
- 「通用兜底」语义：从 `category IS NULL` 改成「该 reference **无任何关联行**」。
- MCP 端点 / 工具（`pick_quality_references` / `record_adversarial_score`）签名、Task 9 verifier skill **全不动**（pick 仍按单个 category 命中子表）。

## 当前实况（已核实）
- head = `0062_adversarial_review`（down_revision 用它全 id）。
- 0062 里 qref 的 category 索引名 = `ix_quality_reference_category`（0063 先 drop 索引再 drop 列）。
- qref 模块**外无** `pick_references`/`category_origin_stats`/`QualityReference.category` 消费者（router 文档里"pipelines 调 pick"是过时描述，无实际调用）。
- 现有 category 读写点：service.py（adopt:85 / import:108 / list:120 / patch:134,138 / pick:155-178 / stats:208-224）、router.py（adopt:31 / import:45 / list:57,65 / categories:82 / patch:119）、mcp_router.py（pick:32,41）、models.py:49、schemas.py（10/18/25/33）。

## 设计决策（锁定）
- **子表** `quality_reference_category`：`id` PK / `reference_id` NOT NULL FK→quality_reference(id) **ON DELETE CASCADE** / `category` varchar(200) NOT NULL / `question_texts` JSON null / `created_at` / **UNIQUE(reference_id, category)** / index(category)。
- **service `set_reference_categories(db, ref_id, items)`** replace-all（先删该 ref 全部关联行、再插入 items；跳过空 category；去重靠 UNIQUE）。`items` = `list[{category:str, question_texts:list|None}]`。被 adopt/import/patch 复用。
- **adopt_article**：插 ref（幂等）后，仅当**新插入**（非 dup）时：关联类型 = `article.source_question_category`，无则回落入参 `fallback_category`（前端补选）；有则 `set_reference_categories([{category, question_texts=article.source_question_texts}]）`；都无则不关联（=通用）。dup 复活时不动关联。
- **import_external**：入参 `category`（单值，section 2 契约）+ 可选 `question_texts`；新插入且 category 非空 → 关联一条；空 → 不关联。（前端多类型录入 = import 后再 patch replace-all。）
- **pick_references(category,k)**：池 A = 有该 category 关联行的 active ref；不足回落池 B = **无任何关联行**的 active ref（通用）；两池仍 prefer external、own 补足；UNIQUE 不动。返回 dict 保持 `category` 键（池 A 填命中类目、池 B 填 None）供 verifier 兼容。
- **list_references** 的 category 过滤 / **category_origin_stats** / GET /stats：改成 **join 子表按子表.category 聚合**（stats 按 `child.category` group by，external/own 用 `QualityReference.origin`）。
- **patch_reference**：`categories`（`list[{category, question_texts?}]`）整体 replace-all（空数组=清空=通用）；单列 category 参数移除。
- **schemas**：`CategoryAssoc{category:str, question_texts:list|None}`；`QualityReferenceRead` 去 `category`、加 `categories:list[CategoryAssoc]`；`PatchRequest` 去 `category`、加 `categories:list[CategoryAssoc]|None`；`ImportRequest` 加 `question_texts:list|None`；`AdoptRequest` 保留单 `category`（补选）。
- **备选未采纳**：折进 0062（因 0062 已 review + 已在 geo_test upgrade 过，保持不可变），用新迁移 0063。

---

## Task 1（后端原子，一个绿 commit）：迁移 0063 + ORM 子表 + service + schemas + router + 测试

删列与 service 改写必须原子（删列后旧 service 引用 `QualityReference.category` 会炸），故合成一个任务/一个 commit。TDD：先写失败测试。

**迁移 `server/alembic/versions/0063_qref_multi_category.py`**（down_revision=`0062_adversarial_review`，全 id 格式）：
- upgrade：建 `quality_reference_category`（含 CASCADE FK + UNIQUE(reference_id,category) + index category）；回填 `INSERT ... SELECT qr.id, qr.category, a.source_question_texts, NOW() FROM quality_reference qr LEFT JOIN articles a ON a.id=qr.article_id WHERE qr.category IS NOT NULL`（own join 到 source_question_texts、external 无 join→NULL）；`drop_index ix_quality_reference_category`；`drop_column quality_reference.category`。
- downgrade：`add_column category` + `create_index ix_quality_reference_category`；回填 `UPDATE quality_reference qr SET category=(SELECT qrc.category FROM quality_reference_category qrc WHERE qrc.reference_id=qr.id LIMIT 1)`（多类型时 lossy，best-effort）；`drop_table quality_reference_category`。

**ORM**（`models.py`）：新增 `QualityReferenceCategory` 模型（同 models.py 内，`_model_modules` 已导入该模块、无需改 utils）；`QualityReference` 删 `category` 列、加 `categories` relationship（`cascade="all, delete-orphan"`）。

**service / schemas / router**：按上方"设计决策"改所有点。

**测试**：
- 新 `server/tests/test_qref_category_migration.py`（真 alembic：**先 monkeypatch `GEO_DATABASE_URL`=test URL**，`reset_test_database(create_schema=False)` 清空 → `upgrade("0062_adversarial_review")` → 原生 SQL 插 qref 行（own+external 各一，own 关联一篇有 source_question_texts 的 article）→ `upgrade("head")` → 断言子表回填正确 + quality_reference 无 category 列）。**安全铁律**：任何 alembic upgrade/downgrade 前必须把 GEO_DATABASE_URL 指到含 "test" 的库（env.py:24 会用 get_database_url() 覆盖 cfg），否则打到共享 geo_dev。
- 更新 `test_quality_reference_service.py` / `test_quality_reference_api.py` / `test_quality_reference_mcp.py` 到新契约（保持全绿）。
- 用户指定用例：同一 article adopt 后再 patch 加第二个类型 → 主表仍一行、子表两行；pick(类型A) 命中、pick(类型B) 也命中（一个 ref 两类型）；pick(无关联类型) 回落通用池；patch replace 生效。

**Commit**：`feat(adversarial): quality_reference category 单列改多对多子表 + 存问题词`

---

## Task 2（前端）：多类型 UI + replace-all patch + categories 类型

**Files**：`web/src/api/qualityReference.ts`（`QualityReference` 去单 `category`、加 `categories:{category:string;question_texts:string[]|null}[]`；`patchReference` body 改 `{is_active?; categories?}`；`importReference` body 加 `question_texts?`）；`web/src/features/quality-reference/QualityReferenceWorkspace.tsx`。

- 采纳/录入/编辑：category 单选 → 「可加多条（类型 + 可选问题词）」。录入多类型 = importReference 后再 `patchReference(id,{categories})` replace-all（或 import 单条后 patch）；编辑走 replace-all patch。
- 列表 category 过滤仍可用（后端 join）；行内展示该 ref 的类型标签集（`r.categories`）。
- `referenceCategories` 下拉源后端已改成 `QuestionItem.category ∪ quality_reference_category.category`（Task 1）；前端照用。
- 详情抽屉：类目显示改成展示 `detail.categories` 标签集（含问题词）。
- 门禁：`pnpm -C web typecheck && pnpm -C web build` 双绿。

**Commit**：`feat(adversarial): 前端高质量库多类型关联 UI（replace-all）`
