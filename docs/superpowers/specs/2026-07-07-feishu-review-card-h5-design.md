# 飞书审核卡片 + 端内 H5 免登（首登自绑）设计

> 日期：2026-07-07
> 状态：待实现
> 关联上游：飞书文档《飞书可交互审核卡片 · 实施计划》（`docx/I5BidDRSCo2o70xz82WcvfN2n7b`）；本 spec 是与用户逐条收敛后的最终口径，若与该飞书文档冲突，**以本 spec 为准**。
> 依赖前置：单篇永久链接 `/article/:id` 已上线（`docs/superpowers/specs/2026-07-06-article-permalink-share-design.md`，已合并 main）。

## 1. 背景与目标

现状：`/loop`、`/goal`、pipeline 生文后文章置 `review_status="pending"`，人工必须进网站逐篇点「通过审核」。飞书侧只有整批跑完时经 webhook 机器人（`GEO_FEISHU_WEBHOOK_URL` → `feishu.py:send_text`）发一条纯文本汇总，不能点、不能审批。

**目标（本期 = B 档）**：每写完并自评一篇文章，就往飞书群发一张**交互卡片**（标题 / ID / 自评分 / 选题 / 封面 + 「查看文章」链接）。审核人在**飞书客户端内**点链接，端内打开 GEO 已上线的 `/article/:id` 只读页看全文，**首次手动登录一次 GEO 账号、之后自动免登**，看完回网页点「通过审核」。

**非目标（本期明确不做，留给后续 C 档）**：
- ❌ 飞书卡片内「审核通过」真按钮 + 公网回调 + 验签 + 白名单 + 卡片翻面（安全敏感的入站面，独立成期）。
- ❌ 在飞书里直接完成审批（本期审批动作仍在网页 `/article/:id` 或工作台点）。
- ❌ 卡片内渲染完整图文正文（飞书卡片能力不支持，全文一律靠端内 H5 看）。

## 2. 已确认的关键决策（与用户逐条收敛）

| 维度 | 决策 | 理由 |
|---|---|---|
| 全文展示 | **端内打开现有 `/article/:id` 永久链接页**，复用现有前端，不新建页面 | 永久链接已上线；卡片渲染不了完整图文 |
| 免登本质 | **复用现有 GEO 账号体系**（`User` 表 + 现有 JWT），不是单独账号体系；把「飞书登录」换成「GEO 登录」，不是读浏览器 cookie | `User.feishu_open_id` 字段 + admin 绑定入口已预埋 |
| 绑定方式 | **首登自绑**：库里 `feishu_open_id` 现全空，首次未命中 → 手动登录一次 → 后台自动回填 open_id → 之后免登 | 免去 admin 预先手工绑每个人；标准 account-linking |
| 登录逻辑 | **现有 `/api/auth/login` 一字不改**；免登 = 平行新入口复用 `create_access_token`；绑定 = 登录后独立的已登录写请求 | 绑定与登录解耦，登录端点无感 |
| cookie 写法 | **办法 A**：抽 `set_access_cookie(response, token)` helper，login 与免登两处共用（行为保持的小重构，保证两条路 cookie 标志一致） | 用户拍板 A |
| 身份归属 | 群里每个人点链接 → 各自免登成**各自的** GEO 用户（按 open_id 固定到人），非共享账号、非随机 | 审计落到人头 |
| 访问 vs 审批 | 两道独立的门：**看全文** 靠免登映射（永久链接允许任意登录用户只读）；**审批** 是 C 档的事、独立门控 | 能看可放宽、能拍板收紧 |

## 3. 范围与分层

本期三块，互相解耦：

```
① 出站发卡（自建应用 bot）
   生文成功 → notify_review_card(MCP) → POST /api/articles/{id}/review-card
   → send_review_card → 群里一张交互卡（链接指 /article/{id}）

② H5 全文展示（零新代码，复用已上线永久链接）
   卡片「查看文章」→ 飞书端内打开 https://<base>/article/{id} → 只读看全文

③ 首登自绑免登（H5 登录态）
   H5 加载 → 试免登 /api/feishu/h5-login
     命中 open_id → 直接种 GEO cookie（静默）
     未命中     → 手动登录一次 → /api/feishu/h5-bind 回填 open_id
```

