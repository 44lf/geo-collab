# Articles 模块渐进拆分实施计划

> **交给 Claude 执行：**严格按 Task 顺序、小步提交。默认只做结构重组，不改变 HTTP 路径、请求/响应、数据库模型、事务边界和业务语义。每个 Task 完成后先跑对应测试，失败时在当前 Task 内修复，不把失败带入下一步。

**日期：**2026-07-13  
**状态：**可执行  
**目标：**把 `server/app/modules/articles/` 从“大 Router + 大 Service + 大 AI Format”整理成职责清晰、可独立阅读和测试的内部模块，同时维持所有现有调用方兼容。  
**实施策略：**先建行为护栏，再拆 Router，再拆 Service，最后只抽取 `ai_format.py` 中低耦合的纯逻辑。前三个阶段即可取得主要维护收益；AI Format 深拆不是本计划的首批上线门槛。

---

## 1. 背景与现状

当前 Articles 模块至少承担以下职责：

- 文章 CRUD、列表、搜索和 Feed；
- 文章审核、封面和正文素材关联；
- 文章分组、每日分组和批量审核；
- 素材上传、文件读取、统计和孤儿清理；
- 分块上传；
- MCP 保存文章、配图、审核状态和飞书审核卡；
- AI 排版、标题转换、配图规划、联网补图和数据库写回。

现有代码已经形成天然边界，但文件没有按边界分开：

| 现有对象 | 当前位置 | 天然边界 |
|---|---|---|
| `articles_router` | `articles/router.py` | 文章 CRUD / 审核 / AI 排版触发 |
| `article_groups_router` | `articles/router.py` | 文章分组 |
| `assets_router` | `articles/router.py` | 素材 |
| `chunked_assets_router` | `articles/router.py` | 分块上传 |
| `articles_mcp_router` | `articles/router.py` | MCP 能力 |
| 文章/Feed/分组/每日分组 Service | `articles/service.py` | 多个独立用例簇 |
| 文档转换/Prompt/LLM/图片/写回 | `articles/ai_format.py` | 多个执行阶段 |

### 1.1 主要耦合风险

1. `main.py` 从 `server.app.modules.articles.router` 导入 5 个 Router 名称，旧路径必须保留。
2. Pipeline、AI Generation、MCP Catalog 和大量测试从 `server.app.modules.articles.service` 导入函数，旧路径必须保留。
3. `articles/__init__.py` 重导出 parser/service/store 的公开 API，不能在首轮拆分中删除。
4. `test_ai_format*.py` 大量 monkeypatch `server.app.modules.articles.ai_format` 内部符号；如果直接移动 `_call_litellm_completion`、`pick_image_id` 等依赖，测试 patch 可能静默失效。
5. `test_review_card_endpoint.py` monkeypatch `server.app.modules.articles.router.send_review_card`——该名字随 `post_review_card` 端点迁入 `routers/mcp.py` 后，facade 上会消失，patch 直接抛 `AttributeError`。这是与 #4 不同的 router 层断点，处理见 Task 1 Step 1.5。
6. 完整 `backend-test` GitLab Job 当前被隐藏；本计划实施期间不得以“CI 没跑”为通过依据，必须显式跑目标测试。

---

## 2. 目标目录

第一阶段目标目录：

```text
server/app/modules/articles/
├── __init__.py                    # 保留现有公开 API
├── models.py
├── schemas.py                     # 本计划暂不拆
├── parser.py
├── store.py
├── uploader.py
├── ai_illustrate_svc.py           # 本计划不改：ai-illustrate MCP 端点的底层实现，仅被 routers/mcp.py 引用
├── router.py                      # 兼容 facade，仅重导出 Router
├── routers/
│   ├── __init__.py
│   ├── articles.py                # 文章 CRUD、审核、封面、AI 排版触发
│   ├── groups.py                  # 分组接口
│   ├── assets.py                  # 素材接口
│   ├── chunked_assets.py          # 分块上传接口
│   └── mcp.py                     # MCP 接口
├── service.py                     # 兼容 facade，仅重导出 Service API
├── services/
│   ├── __init__.py
│   ├── body_assets.py             # 正文素材同步和素材存在性校验
│   ├── articles.py                # 单篇文章 CRUD、封面
│   ├── feed.py                    # 列表、搜索、Feed、摘要统计
│   ├── review.py                  # 文章审核
│   ├── groups.py                  # 分组 CRUD、批量审核
│   └── daily_groups.py            # 每日分组和流式追加
├── ai_format.py                   # 先保留总编排和兼容 patch 点
└── formatting/
    ├── __init__.py
    └── document.py                # 首批仅迁移无 IO 的文档纯函数
```

