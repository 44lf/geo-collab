# 提示词模板按平台区分（platform 字段）设计

> 日期：2026-07-22　状态：待实现　类型：后端为主 + 前端 + MCP + skill

## 背景与目标

生成小红书图文时挑「生成提示词」，但提示词当前不区分平台——所有 `scope=generation` 的模板混在一起，主对话/大模型无法识别哪些是给小红书的、哪些是给头条文章的。后续要扩展发布到其它平台，不同平台的提示词写法不同。

给 `PromptTemplate` 加一个**平台**维度（与 `scope` 用途维度正交），让生成时能按平台筛选/识别、选到合适的提示词。

## 关键决策（已确认）

- **字段**：新增 `platform: str | None`（字符串 + 已知值，null=通用）。
- **已知值**（`VALID_PROMPT_PLATFORMS`，可扩展、加值不迁移）：`xiaohongshu` / `toutiao` / `wechat_mp`；**null=通用**（所有平台可用，存量模板自动归此）。
- **过滤语义**：传 platform → 返回 `platform == 该值` **OR** `platform IS NULL`（通用永远是候选）；不传 → 全部。
- **生效**：MCP `list_prompt_templates` 返回 `platform`（advisory，主对话识别）+ 加可选 `platform` 过滤参数。
- **范围**：全套——后端迁移/模型/schema/service + MCP + 前端编辑下拉/徽标 + xhs skill 引导。列表平台筛选**不做**（只徽标 + 编辑下拉）。

## 现状事实（代码）

- 模型 `server/app/modules/prompt_templates/models.py:PromptTemplate`：字段 `name/content/scope/user_id/is_system/is_enabled/is_deleted`。`scope` 是用途（generation/ai_format/image_search/image_companion），非平台。
- schema `schemas.py`：`PromptScope` Literal + `PromptTemplateCreate/Update/Read`。
- service `service.py`：`_validate_scope` / `_visible_query`（带 scope 过滤）/ `list_prompt_templates` / `create_prompt_template` / `update_prompt_template`。
- MCP：工具 `server/mcp/tools/catalog.py:list_prompt_templates(scope)` → `/api/mcp/prompt-templates?scope=`；端点 `mcp_catalog/router.py:190` `mcp_list_prompt_templates` 用 `svc_list_templates`，响应 `PromptTemplateRead`。
- 前端 `web/src/features/prompt-templates/PromptsWorkspace.tsx`：`PromptModal` 编辑弹窗（有 scope + is_system）；卡片展示。类型在 `web/src/types.ts`。
- 迁移 head：`0069_game_cull_and_manual` → 新迁移 `0070`。

## 架构与改动

### 后端
1. **迁移 `0070_prompt_template_platform.py`**（`down_revision="0069_game_cull_and_manual"`）：`prompt_templates` 加 `platform VARCHAR(50) NULL` + index `ix_prompt_templates_platform`。
2. **模型**：`platform: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)`。
3. **schema**：
   - `VALID_PROMPT_PLATFORMS = {"xiaohongshu", "toutiao", "wechat_mp"}`（null 另算通用，不入集合）。
   - `PromptTemplateCreate/Update` 加 `platform: str | None = None`；`PromptTemplateRead` 加 `platform: str | None`。
4. **service**：
   - `_validate_platform(platform)`：非 None 且不在 `VALID_PROMPT_PLATFORMS` → `ValidationError`。
   - `create_prompt_template` / `update_prompt_template`：校验 + 存 platform。**update 直接应用 platform（不走 scope/is_system 的「None=保持」）**：前端下拉恒传值（空=通用、否则 code），后端 `template.platform = payload.platform or None`（falsy→null=通用），这样能切回通用。前端是唯一更新方，不担心旧客户端误清。
   - `_visible_query` / `list_prompt_templates` 加可选 `platform`：`platform` 非 None 时 `filter( (PromptTemplate.platform == platform) | (PromptTemplate.platform.is_(None)) )`。不传不加此过滤。

### MCP
5. 端点 `/api/mcp/prompt-templates` 加 `platform: str | None = None` 查询参数 → 透传 `svc_list_templates`。`PromptTemplateRead`（已加 platform）随响应带出。
6. 工具 `list_prompt_templates(scope="generation", platform: str | None = None)`：把 platform 拼进 params。docstring 说明「传 platform 会连通用一起返回；不传返回全部」。

### 前端（提示词管理）
7. `PromptModal` 加**平台下拉**：选项 `通用(空)/ 小红书(xiaohongshu)/ 头条(toutiao)/ 公众号(wechat_mp)`；初值 `initial?.platform ?? ""`（空=通用）；`handleSave` 透传 `platform: value || null`。
8. 卡片加**平台徽标**（复用现有 `.badge`）：`通用/小红书/头条/公众号`（label 映射，未知回落 code）。
9. `types.ts`：`PromptTemplate` 类型 + Create/Update 载荷加 `platform: string | null`。API 客户端（`web/src/api/prompt-templates.ts`）透传。

### skill
10. `xhs-note-creator` SKILL.md：第 2 步改成 `list_prompt_templates(scope="generation", platform="xiaohongshu")`，引导「优先选小红书专属模板，无则用通用」。

## 测试
- 后端：`_validate_platform`（合法/非法/None）；`list_prompt_templates(platform="xiaohongshu")` 返回「小红书专属 + 通用」、不返回「头条专属」；create/update 存取 platform；MCP 端点 platform 参数 + 响应含 platform。对标 `test_prompt_templates*`（若有）/ `build_test_app`。
- 迁移 up/down round-trip。
- 前端：typecheck + build + 人工点验（编辑存平台、徽标显示）。

## 非目标（YAGNI）
- 列表平台筛选 UI（先徽标）。方案运行/pipeline 生文按平台自动选（本期只 xhs skill 用；web 生文仍手选）。不动 scope 语义。不把小红书加进发布 Platform 表/驱动（platform 只是提示词标签）。

## 交付 & 部署
后端迁移 + schema/service + MCP + 前端 + skill。`release-*`（server 迁移 + web）。按新纪律先合 main + 解决冲突再发版。