## 4. 出站发卡（Phase 1 出站面）

### 4.1 新增配置 `server/app/core/config.py`

`Settings`（`feishu_app_id/secret` 已有）追加：

```python
feishu_public_base_url: str | None = None     # GEO_PUBLIC_BASE_URL  评审链接根 https://geo.example.com（无尾斜杠）
feishu_review_card_enabled: bool = False       # GEO_FEISHU_REVIEW_CARD_ENABLED  发卡总开关，默认关
feishu_review_chat_id: str | None = None       # GEO_FEISHU_REVIEW_CHAT_ID  目标群 chat_id（oc_xxx）
feishu_h5_enabled: bool = False                # GEO_FEISHU_H5_ENABLED  H5 免登开关，默认关（复用 app_id/secret）
```

> 测试改环境后须 `get_settings.cache_clear()`（config.py 既有约定）。

### 4.2 通用 API helper `server/app/shared/feishu_bitable.py`

复用既有 `get_tenant_access_token()` + `_TOKEN_INVALID_CODES` 刷新逻辑，抽出通用调用器：

```python
def feishu_api(method: str, path: str, *, body: dict | None = None) -> dict:
    # 拼 _FEISHU_BASE + 注入 Bearer tenant_access_token + token 失效重试一次
```

### 4.3 新文件 `server/app/shared/feishu_card.py`（不塞进 webhook-only 的 feishu.py）

```python
def build_review_card(*, article_id, title, question, score, decision, review_url, cover_url=None) -> dict:
    # 纯函数、无 I/O、可单测。卡片 2.0 JSON：
    #   header「📝 文章待审 #<id>」
    #   + 标题（lark_md，转义+截断）+ 可选封面 img + 选题 + 自评分/决策
    #   + action 块：url 按钮「查看文章」→ review_url
    #   本期不含「审核通过」primary 按钮（C 档再加，见 §9）

def send_review_card(*, chat_id, article_id, title, question, score, decision, review_url, cover_url=None) -> str | None:
    # POST im/v1/messages?receive_id_type=chat_id, msg_type=interactive, content 必须是 JSON 字符串
    # 返回 message_id；失败吞掉返回 None（仿 send_text，绝不让发卡拖垮 loop）
    # 未开启 feishu_review_card_enabled / 无 chat_id → 直接 None
```

### 4.4 发卡端点 `server/app/modules/articles/router.py`

挂现有 `articles_mcp_router`（MCP token 鉴权，非 user JWT）：

```python
@articles_mcp_router.post("/{article_id}/review-card", response_model=ReviewCardResponse,
    dependencies=[Depends(require_mcp_token)])
def post_review_card(article_id, payload, db=Depends(get_db)):
    # 查文章(404)；未开启 → sent=False
    # review_url = f"{settings.feishu_public_base_url}/article/{article_id}"   ← 复用永久链接，服务端拼
    # cover_url 从 article.cover_asset 派生（有则传）
    # mid = send_review_card(chat_id=settings.feishu_review_chat_id, ...)
    # 异常统一走 core/mcp_errors.mcp_exception_response（CLAUDE.md 规约）
```

> 注意：链接是 `/article/{id}`（已上线永久链接），**不是**上游飞书文档里旧写的 `/content/pending?articleId=`。

### 4.5 MCP 工具 `server/mcp/tools/action.py`

```python
@mcp.tool()
async def notify_review_card(article_id, title, question="", score=None, decision=None) -> dict:
    return await _apost(f"/api/articles/{article_id}/review-card", json={...})
```

- `from server.mcp.tools import action` 已在 server.py 触发注册，新 tool 自动出现。
- 同步 `mcp_catalog/connect_router.py:MCP_TOOLS_COUNT` **21 → 22**（CLAUDE.md：工具数唯一真值）。
- tool 必须 async + 自调用走线程池（`gotcha-mcp-selfcall-deadlock-sync-tool`：同步 tool 自调用会死锁）。