本计划完成后仍保留：

```python
from server.app.modules.articles.router import articles_router
from server.app.modules.articles.service import create_article
from server.app.modules.articles import get_article
from server.app.modules.articles.ai_format import run_ai_format
```

---

## 3. 强制约束

### 3.1 本计划允许的变化

- 新增子目录和文件；
- 移动函数及其直接依赖；
- 用 facade 重导出现有名称；
- 为保证兼容性调整内部 import；
- 新增路由契约测试、纯函数测试和 import 门禁；
- 删除移动完成后旧文件中的重复实现。

### 3.2 本计划禁止的变化

- 不改 API URL、HTTP method、鉴权依赖、tag、状态码和 response model；
- 不改数据库表、字段、外键、Alembic 迁移；
- 不改 Article 三份正文的现有语义；
- 不改审核、软删除、分发去重和每日分组口径；
- 不把同步函数改成异步函数；
- 不引入 Repository/Unit of Work/CQRS 等新框架；
- 不顺便接入 Wechatsync；
- 不顺便实现通用 Job Worker；
- 不在本计划中删除 `articles/__init__.py`、`router.py`、`service.py`、`ai_format.py` 兼容入口；
- 不在同一提交中进行格式化全仓、重命名业务概念或清理无关 legacy 代码。

### 3.3 行为不变判定

以下任一项发生即视为行为变化，必须停止并单独评审：

- OpenAPI 路由 path/method/name/response model 变化；
- JWT/MCP 鉴权位置变化；
- commit/rollback 次数或事务边界变化；
- 后台线程启动时机变化；
- 同一请求返回的字段、状态码或错误类型变化；
- 函数被移动后 monkeypatch 不再作用于真实调用点；
- SQL 查询过滤条件、排序、分页或 eager-load 策略变化。

---

## 4. 执行与验证约定

### 4.1 环境

```powershell
conda activate geo_xzpt
$env:GEO_TEST_DATABASE_URL = "mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test"
```

测试库名称必须包含 `test`。如实际账号不同，使用本机已有的测试连接串，不要把密码写入仓库。

### 4.2 每个 Task 都要跑

```powershell
ruff check server/app/modules/articles server/tests
ruff format --check server/app/modules/articles server/tests
mypy server/app
```

涉及 HTTP/DB 的 Task 还要跑对应 pytest。计划末尾有全量目标集。

### 4.3 提交纪律

- 一个 Task 一个提交；Router 五个文件可以在同一个 Task/提交内完成，因为必须同时切 facade。
- 提交只包含本 Task 涉及的文件。
- 开始前检查 `git status --short`；现有无关改动属于用户，不覆盖、不顺手提交。
- 发生复杂冲突时优先保留用户现有代码，并停止请求确认。

---

# Phase 0：建立行为护栏

## Task 0：记录公开入口和路由契约

**目标：**在移动代码前锁定当前 Router 与兼容 import，避免“测试全绿但路由少挂了一个”。

**Files:**

- Add: `server/tests/test_articles_module_contract.py`
- Read only: `server/app/main.py`
- Read only: `server/app/modules/articles/router.py`
- Read only: `server/app/modules/articles/service.py`
- Read only: `server/app/modules/articles/__init__.py`

- [ ] **Step 1：为 5 个 Router 建相对路径契约测试。**

直接导入 Router，收集每个 `APIRoute` 的 **path / methods / name / status_code / response_model / 依赖调用名**——只锁 `(path, methods, name)` 不够，因为本计划禁改 status_code、response_model 和鉴权依赖（见 §3.2/§3.3），必须一并入签名，否则搬动时漏掉 `status_code=204`、`response_model=` 或 `Depends(require_mcp_token)` 契约测试仍会假绿：

```python
def _call_name(call):
    return f"{call.__module__}.{call.__qualname__}"


def _route_signature(router):
    return {
        (
            route.path,
            tuple(sorted(route.methods or [])),
            route.name,
            route.status_code,
            getattr(route.response_model, "__name__", None),
            tuple(
                _call_name(dep.call)
                for dep in route.dependant.dependencies
                if dep.call is not None
            ),
        )
        for route in router.routes
    }
```

重点锁住：删除文章 `204`（`router.py:272`）、AI Format `202`（`router.py:361`）、分组删除 `204`（`router.py:546`）、全部 `response_model`，以及 5 个 MCP 端点的 `require_mcp_token`。