### 4.6 Loop 模板发卡点（保留收尾汇总文本，不动）

每轮自评后新增逐篇发卡，两处：
- `server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md`
- `claude-loops/generation-loop.md`

两处工具清单加 `notify_review_card`。改了 bundle 内文件 → 必须 bump `server/app/modules/loop_skills/version.py` 并按 posix 串排序 + LF 重取 sha 加进 `KNOWN_BUNDLE_SHAS`（CLAUDE.md + `gotcha-loop-bundle-sha-cross-os` 反复栽坑，改后跑 CI 取真值）。

**发卡决策口径（默认）**：仅对 `approved` + `needs_rewrite` 发卡，跳过 `rejected`（群清爽）。实现时若用户改主意再调。

## 5. H5 全文展示（零新代码）

卡片「查看文章」→ 飞书端内打开 `https://<base>/article/{id}`。该页 = 已上线永久链接：
- 后端 `read_article` 已放开属主校验，任意登录用户可只读（`can_edit=false` 时前端逐项屏蔽写入口）。
- 正文图 / 封面经 `/api/assets/{id}` 加载（无属主校验），端内正常显示。

**唯一要处理**：端内打开需飞书客户端 V7.4+，且后台把新页面打开方式配「飞书内新标签页打开」，否则 `target=_blank` 跳系统浏览器、免登断链（§8 坑）。

## 6. 首登自绑免登（H5 登录态，本期核心新增）

### 6.1 数据库迁移 `0056`（唯一改表项）

给 `users.feishu_open_id` 加**唯一索引**（当前是迁移 0013 加的裸 nullable 列，无约束）：

```python
# down_revision = "0055"
op.create_index("uq_users_feishu_open_id", "users", ["feishu_open_id"], unique=True)
# MySQL 唯一索引允许多个 NULL，与"可空 + 大量未绑定用户"并存无冲突
```

作用：数据库层兜底「一个飞书人只能绑一个 GEO 账号」，防自动回填串号。除此之外**零改表**。

### 6.2 新模块 `server/app/modules/feishu/`

**`service.py`**（无 I/O 的解析 + 短生命周期 session 的绑定/查询）：
```python
def resolve_open_id(code: str) -> str:
    # 用 feishu_app_id/secret 取 app_access_token
    #   → 调 authen/v2/oauth/token（client_id/secret/code）换 user_access_token
    #   → 拉用户信息得 open_id
    # 版本以官方最新为准（v1 authen/v1/access_token 仍可用，推荐 v2）

def find_user_by_open_id(db, open_id) -> User | None: ...
def bind_open_id(db, user, open_id) -> None:
    # user.feishu_open_id 为空才写（幂等）；open_id 已属他人 → 唯一索引报错 → 上抛友好冲突
```

**`router.py`**（两个端点）：
```python
# 免登入口：公开（进来时还没登录），仿 stock_files_router 挂法、加限流（仿 login 5/min）
POST /api/feishu/h5-login  { code }
    → 未开 feishu_h5_enabled → {authenticated:false, reason:"disabled"}
    → open_id = resolve_open_id(code)
    → user = find_user_by_open_id → 命中：set_access_cookie(response, create_access_token(user.id, user.role)) + {authenticated:true}
                                   → 未命中：{authenticated:false, reason:"unbound"}

# 绑定：已登录写请求，复用现有登录态校验（get_current_user / verify_token）
POST /api/feishu/h5-bind   { code }
    → current_user.feishu_open_id 非空 → {bound:true}（幂等，直接返回）
    → open_id = resolve_open_id(code) → bind_open_id(db, current_user, open_id)
    → 冲突（open_id 已属他人）→ 友好错误，不覆盖
```

> 可能需要配套 `GET /api/feishu/jssdk-config`（签 jsapi_ticket 供前端 `window.h5sdk` 初始化）——以官方 SDK 要求为准，实现时确认是否必需。

### 6.3 `main.py` 挂载

```python
# h5-login 公开（无 get_current_user、无 require_mcp_token），仿 stock_files_router（main.py:360）
app.include_router(feishu_h5_router, prefix="/api/feishu", tags=["feishu"])
# h5-bind 走标准 user JWT 鉴权（同其它 /api/* 已登录端点）
```

### 6.4 现有登录逻辑：不改，只做办法 A 小重构

- `POST /api/auth/login`（账号密码）**逻辑行为一字不改**。
- 把 login 里内联的 9 行 `response.set_cookie("access_token", ...)`（auth_router.py:118-126）抽成
  `set_access_cookie(response, token)`（放 `core/security.py` 或 auth_router 内），**login 与 h5-login 两处共用**。这是行为保持的重构：login 干的事不变，只保证两条路种的 cookie 标志（httponly/samesite/path/max_age/secure）完全一致。
- `create_access_token` / `verify_token` / `get_current_user` **只被调用，不修改**。

### 6.5 前端 H5 引导（`web/src/features/content/` 内，或 `/article` 路由外层薄封装）

H5 页加载时的引导逻辑（仅在检测到 `window.h5sdk` 存在、即飞书端内时启用）：

```
1. GET /api/auth/me
   已登录：
     - 渲染文章（既有）
     - 若 me.feishu_open_id 为空 且 在飞书端内 → 静默拿 code → POST h5-bind（自绑，见 §6.2）
   未登录 且 在飞书端内：
     - JSSDK 拿 code → POST h5-login
         authenticated:true  → 刷新/渲染文章
         reason:"unbound"     → 落到现有 GEO 登录页（提示「首次使用请用 GEO 账号登录一次」）
                                登录成功后回到本页 → 命中"已登录+open_id空"分支 → 自绑
   未登录 且 非飞书端内 → 现有登录重定向（行为不变）
```

- 非飞书端内（普通浏览器）打开 `/article/:id`：**行为与今天完全一致**，H5 引导不介入。
- CSRF/时效：`code` 短时效、server 端换取；h5-login 仅在飞书端内有意义 + 限流兜底。

## 7. 数据流（群聊多人）

```
【发卡】生文成功 → notify_review_card → POST /article/{id}/review-card
   → send_review_card（自建应用 bot）→ 群里一张交互卡

【张三点「查看文章」】飞书端内打开 /article/123
   → H5 引导：未登录+端内 → h5-login(code→张三 open_id)
       命中 → 种"张三"GEO cookie → 只读看全文
       未命中（首次）→ 手动登录成张三的 GEO 账号 → h5-bind 回填张三 open_id → 下次静默
【李四点】同链接 → h5-login(code→李四 open_id) → 登录成"李四"的 GEO 用户
   （身份按人固定，非共享、非随机）
```

## 8. 权衡 / 边界 / 坑

- **端内打开配置**：客户端 V7.4+，后台配「飞书内新标签页打开」+ H5 可信域名 + 重定向 URL（**末尾斜杠极敏感**，`invalid redirect uri` 多因此）。
- **open_id 按应用隔离**：h5-login/bind 解析出的 open_id 与 admin 手填的 `feishu_open_id`，必须都来自**同一个自建应用**。
- **首登信任模型**："first login wins"——第一个用某 GEO 账号在飞书登录的人，把该账号绑到自己的飞书身份。内部小团队可接受。
- **无 GEO 账号者**：手动登录无凭据 → 进不去，只能看不了全文。审批门（C 档）另有白名单，与看全文解耦。
- **JWT 8h 过期**：免登静默补发，审核人无感；未开免登时手动登录后 cookie 持久于 WebView 罐、8h 后需重登。
- **发卡失败不拖垮 loop**：`send_review_card` 吞异常返回 None（仿 `send_text`）。
- **敏感 helper**：h5-login 是公开的种 cookie 端点，务必限流 + code 由飞书侧验证（换取失败即拒），mapping 可信性来自已鉴权的 bind。

## 9. 范围外：C 档（飞书内直接审批）预留