> **鉴权捕获的实测事实（2026-07-13 真库内省，务必按此理解，别误修）：**
> - MCP token 逐 endpoint 内联挂（`router.py:988/1082/1160/1256/1290` 各自 `dependencies=[Depends(require_mcp_token)]`）→ 孤立 router 内省 `route.dependant.dependencies` **能**看到 `mcp_auth.require_mcp_token`、被本签名锁住；且 MCP 路由**不含** `get_current_user`。
> - **大多数 JWT 端点**在函数签名里显式写了 `current_user: User = Depends(get_current_user)`（属主校验要用），所以它们的 dependant **内联可见** `security.get_current_user`——本契约测试**能**锁住。（`main.py` include 时还会再挂一层同名依赖，属双层，与本测试无冲突。）
> - **例外（要记住，别当成 bug）：**3 个 asset 文件服务路由 `read_asset_file` / `read_asset_meta` / `read_asset_thumbnail` 的 dependant **只有** `session.get_db`、**没有** `get_current_user`（它们不做属主校验，靠 `main.py` include-time 依赖兜底鉴权）；`asset_stats` / `cleanup_orphan_assets` 用的是 `security.require_admin` 而非 `get_current_user`。这些都是**当前真实契约**，已如实冻进基线（见 `test_articles_module_contract.py` 的 `EXPECTED_SIGNATURES`）。
> - 结论：**照实冻结、逐项 assert 相等即可，不要为"补齐鉴权"往任何路由上加/减依赖**——那才是真改了鉴权位置。`main.py` 的 include 依赖不在本快照内，由 §8.4 人工核对 + `main.py 无变化` 守住。

> **环境坑（本仓已知，必须按此写）：**导入 `articles.router` 会连带加载 `db.session`，而 `session.py` 在 **import 期**就执行 `ensure_data_dirs()`（`session.py:11`，缺 `GEO_DATA_DIR` 抛 RuntimeError）和 `create_engine(get_database_url())`（`session.py:28`，缺 `GEO_DATABASE_URL`/`GEO_DB_*` 时 `core/paths.py:37` 抛 RuntimeError）。`GEO_TEST_DATABASE_URL` **仅**被 `build_test_app()`（`server/tests/utils.py:195-196`）转写成 `GEO_DATABASE_URL`+`GEO_DATA_DIR`；只设它 + 打 `@pytest.mark.mysql`（marker 只 skip、不设 env）**不够**，Step 3 隔离运行会在 import Router 时直接 RuntimeError。
>
> **统一走 `build_test_app`**（与全仓测试体系一致，最不易污染进程态——`session` 的全局 Engine/`SessionLocal` 在 import 期只建一次，`monkeypatch` 事后恢复 env 不会重建它，手动 setenv 再 import 的写法会给后续测试引入顺序依赖，故本计划**不采用**手动 setenv 方案）：
>
> ```python
> @pytest.mark.mysql
> def test_article_router_contract(monkeypatch):
>     test_app = build_test_app(monkeypatch)  # 设 GEO_DATABASE_URL + GEO_DATA_DIR
>     try:
>         from server.app.modules.articles.router import articles_router  # 函数体内 import
>         ...
>     finally:
>         test_app.cleanup()
> ```
>
> 本文件**所有**用例（含下面 Step 2 的兼容 import smoke test）统一打 `@pytest.mark.mysql`、共用同一套 `build_test_app` 起停（可抽成一个 fixture 复用），且把待验证的 import 全放进函数体内——不要在模块顶层 import Router。

至少断言当前全部路径存在，而不是只断言数量。覆盖：

- `articles_router`：列表、feed、CRUD、封面、审核、撤回审核、AI format；
- `article_groups_router`：CRUD、items、approve-all；
- `assets_router`：上传、stats、cleanup、meta、thumbnail、文件；
- `chunked_assets_router`：upload-start/chunk/status/complete；
- `articles_mcp_router`：illustrate、ai-illustrate、save、review status、review card。

- [ ] **Step 2：为兼容导入建 smoke test。**

同样打 marker、同样在 `build_test_app` 起好 env 后再函数体内 import（原因见 Step 1 环境坑）：

```python
@pytest.mark.mysql
def test_articles_public_imports_remain_available(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.articles.router import articles_router
        from server.app.modules.articles.service import create_article, get_article
        from server.app.modules.articles import store_bytes

        assert articles_router is not None
        assert callable(create_article)
        assert callable(get_article)
        assert callable(store_bytes)
    finally:
        test_app.cleanup()
```

- [ ] **Step 3：运行新契约测试，确认在移动前通过。**

```powershell
pytest server/tests/test_articles_module_contract.py -q
```

- [ ] **Step 4：记录基线，不修改生产代码。**

**完成标准：**新测试在当前结构下通过；测试明确列出路由，而不是宽松地只判断非空。

---

# Phase 1：拆 Router（第一批上线目标）