本期不做，但设计不堵死：
- `build_review_card` 预留 `with_button` 开关位（本期恒 False）；C 档加 primary 按钮「审核通过」带 `value={action:"approve_article", article_id}`。
- C 档新增：`/api/feishu/card-callback`（公网、无鉴权 dep）+ 验签模块（sha256 + verification token + 可选 AES 解密）+ 幂等审批 + 卡片翻面。
- **审批授权可复用本期成果**：回调拿到 `operator.open_id` → 经本期已建立的 `feishu_open_id` 映射查到 GEO 用户及其 `role` → 按 GEO 角色（admin/operator）判定能否审批，比单独维护 open_id 白名单更省。（C 档设计时再定。）

## 10. 测试策略

**后端（纯 / 无 DB）** `test_feishu_card.py`：
- `build_review_card` 结构、lark_md 转义 / 截断、review_url = `{base}/article/{id}`、本期无 primary 按钮。
- `send_review_card`：monkeypatch `feishu_api` 断言 `receive_id_type=chat_id` / `msg_type=interactive` / `content` 是 JSON 串；未开启 / 无 chat_id 返回 None。

**后端（`@pytest.mark.mysql`）** `test_feishu_h5_api.py`：
- 发卡端点：`require_mcp_token`（无 token 401）；关开关 → sent=False。
- h5-login：命中 open_id → 种 cookie + authenticated:true；未命中 → unbound；关 `feishu_h5_enabled` → disabled。
- h5-bind：已登录 + open_id 空 → 回填成功；再调 → 幂等 bound:true；open_id 已属他人 → 冲突不覆盖。
- 唯一索引：两个用户绑同一 open_id → 第二个失败。
- **回归**：`/api/auth/login` 行为不变（抽 `set_access_cookie` 后 cookie 标志与改前一致）。

**前端**：无单测框架，门禁 = `pnpm --filter @geo/web typecheck` + `build`。手动验证：飞书端内首登→自绑→二次免登；普通浏览器打开 `/article/:id` 行为不变。

## 11. 有序落地顺序

1. **迁移 0056**：`feishu_open_id` 唯一索引。
2. **config**：4 个新配置项。
3. **办法 A 重构**：抽 `set_access_cookie`，login 改调它（行为不变 + 回归测试绿）。
4. **feishu 模块**：`service.py`（resolve/find/bind）+ `router.py`（h5-login / h5-bind）+ main.py 挂载。
5. **feishu_bitable.py**：`feishu_api` helper。
6. **feishu_card.py**：`build_review_card` + `send_review_card`。
7. **发卡端点** `POST /api/articles/{id}/review-card`（挂 articles_mcp_router）。
8. **MCP 工具** `notify_review_card` + `MCP_TOOLS_COUNT 21→22`。
9. **前端 H5 引导** + 手动登录兜底文案。
10. **loop 模板发卡点** ×2 + `version.py` bump + sha 重取（CI 取真值）。
11. **测试**：纯 + mysql + 前端 typecheck/build。
12. **飞书后台**：自建应用加 im:message + 机器人能力、网页应用（H5）能力、可信域名、重定向 URL、机器人拉群取 chat_id、发布新版本。

## 12. 验证（end-to-end）

设 `GEO_PUBLIC_BASE_URL` / `GEO_FEISHU_APP_ID/SECRET` / `GEO_FEISHU_REVIEW_CHAT_ID`、开 `feishu_review_card_enabled` + `feishu_h5_enabled`：
1. MCP `notify_review_card` 打一张卡到群 → 群里看到卡、封面/标题/自评分/选题齐。
2. 飞书端内点「查看文章」→ 端内标签页打开 `/article/{id}`。
3. 首次 → 手动登录一次 GEO → 自绑 → 关掉重点 → 免登直达。
4. 换个人点 → 登录成他自己的 GEO 用户（身份按人固定）。
5. `pytest server/tests/test_feishu_card.py -q`；`GEO_TEST_DATABASE_URL=... pytest server/tests/test_feishu_h5_api.py -q`。