## Task 1：建立 `routers/` 并移动五组端点

**目标：**把约 1300 行 Router 按已有 APIRouter 边界拆为五个文件；不改 `main.py`，不改任何 URL。

**Files:**

- Add: `server/app/modules/articles/routers/__init__.py`
- Add: `server/app/modules/articles/routers/articles.py`
- Add: `server/app/modules/articles/routers/groups.py`
- Add: `server/app/modules/articles/routers/assets.py`
- Add: `server/app/modules/articles/routers/chunked_assets.py`
- Add: `server/app/modules/articles/routers/mcp.py`
- Modify: `server/app/modules/articles/router.py`
- Test: `server/tests/test_articles_module_contract.py`

### Step 1.1：移动文章端点

移动到 `routers/articles.py`：

- `articles_router`；
- `AIFormatRequest`；
- `_verify_article_ownership`；
- `_is_ai_lock_expired`；
- `_clear_ai_lock_if_expired`；
- `_check_not_ai_locked`；
- `read_articles`；
- `read_article_feed`；
- `create_article_endpoint`；
- `read_article`；
- `update_article_endpoint`；
- `delete_article_endpoint`；
- `update_article_cover`；
- `approve_article_endpoint`；
- `revoke_article_approval_endpoint`；
- `trigger_ai_format_endpoint`。

要求：完整复制原 decorator、参数、Depends、response model、status code、后台线程逻辑和异常处理。

### Step 1.2：移动分组端点

移动到 `routers/groups.py`：

- `article_groups_router`；
- `_verify_group_ownership`；
- `_group_read_with_summary`；
- `read_groups`；
- `create_group_endpoint`；
- `read_group`；
- `update_group_endpoint`；
- `delete_group_endpoint`；
- `update_group_items`；
- `approve_group_endpoint`。

### Step 1.3：移动素材端点

移动到 `routers/assets.py`：

- `assets_router`；
- `resolve_asset_path_from_storage_key`；
- `to_asset_read`；
- 上传端点；
- `asset_stats`；
- `cleanup_orphan_assets`；
- `read_asset_meta`；
- thumbnail；
- 文件读取端点。

不要把 `articles/store.py` 的底层存储函数一起移动。

### Step 1.4：移动分块上传端点

移动到 `routers/chunked_assets.py`：

- `chunked_assets_router`；
- `ChunkedUploadStartRequest`；
- `ChunkedUploadCompleteRequest`；
- start/chunk/status/complete 四个端点；
- 这些端点直接依赖的局部常量/helper。

不要重写 uploader 行为，不改变 chunk 校验和临时目录清理。

> **已知的一处跨 router 依赖：**`complete_chunked_upload`（`router.py:944`）会调用 `to_asset_read`——该函数按 Step 1.3 归入 `routers/assets.py`。因此 `routers/chunked_assets.py` 需要 `from .assets import to_asset_read`。这是包内单向 import（assets 不反向依赖 chunked_assets），**不构成循环**，照写即可；不要为了"避免跨文件"把 `to_asset_read` 复制一份到 chunked_assets.py。

### Step 1.5：移动 MCP 端点

移动到 `routers/mcp.py`：

- `articles_mcp_router`；
- MCP 请求/响应 Pydantic 类；
- illustrate；
- ai-illustrate（其底层实现 `ai_illustrate_svc.py` 不动，`routers/mcp.py` 照旧 import）；
- save article；
- set review status；
- review card。

MCP token 鉴权必须保持在原来的 endpoint/dependency 位置，不能因为拆文件改挂 JWT。

> **必查的 monkeypatch 断点（会真炸，共 2 组）：**端点随文件搬走后，凡是把它们 import 进 `router.py` 命名空间的名字都会从 facade `articles.router` 上消失，任何 `monkeypatch.setattr("server.app.modules.articles.router.<name>", ...)` 都会抛 `AttributeError`。本模块有两组：
>
> **(a) `send_review_card` / `build_review_link`**（`router.py:968` 中段 import，来自 `server.app.shared.feishu_card`；被 `post_review_card` 用）。断点在 `server/tests/test_review_card_endpoint.py:62/99`。
>
> **(b) `illustrate_one`**（`router.py:965` import，来自 `articles.ai_illustrate_svc`；被 `ai_illustrate_article_mcp` 在 `router.py:1098` 用）。断点在 `server/tests/test_articles_ai_illustrate_endpoint.py:61/111` **和** `server/tests/test_illustrate_game_list_endpoint.py:35/72`（后者此前不在验证清单里，易漏）。
>
> 两组统一处理（二选一，优先前者）：
> 1. **改测试 patch 到真实使用点** `server.app.modules.articles.routers.mcp.<name>`（推荐，符合 patch-at-usage-site）；
> 2. 若要保旧路径，在 facade 显式 `from .routers.mcp import <name>` alias，并加一条兼容测试说明——**不得用 `import *` 糊**。
>
> 无论哪种，**本 Task 必须把 `test_review_card_endpoint.py`、`test_articles_ai_illustrate_endpoint.py`、`test_illustrate_game_list_endpoint.py` 三个全纳入 Step 1.7 运行**，否则破坏在本轮不会被任何目标测试暴露。

### Step 1.6：将旧 `router.py` 改成 facade

```python
"""Articles 路由兼容入口；具体端点按职责位于 routers/。"""

from .routers.articles import articles_router
from .routers.assets import assets_router
from .routers.chunked_assets import chunked_assets_router
from .routers.groups import article_groups_router
from .routers.mcp import articles_mcp_router

__all__ = [
    "articles_router",
    "article_groups_router",
    "assets_router",
    "chunked_assets_router",
    "articles_mcp_router",
]
```

不要修改 `main.py` 的导入路径。

### Step 1.7：验证

```powershell
pytest server/tests/test_articles_module_contract.py -q
pytest server/tests/test_articles_api.py server/tests/test_article_groups_api.py -q
pytest server/tests/test_assets_api.py -q
pytest server/tests/test_articles_ai_illustrate_endpoint.py server/tests/test_illustrate_game_list_endpoint.py server/tests/test_save_article_mcp.py server/tests/test_review_card_endpoint.py -q
ruff check server/app/modules/articles server/tests/test_articles_module_contract.py
ruff format --check server/app/modules/articles server/tests/test_articles_module_contract.py
mypy server/app
```

**完成标准：**

- `articles/router.py` 只剩 facade；
- `main.py` 无变化；
- 路由契约完全一致；
- 不产生 Alembic migration；
- 目标测试通过。

**停止条件：**如果 monkeypatch 依赖旧 `articles.router.<helper>` 路径，不要通过在 facade 复制实现来糊住；先判断该 helper 是否真是公开 patch 点。需要兼容时在 facade 显式 alias，并加测试说明，不能使用 `import *`。

---

# Phase 2：拆 Service（第二批上线目标）

## Task 2：拆正文素材和文章 CRUD

**目标：**先迁移依赖最清晰的基础能力，为 Feed/Group 拆分提供底座。

**Files:**

- Add: `server/app/modules/articles/services/__init__.py`
- Add: `server/app/modules/articles/services/body_assets.py`
- Add: `server/app/modules/articles/services/articles.py`
- Modify: `server/app/modules/articles/service.py`

### Step 2.1：移动正文素材能力

移动到 `services/body_assets.py`：

- `ensure_asset_exists`；
- `sync_article_body_assets`。

该文件只依赖 Article/Asset 模型、parser 结果和 SQLAlchemy，不反向 import `services/articles.py`。

### Step 2.2：移动文章基础能力

移动到 `services/articles.py`：

- `VALID_ARTICLE_STATUSES`；
- `VALID_REVIEW_STATUSES`（如果 review 也依赖，后续由 facade/单一常量模块重导出，禁止复制两份）；
- `validate_article_status`；
- `get_article`；
- `create_article`；
- `update_article`；
- `set_article_cover`；
- `delete_article`。

文章创建/更新继续调用 `body_assets.py`，保持原 flush/commit 责任不变。Service 不新增自动 commit。

### Step 2.3：在旧 `service.py` 重导出

所有现有导入继续工作。使用显式 import 和 `__all__`，不要 `import *`。

### Step 2.4：验证

```powershell
pytest server/tests/test_articles_api.py server/tests/test_article_writer.py -q
pytest server/tests/test_article_writer_review_status.py server/tests/test_article_writer_title.py -q
pytest server/tests/test_mcp_catalog.py server/tests/test_save_article_mcp.py -q
ruff check server/app/modules/articles
ruff format --check server/app/modules/articles
mypy server/app
```

**完成标准：**外部调用仍可从 `articles.service` 和 `articles` 包入口导入相同函数；没有循环 import。

---

## Task 3：拆 Feed 查询

**目标：**把读模型和复杂查询从写 Service 中分离，保持 SQL 完全一致。

**Files:**

- Add: `server/app/modules/articles/services/feed.py`
- Modify: `server/app/modules/articles/service.py`

移动到 `services/feed.py`：

- `_list_summary_load_options`；
- `_search_articles`；
- `list_articles`；
- `serialize_article_summaries`；
- `_feed_article_branch`；
- `_feed_group_match`；
- `_feed_group_branch`；
- `_feed_counts`；
- `list_article_feed`。

要求：

- SQLAlchemy where、join、exists、order_by、pagination 原样迁移；
- 不在本 Task 中处理 `PublishRecord` 跨模块依赖；发布状态抽象属于另一个计划；
- 不改变 eager-load 和序列化调用顺序；
- `service.py` 显式重导出公开函数。

> **必查的 spy 断点（会静默假绿）：**`server/tests/test_search.py:199-210` 现在 `real = svc._search_articles` 后 `monkeypatch.setattr(svc, "_search_articles", spy)`——它 patch 的是 facade `articles.service` 上的名字。迁移后 `_search_articles` 与 `list_articles` 同住 `services/feed.py`，`list_articles.__globals__` 属于 `feed` 模块，其内部对 `_search_articles` 的调用**绑定在 feed 命名空间**，patch facade 名字不影响真实调用 → spy 调用数为 0 → `assert calls["n"] >= 1` 失败（且这是"验证 FTS 没退化成 LIKE"的关键断言，不能糊）。
>
> 本 Task 必须同步把该测试改为 patch 真实使用点：
> ```python
> from server.app.modules.articles.services import feed
> real = feed._search_articles
> monkeypatch.setattr(feed, "_search_articles", spy)
> ```
> **不要**为这个私有函数在 facade 里加动态 wrapper 来"骗过" patch。

验证：

```powershell
pytest server/tests/test_articles_feed.py server/tests/test_article_list_score.py -q
pytest server/tests/test_articles_published_count.py server/tests/test_search.py -q
ruff check server/app/modules/articles
ruff format --check server/app/modules/articles
mypy server/app
```

---

## Task 4：拆审核、分组和每日分组

**目标：**把内容状态变化、普通分组 CRUD、每日分组编排分开，保留原事务和并发语义。

**Files:**

- Add: `server/app/modules/articles/services/review.py`
- Add: `server/app/modules/articles/services/groups.py`
- Add: `server/app/modules/articles/services/daily_groups.py`
- Modify: `server/app/modules/articles/service.py`

### Step 4.1：审核

移动到 `services/review.py`：

- `_get_owned_article`；
- `_set_article_review_status`；
- `approve_article`；
- `revoke_article_approval`。

保持 operator/admin 权限和命名异常不变。

### Step 4.2：普通分组

移动到 `services/groups.py`：

- `get_group`；
- `list_groups`；
- `create_group`；
- `update_group`；
- `replace_group_items`；
- `delete_group`；
- `compute_group_review_summary`；
- `approve_group`。

### Step 4.3：每日分组与流式追加

移动到 `services/daily_groups.py`：

- `mark_pending_and_group`；
- `mark_pending_and_append_daily`；
- `resolve_or_create_daily_group`；
- `append_article_to_group_pending`；
- 只被这些函数使用的局部 helper。

注意：每日分组的 `sort_order`、重复追加、commit 时机和并发行为是业务契约；本 Task 只移动，不优化。

### Step 4.4：验证

```powershell
pytest server/tests/test_article_groups_api.py server/tests/test_daily_grouping.py -q
pytest server/tests/test_streaming_daily_group.py server/tests/test_ai_compose_daily_group.py -q
pytest server/tests/test_pipeline_review_distribute.py -q
ruff check server/app/modules/articles
ruff format --check server/app/modules/articles
mypy server/app
```

**完成标准：**`articles/service.py` 只做显式兼容导出，不再包含业务实现。

---

## Task 5：建立模块 import 门禁

**目标：**防止后续代码又回填到 facade，锁住本次整理成果。

**Files:**

- Modify: `server/tests/test_articles_module_contract.py`

增加轻量 AST/文本门禁，至少保证：

- `articles/router.py` 不再定义 endpoint；
- `articles/service.py` 不再定义业务函数；
- `routers/` 不从 `server.app.modules.articles.router` 反向导入；
- `services/` 不从 `server.app.modules.articles.service` 反向导入；
- 新代码不得通过 `import *` 构建 facade。

不要在本 Task 中建立全仓完美依赖图；只约束本次已经确定的边界。

验证：

```powershell
pytest server/tests/test_articles_module_contract.py -q
```

---

# Phase 3：谨慎抽取 AI Format 纯逻辑（可单独排期）

## Task 6：只抽取无 IO 文档转换函数

**目标：**降低 `ai_format.py` 阅读负担，但不破坏大量 monkeypatch 点。此 Task 不移动 LLM、图片选择、下载、DB session 和总编排。

**Files:**

- Add: `server/app/modules/articles/formatting/__init__.py`
- Add: `server/app/modules/articles/formatting/document.py`
- Modify: `server/app/modules/articles/ai_format.py`
- Add or Modify: `server/tests/test_ai_format_document.py`

优先移动这些纯函数及必要常量：

- `_top_level_text_nodes`；
- `_non_empty_text_nodes`；
- `has_ai_format_targets`；
- `_node_text`；
- `_normalize_game_name`；
- `_find_heading_index`；
- `build_image_positions_from_game_list`；
- `_to_heading`；
- `_to_paragraph`；
- `_inline_html`；
- `_node_html`；
- `_node_plain_text`；
- `_derive_html_and_text`；
- `_normalize_heading_indices`；
- `_apply_headings`。

### 兼容规则

`ai_format.py` 必须显式重导出当前被外部/测试使用的公开或事实公开名称：

```python
from .formatting.document import (
    build_image_positions_from_game_list,
    has_ai_format_targets,
)
```

对于仅在 `ai_format.py` 内部使用的 `_private` 函数，可以直接从新模块导入；如果现有测试直接调用或 patch，则先保留 alias 并补兼容测试。

### 明确不移动

- `_call_litellm_completion`；
- `pick_image_id`、`fetch_image_by_id` 等当前 patch 点；
- `_maybe_insert_images`；
- `_web_fallback_*`；
- `_ai_format_prepare`；
- `_ai_format_write_back`；
- `_ai_format_finalize_error`；
- `run_ai_format`；
- `run_ai_format_from_game_list`。

验证：

```powershell
pytest server/tests/test_ai_format_document.py -q
pytest server/tests/test_ai_format.py server/tests/test_ai_format_error_messages.py -q
pytest server/tests/test_ai_format_connection_lifecycle.py -q
pytest server/tests/test_ai_illustrate_svc.py server/tests/test_ai_illustrate_node.py -q
pytest server/tests/test_illustrate_render_fix.py server/tests/test_illustrate_game_list_resolver.py -q
pytest server/tests/test_image_search_prompts.py -q
ruff check server/app/modules/articles
ruff format --check server/app/modules/articles
mypy server/app
```

> **上面两个是被移函数的直接回归：**`test_illustrate_render_fix.py` 直接 `from ...ai_format import _derive_html_and_text`，`test_illustrate_game_list_resolver.py` 直接 import `_normalize_game_name` + `build_image_positions_from_game_list`。三者都是 Task 6 要迁的函数，靠 `ai_format.py` 的显式 re-export 兜底——其中 `_normalize_game_name` 是 `_private` 但被测试直接 import，按本 Task「兼容规则」必须在 `ai_format.py` 保留 alias。跑这两个测试才能确认 re-export/alias 到位、没漏。

**停止条件：**如果为了移动纯函数必须改变 LLM/图片/DB 的调用点，停止此 Task；不要扩大范围。

---

## Task 7：评估是否继续深拆 AI Format（决策项）

完成 Task 6 后再评估，不默认实施。

- [ ] `ai_format.py` 是否已经降到可接受复杂度；
- [ ] monkeypatch 是否仍大量依赖模块内部符号；
- [ ] Prompt、图片规划、联网下载能否按纯输入/输出形成稳定 seam；
- [ ] 是否应先完成通用 Job Worker，再移动总编排；
- [ ] 是否值得另写 `ai-format-decomposition` 设计和计划。

如果继续深拆，必须另起计划，不能在本计划尾部临时扩范围。

---

# Phase 4：全量签收

## Task 8：回归、静态检查和结构验收

### 8.1 目标测试集

```powershell
pytest `
  server/tests/test_articles_module_contract.py `
  server/tests/test_articles_api.py `
  server/tests/test_article_groups_api.py `
  server/tests/test_articles_feed.py `
  server/tests/test_article_list_score.py `
  server/tests/test_articles_published_count.py `
  server/tests/test_search.py `
  server/tests/test_assets_api.py `
  server/tests/test_save_article_mcp.py `
  server/tests/test_review_card_endpoint.py `
  server/tests/test_mcp_catalog.py `
  server/tests/test_article_writer.py `
  server/tests/test_article_writer_review_status.py `
  server/tests/test_article_writer_title.py `
  server/tests/test_daily_grouping.py `
  server/tests/test_streaming_daily_group.py `
  server/tests/test_ai_compose_daily_group.py `
  server/tests/test_pipeline_review_distribute.py `
  server/tests/test_ai_format.py `
  server/tests/test_ai_format_error_messages.py `
  server/tests/test_ai_format_connection_lifecycle.py `
  server/tests/test_ai_illustrate_svc.py `
  server/tests/test_ai_illustrate_node.py `
  server/tests/test_articles_ai_illustrate_endpoint.py `
  server/tests/test_illustrate_game_list_endpoint.py `
  server/tests/test_illustrate_render_fix.py `
  server/tests/test_illustrate_game_list_resolver.py `
  server/tests/test_image_search_prompts.py `
  -q
```

> 上表是各 Task 目标测试的**并集**——终审集必须覆盖所有被移动代码碰过的用例，尤其 `test_review_card_endpoint.py`（Task 1 monkeypatch 断点）、`test_search.py`（Feed 搜索）、`test_pipeline_review_distribute.py` / `test_ai_compose_daily_group.py`（每日分组跨模块调用）这几个此前只出现在分任务列表里、极易在终审漏跑的。

**强制全量回归**（不再是"环境允许才跑"）：完整 `backend-test` GitLab Job 当前被隐藏，本计划不得以 CI 兜底，必须本地实跑全量。

本仓 main 有一个**在册的已知红**——`test_old_write_endpoints_are_removed`（`server/tests/test_loop_skill_bundle_versions.py:509`，断言 405 实为 404，属 loop_skills，团队决定暂不修；`backend-test` job 正因它被整体隐藏，见 `.gitlab-ci.yml:143-148`），与 Articles 无关。为让验收可**自动判定**（而不是人肉从长输出里数失败项），把它 deselect 掉后要求全绿：

```powershell
# 这条必须全绿（0 失败、退出码 0）
pytest server/tests/ -q `
  --deselect server/tests/test_loop_skill_bundle_versions.py::test_old_write_endpoints_are_removed
```

再单独把那条已知红跑一遍、确认它**仍是唯一**失败、且失败原因未变（405/404，与本次改动无关）：

```powershell
# 预期：仍失败，且仅此一条。若它变绿或换了失败原因，说明碰到了不该碰的东西，停下排查
pytest server/tests/test_loop_skill_bundle_versions.py::test_old_write_endpoints_are_removed -q
```

> **口径（重要，避免自相矛盾）：**第一条 deselect 命令必须全绿；已知红仅限 `test_old_write_endpoints_are_removed` 一条。本计划禁止顺手修改无关 legacy 代码——**不要**为了"全绿"去改 / xfail 这个 loop_skills 测试（如确实想清零基线，另起独立 PR 处理，不混进本轮结构整理）。

### 8.2 静态检查

```powershell
ruff check server/
ruff format --check server/
mypy server/app
```

### 8.3 前端验证

本计划不改前端，但 API 契约相关改动必须确认前端仍可构建：

```powershell
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web build
```

### 8.4 人工结构验收

- [ ] `router.py` 只做 Router facade；
- [ ] `service.py` 只做 Service facade；
- [ ] `main.py` 的 Article Router 导入和 prefix 未改变；
- [ ] `articles/__init__.py` 的现有公开名称仍可用；
- [ ] 没有新增数据库迁移；
- [ ] 没有跨模块复制 ORM Model；
- [ ] 没有新增 `import *`；
- [ ] 没有把 MCP 端点误挂到 JWT 鉴权或反过来；
- [ ] 没有更改 AI Format 的线程、连接生命周期和锁语义；
- [ ] 新增代码落在具体子模块，facade 未重新膨胀；
- [ ] `git diff --stat` 中没有无关文件。

---

## 5. 推荐 PR 划分

| PR | 内容 | 风险 | 可独立回滚 |
|---|---|---:|---:|
| PR 1 | Task 0 + Task 1：契约护栏 + Router 拆分 | 低 | 是 |
| PR 2 | Task 2：正文素材 + Article CRUD Service | 中低 | 是 |
| PR 3 | Task 3：Feed 查询 Service | 中低 | 是 |
| PR 4 | Task 4 + Task 5：分组/审核/每日分组 + 门禁 | 中 | 是 |
| PR 5 | Task 6：AI Format 纯文档函数 | 中 | 是 |

PR 1～4 是本轮推荐必做范围；PR 5 可在前四个稳定后再做。

---

## 6. 完成定义

本计划完成不是以“文件变多”为标准，而是满足：

1. 阅读文章 CRUD 不需要打开素材、MCP、分组和 AI Format 端点；
2. 阅读 Feed 查询不需要穿过文章写入和每日分组逻辑；
3. 新增一个 Article API 时，开发者能明确选择对应 Router/Service；
4. 旧导入路径和所有 API 契约保持兼容；
5. 结构门禁阻止业务重新堆回 facade；
6. 目标测试、ruff、format、mypy 通过；
7. 没有数据库迁移、前端改造或发布模块耦合。

达到以上条件后，再单独考虑 ArticleDocument、通用 Job Worker、PublishingClient 或 generation 服务拆分；不要把这些议题混进本轮结构整理。
