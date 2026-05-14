# Geo 平台多平台重构计划

> **目标**：从「头条号 Windows 桌面 MVP」迁移到「Linux 服务器 + PlatformDriver 多平台架构」
> **创建**：2026-05-13
> **文档目的**：让任意 AI 子代理都能随时认领任意一个任务并独立完成；任务粒度足够小、上下文足够自洽，可由多个 agent 并行推进

---

## 0. 文档使用方式

### 0.1 状态标记

- 🔲 **TODO** — 未开始，任何 agent 可认领
- 🟡 **IN-PROGRESS** `[@owner]` — 进行中，已被认领
- ✅ **DONE** — 已完成并通过自验
- ⚠️ **BLOCKED** — 上游任务未完成，等待

### 0.2 认领流程

1. 扫一遍 [§4 依赖图](#4-任务依赖图)，挑一个其依赖均已 ✅ 的 TODO 任务
2. 把该任务状态改为 🟡 并标记 `[@your-id]`
3. 阅读该任务卡的「上下文 / 文件 / 验收」三段，无需读其他任务卡
4. 完成后跑「自验」段的命令，全过后改 ✅
5. 若发现上游任务遗漏或自己改动影响下游，在末尾「8. 协调日志」补一条

### 0.3 并行安全规则

每个任务在「修改文件」段声明所触文件。**不同任务不应触同一文件**。若必须共改一文件，序列化在同一 Lane 内执行。本计划已按此原则切分。

---

## 1. 背景与目标

### 1.1 起因

当前 Geo 平台在 Windows 上调用「添加账号授权」(`POST /api/accounts/toutiao/login-session`) 返回 500：

- 服务进入 `_start_remote_account_browser` → `start_remote_browser_session` → `_ensure_linux_runtime()`，在 Windows 上抛 `RuntimeError("Remote browser sessions require a Linux runtime")`
- `RuntimeError` 未被 `server/app/main.py:104` 的 ValueError 全局 handler 捕获 → FastAPI 默认 500
- 触发条件：`.env` 中 `GEO_PUBLISH_REMOTE_BROWSER_ENABLED=true`（为 Docker 部署写的），开发机加载 `.env` 后误走该分支

### 1.2 重新定位

500 只是表象。根本问题：

1. **产品定位变更**：从「Windows 桌面 MVP」转为「Linux 服务器」。launcher.py / geo.spec / PyInstaller / `managed_browser_context` 本地分支等大量代码沦为死路径
2. **多平台扩展前置**：未来要加搜狐 / 网易 / 中关村 / 小红书，当前架构以 toutiao 硬编码为中心：
   - 路由 `/api/accounts/toutiao/*`
   - 服务函数 `login_toutiao` / `start_toutiao_login_session` / `run_toutiao_browser_check`
   - 路径硬编码 `browser_states/toutiao/...`
   - `account_key_from_state_path` 硬找 `"toutiao"` 段
   - Publisher 是单类 `ToutiaoPublisher`，与发布编排逻辑揉在一起

### 1.3 本次目标

| # | 目标 | 说明 |
|---|------|------|
| G1 | 抽出 `PlatformDriver` 协议 + 注册表 | 平台扩展点，新加平台只需新建一个 driver 文件 |
| G2 | 把 ToutiaoPublisher 重构为 ToutiaoDriver | 验证 driver 抽象可用，保留所有现有发布行为 |
| G3 | 通用化 accounts service / routes / 路径解析 | 按 `platform_code` 区分，去除 toutiao 硬编码 |
| G4 | 删除 Windows 桌面 MVP 全部代码 | 代码只伺候 Linux 服务器 |
| G5 | 无交互快速校验路径改 headless Playwright | 不再启 Xvfb 一整套 |
| G6 | 顺带解决 500（重构副产物） | 删了 Windows 守卫后 Windows 上启动会主动失败而非神秘 500 |

### 1.4 不在本次范围

- 搜狐 / 网易 / 中关村 / 小红书 driver 实现（**只搭骨架，不写实现**）
- 数据库 schema 改动（Account / Platform 表保持原结构）
- 任务调度 / 鉴权 / 通知模块不动
- 异步队列 / Celery / 微服务化（仍是同步阻塞 + 全局 semaphore=5）

### 1.5 用户已确认的边界

| 议题 | 决定 |
|------|------|
| Windows 遗留 | **全删**，代码只伺候 Linux；删错只能在 Linux 启动时发现 |
| Driver 抽象粒度 | **只抽口子 + 头条 driver**；其他平台后期一个一个加 |
| 数据迁移 | **重置数据**：清空 accounts 表 + 删 `browser_states/` 目录，重新授权 |
| 无交互校验路径 | **Headless Playwright**，不走 Xvfb |
| 向后兼容 | **不保留**任何 shim / fallback / `@deprecated` 标记 |

---

## 2. 架构对照

### 2.1 当前

```
              ┌────────────────────────────────────────┐
                FastAPI routes (toutiao 硬编码)        
                /api/accounts/toutiao/login            
                /api/accounts/toutiao/login-session    
              └────────────────────────────────────────┘
                                                   
              ┌────────────────────────────────────────┐
                services/accounts.py                   
                login_toutiao / start_toutiao_login_  
                session / run_toutiao_browser_check    
              └────────────────────────────────────────┘
                                                    
              ┌──────────────┐         ┌────────────────────┐
                browser.py             browser_sessions.py 
                managed_browser_       Xvfb+x11vnc+        
                context                websockify          
                Windows + Linux        Linux only          
              └──────────────┘         └────────────────────┘
                                                   
              ┌────────────────────────────────────────┐
                toutiao_publisher.py                   
                ToutiaoPublisher (publisher = driver)  
                if remote_browser_enabled() / else     
                managed_browser_context                
              └────────────────────────────────────────┘
```

问题：

- 平台耦合：accounts / publisher 都直接 import 头条相关
- 路径耦合：state_path 硬编码 toutiao 段
- 双路径耦合：publisher 内 if/else 决定是否走 Xvfb
- Windows 路径已死但守卫仍在，触发误用即 500

### 2.2 目标

```
              ┌────────────────────────────────────────┐
                FastAPI routes (platform_code 参数化)  
                /api/accounts/{platform_code}/login    
                /api/accounts/{platform_code}/login-   
                  session                              
              └────────────────────────────────────────┘
                                                   
              ┌────────────────────────────────────────┐
                services/accounts.py (平台无关)        
                register_account / start_login_session
                check_account / finish_login_session   
                  → 全部接受 platform_code，按其取 driver
              └────────────────────────────────────────┘
                                                   
              ┌────────────────────────────────────────┐
                services/drivers/                      
                  __init__.py  Protocol + 注册表       
                  toutiao.py   ToutiaoDriver           
                  (后期: sohu.py / netease.py / ...)   
              └────────────────────────────────────────┘
                                                   
              ┌────────────────────────────────────────┐
                services/publish_runner.py (通用编排)  
                build_runner(record)                   
                  └─ 启 session → launch context       
                     → driver.publish(...) → 处理      
                       user_input 异常                 
              └────────────────────────────────────────┘
                                                   
              ┌────────────────────────────────────────┐
                services/browser_sessions.py (单一栈) 
                Xvfb + x11vnc + websockify             
                （删平台守卫，注释陈述：本系统部署 Linux）
              └────────────────────────────────────────┘
```

---

## 3. PlatformDriver 协议定义（任务 A1 落地此段）

```python
# server/app/services/drivers/__init__.py
from __future__ import annotations
from pathlib import Path
from typing import Protocol, runtime_checkable

from playwright.sync_api import BrowserContext, Page

from server.app.models import Account, Article


@runtime_checkable
class PlatformDriver(Protocol):
    code: str            # 与 Platform.code 一致，如 "toutiao"
    name: str            # 显示名，如 "头条号"
    home_url: str        # 用于登录态检测的首页 URL
    publish_url: str     # 发布页 URL

    def detect_logged_in(self, *, url: str, title: str, body: str) -> bool:
        """根据当前页面文本判断是否已登录。"""

    def publish(
        self,
        *,
        page: Page,
        context: BrowserContext,
        article: Article,
        account: Account,
        state_path: Path,
        stop_before_publish: bool,
    ) -> "PublishFillResult":
        """填表 + 上传 + 点发布；不负责浏览器生命周期。

        遇到登录失效/扫码/验证码时抛 ToutiaoUserInputRequired
        （后续重命名为 UserInputRequired，但本次保留旧名以减少波及）
        """


_REGISTRY: dict[str, PlatformDriver] = {}


def register(driver: PlatformDriver) -> None:
    if driver.code in _REGISTRY:
        raise ValueError(f"Driver already registered: {driver.code}")
    _REGISTRY[driver.code] = driver


def get_driver(platform_code: str) -> PlatformDriver:
    if platform_code not in _REGISTRY:
        raise ValueError(f"Unknown platform: {platform_code}")
    return _REGISTRY[platform_code]


def all_driver_codes() -> list[str]:
    return sorted(_REGISTRY.keys())
```

注册时机：在 `server/app/main.py:create_app()` 开头 import 一次 `drivers.toutiao`（其模块导入时调用 `register(ToutiaoDriver())`）。

---

## 4. 任务依赖图

```
                  ┌─── A1 ───→ A2 ──┬──→ B1 ─→ B2
                                    │
                                    ├──→ C1 ─→ C2 ─┬─→ E2
                                    │              │
                                    │              └─→ F1
                                    │
                                    └──→ F3
                                    
                                    F4 ← B1
                                    
                  ┌─── D1   (独立)
                  │
                  ├─── D2   (独立)
                  │
                  ├─── D3   (独立)
                  │
                  ├─── G2   (独立)
                  │
                  └─── F2 ← D1
                  
                  D4 ← (B1 ∧ C1 ∧ E2 ∧ F2)
                  G1 ← 最后（所有 lane 落定后）
```

**并行机会：**

- 第一波（A1 之后即可启动）：`A2`
- 第二波（A2 之后即可并行）：`B1` / `C1` / `F3`
- 同时段任何时候可独立做：`D1` / `D2` / `D3` / `G2`
- 第三波（依赖具体上游）：`B2`(后于 B1) / `C2`(后于 C1) / `F4`(后于 B1)
- 第四波：`E1` / `E2` / `F1`
- 收尾：`D4` / `G1`

理想团队配比：3 个 agent 并行最饱和。

---

## 5. 任务清单（汇总表）

| ID | Lane | 标题 | 依赖 | 修改文件主集 | 估算 | 状态 |
|----|------|------|------|--------------|------|------|
| A1 | Driver | PlatformDriver Protocol + 注册表 | — | `services/drivers/__init__.py`(新) | S | 🔲 |
| A2 | Driver | ToutiaoDriver 实现 | A1 | `services/drivers/toutiao.py`(新) | L | 🔲 |
| B1 | Runner | publish_runner.py 通用发布编排 | A2 | `services/publish_runner.py`(新) | M | 🔲 |
| B2 | Runner | tasks.py 接入新 runner | B1 | `services/tasks.py` | S | 🔲 |
| C1 | Accounts | accounts service 通用化 + headless check | A2 | `services/accounts.py` | L | 🔲 |
| C2 | Accounts | accounts routes 路径含 platform_code | C1 | `api/routes/accounts.py` `schemas/account.py` | M | 🔲 |
| D1 | Cleanup | browser_sessions.py 删平台守卫 | — | `services/browser_sessions.py` | S | 🔲 |
| D2 | Cleanup | core/config.py 删冗余配置 | — | `core/config.py` | S | 🔲 |
| D3 | Cleanup | core/paths.py 删 Windows 默认 | — | `core/paths.py` | S | 🔲 |
| D4 | Cleanup | 删 launcher.py / geo.spec / browser.py / toutiao_publisher.py | B1,C1,E2,F2 | （删四个文件） | S | 🔲 |
| E1 | Frontend | web/src/types.ts 类型更新 | C2 | `web/src/types.ts` | S | 🔲 |
| E2 | Frontend | AccountsWorkspace 路径含 platform_code | C2,E1 | `web/src/features/accounts/AccountsWorkspace.tsx` | M | 🔲 |
| F1 | Tests | test_accounts_api.py 更新 | C2 | `server/tests/test_accounts_api.py` | M | 🔲 |
| F2 | Tests | test_browser_sessions.py 删守卫 mock | D1 | `server/tests/test_browser_sessions.py` | S | 🔲 |
| F3 | Tests | 新增 test_drivers.py | A2 | `server/tests/test_drivers.py`(新) | S | 🔲 |
| F4 | Tests | 新增 test_publish_runner.py | B1 | `server/tests/test_publish_runner.py`(新) | M | 🔲 |
| G1 | Docs | 重写 CLAUDE.md | 全部 | `CLAUDE.md` | M | 🔲 |
| G2 | Docs | 清理 .env 模板 | — | `.env` | S | 🔲 |

估算：S ≈ 30 分钟，M ≈ 1 小时，L ≈ 2 小时。

---

## 6. 任务详情卡

> 每张卡按统一模板：上下文 / 修改文件 / 实现要点 / 验收 / 协调注意

### 🔲 A1 — PlatformDriver Protocol + 注册表

**上下文**：本次重构的扩展点。当前没有任何 driver 抽象，所有平台逻辑塞在 `services/toutiao_publisher.py` 的 `ToutiaoPublisher` 类里。

**修改文件**：
- 新建 `server/app/services/drivers/__init__.py`

**实现要点**：照搬 [§3](#3-platformdriver-协议定义任务-a1-落地此段) 中 Protocol 与注册表代码。Protocol 用 `runtime_checkable` 装饰，方便测试用 `isinstance(driver, PlatformDriver)`。

**验收**：
- `from server.app.services.drivers import PlatformDriver, register, get_driver, all_driver_codes` 可 import
- `pytest server/tests/test_drivers.py` 通过（F3 写测试，A1 只需保证 import 不炸）

**协调注意**：本任务**不修改**任何现有文件，纯新增。

---

### 🔲 A2 — ToutiaoDriver 实现

**上下文**：把现有 `ToutiaoPublisher` 类的所有发布逻辑搬到一个实现 `PlatformDriver` 协议的 `ToutiaoDriver` 类。**不改变行为**，只换位置和形状。

**修改文件**：
- 新建 `server/app/services/drivers/toutiao.py`
- 注意：**本任务不删 `toutiao_publisher.py`**（D4 才删）

**实现要点**：
1. 从 `server/app/services/toutiao_publisher.py` 抄过来：
   - 常量：`TOUTIAO_PUBLISH_URL`, `QR_HINTS`, `CAPTCHA_HINTS`, `LOGIN_REDIRECT_HINTS`, `LOGIN_HINTS`, `PUBLISH_HINTS`
   - dataclasses：`PublishFillResult`, `BodySegment`
   - 异常：`ToutiaoPublishError`, `ToutiaoUserInputRequired`（命名保留，跨平台父类后续再抽）
   - 私有函数：`_fill_title`, `_handle_cover`, `_fill_body`, `_wait_publish_images_ready`, `_click_publish_and_wait`, `_ensure_publish_page`, `_close_ai_drawer`, `_dismiss_blocking_popups`, `_do_publish` 等所有发布流程子函数
2. **不要** 抄过来：
   - `ToutiaoPublisher` 类外壳
   - `publish_article` 中 `if remote_browser_enabled()` 分支与浏览器启动逻辑（这部分由 publish_runner 负责）
   - `_register_context_for_cleanup` / `_launched_contexts` / `atexit` 收尾（去掉，本地 fallback 路径要删）
3. 类骨架：
   ```python
   class ToutiaoDriver:
       code = "toutiao"
       name = "头条号"
       home_url = "https://mp.toutiao.com"
       publish_url = TOUTIAO_PUBLISH_URL

       def detect_logged_in(self, *, url: str, title: str, body: str) -> bool:
           haystack = f"{url}\n{title}\n{body}"
           if any(hint in haystack for hint in LOGIN_HINTS):
               return False
           return "mp.toutiao.com" in url and ("profile_v4" in url or "头条号" in title)

       def publish(self, *, page, context, article, account, state_path, stop_before_publish):
           return _do_publish(page, context, article, account, state_path, stop_before_publish)
   ```
4. 模块底部：
   ```python
   from server.app.services.drivers import register
   register(ToutiaoDriver())
   ```

**验收**：
- `pytest server/tests/test_drivers.py -k toutiao` 通过
- 文件内不再有 `if remote_browser_enabled()` 字样
- 不再 import `services.browser` 或 `managed_browser_context`

**协调注意**：
- `ToutiaoUserInputRequired` 与 `ToutiaoPublishError` 在 `services/toutiao_publisher.py` 中是原始定义；本任务**搬过去**后，B1 / C1 等下游都从新位置 import。`toutiao_publisher.py` 暂留作历史，D4 删除。
- 现有 `services/tasks.py` 还 import 自 `toutiao_publisher`，本任务**不动**它，B2 处理。

---

### 🔲 B1 — publish_runner.py 通用发布编排

**上下文**：当前 `ToutiaoPublisher.publish_article` 既做编排（启浏览器 / 启会话 / 处理 user_input keep-alive）又做平台操作。本任务把编排单拎出来，平台操作交给 driver。

**修改文件**：
- 新建 `server/app/services/publish_runner.py`

**实现要点**：

```python
# server/app/services/publish_runner.py
from __future__ import annotations
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from server.app.core.paths import get_data_dir
from server.app.models import Account, Article
from server.app.services.accounts import account_key_from_state_path, launch_options, profile_dir_for_key
from server.app.services.browser_sessions import (
    attach_browser_handles,
    keep_session_alive,
    managed_remote_browser_session,
)
from server.app.services.drivers import get_driver
from server.app.services.drivers.toutiao import ToutiaoPublishError, ToutiaoUserInputRequired, PublishFillResult


def run_publish(
    *,
    article: Article,
    account: Account,
    channel: str = "chromium",
    executable_path: str | None = None,
    stop_before_publish: bool = False,
) -> PublishFillResult:
    """统一发布入口：按 account.platform.code 取 driver，启会话+浏览器，跑 driver.publish。"""
    if not article.title or not article.title.strip():
        raise ToutiaoPublishError("标题不能为空")
    if article.cover_asset is None:
        raise ToutiaoPublishError("封面图片是必填项")

    platform_code, account_key = account_key_from_state_path(account.state_path)
    state_path = (get_data_dir() / account.state_path).resolve()
    if not state_path.exists():
        raise ToutiaoPublishError(f"Account storage state not found: {account.state_path}")

    driver = get_driver(platform_code)

    with managed_remote_browser_session(account_key) as session:
        # session 现在恒非 None（删了 publish_remote_browser_enabled）
        pw = sync_playwright().start()
        options = launch_options(channel, executable_path)
        options["env"] = {**os.environ, "DISPLAY": session.display}
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir_for_key(platform_code, account_key)),
            **options,
        )
        context.set_default_navigation_timeout(30000)
        page = context.new_page()
        attach_browser_handles(session.id, pw, context, page)

        _keep_browser = False
        try:
            return driver.publish(
                page=page, context=context, article=article, account=account,
                state_path=state_path, stop_before_publish=stop_before_publish,
            )
        except ToutiaoUserInputRequired as exc:
            _keep_browser = True
            keep_session_alive(session.id)
            exc.session_id = session.id
            exc.novnc_url = session.novnc_url
            raise
        finally:
            if not _keep_browser:
                try: context.close()
                except Exception: pass
                try: pw.stop()
                except Exception: pass
                attach_browser_handles(session.id, None, None, None)
```

**验收**：
- `pytest server/tests/test_publish_runner.py` 通过（F4 写测试）
- 文件内无 `if remote_browser_enabled() else nullcontext()` 分支
- `from server.app.services.publish_runner import run_publish` 可 import

**协调注意**：
- 依赖 C1 中 `account_key_from_state_path` 返回 `(platform_code, account_key)` 元组、`profile_dir_for_key` 接受 `(platform_code, account_key)` 双参数 → **本任务与 C1 在「函数签名约定」上要先对齐**：约定为返回 `tuple[str, str]`。如果 C1 还没动手，本任务先建分支同时开工，A2 完成后两边按这个约定写。
- 不依赖 `publish_remote_browser_enabled`：删该配置由 D2 完成，B1 之内**直接假设** session 非 None。

---

### 🔲 B2 — tasks.py 接入新 runner

**上下文**：当前 `services/tasks.py` 的 `build_publisher_for_record(record)` 返回一个 `ToutiaoPublisher` 实例，调用方 `.publish_article(article, account, stop_before_publish=...)`。本任务把它替换为一个函数闭包，内部调 `run_publish`。

**修改文件**：
- `server/app/services/tasks.py`

**实现要点**：

1. 找到 `build_publisher_for_record` 函数，替换为：
   ```python
   def build_publish_runner_for_record(record):
       from server.app.services.publish_runner import run_publish
       def _runner(article, account, *, stop_before_publish=False):
           return run_publish(
               article=article,
               account=account,
               stop_before_publish=stop_before_publish,
           )
       return _runner
   ```
2. 更新所有调用点：`build_publisher_for_record(record).publish_article(...)` → `build_publish_runner_for_record(record)(article, account, stop_before_publish=...)`
3. 删除 `services/tasks.py` 顶部 `from server.app.services.toutiao_publisher import ...` 中关于 `ToutiaoPublisher` 的引用；保留 `ToutiaoPublishError` / `ToutiaoUserInputRequired` 但改 import 自 `services.drivers.toutiao`。

**验收**：
- `grep -r build_publisher_for_record server/` 无结果（除注释外）
- `pytest server/tests/test_tasks_api.py server/tests/test_concurrent_publish.py` 通过（这些测试 monkeypatch `build_publisher_for_record` 替换 Playwright；要同步更新它们或者保留 alias 以兼容，**推荐同步更新**，alias = 补丁式）

**协调注意**：现有测试用 `monkeypatch.setattr(tasks, "build_publisher_for_record", lambda r: stub)` 模式，本任务**必须**同步更新 `server/tests/test_tasks_api.py` / `test_concurrent_publish.py` / `test_phase4_*.py` 中所有 `build_publisher_for_record` 的引用。这部分改动包含在 B2 内。

---

### 🔲 C1 — accounts service 通用化 + headless check

**上下文**：`services/accounts.py` 现在以 toutiao 为中心。本任务把它通用化，按 `platform_code` 区分；同时改 `check_account` 走 headless Playwright（不再启 Xvfb）。

**修改文件**：
- `server/app/services/accounts.py`

**实现要点**：

1. **路径与解析通用化**：
   ```python
   def state_dir_for_key(platform_code: str, account_key: str) -> Path:
       return get_data_dir() / "browser_states" / platform_code / account_key

   def state_path_for_key(platform_code: str, account_key: str) -> Path:
       return state_dir_for_key(platform_code, account_key) / "storage_state.json"

   def profile_dir_for_key(platform_code: str, account_key: str) -> Path:
       return state_dir_for_key(platform_code, account_key) / "profile"

   def account_key_from_state_path(state_path: str) -> tuple[str, str]:
       """从 'browser_states/<platform_code>/<account_key>/storage_state.json' 中
       提取 (platform_code, account_key)。"""
       parts = Path(state_path).parts
       try:
           idx = parts.index("browser_states")
           return parts[idx + 1], parts[idx + 2]
       except (ValueError, IndexError):
           raise ValueError(f"Invalid state path: {state_path}") from None
   ```
2. **入口函数通用化**（删 toutiao 字样）：
   - `get_or_create_toutiao_platform(db)` → `get_or_create_platform(db, code: str, name: str, base_url: str)`，对头条仍传 `("toutiao", "头条号", "https://mp.toutiao.com")`
   - `login_toutiao(db, user_id, payload)` → `register_account_from_storage_state(db, user_id, platform_code, payload)`，内部只处理 `use_browser=False` 复用 storage_state 的逻辑
   - `start_toutiao_login_session(db, user_id, payload)` → `start_login_session(db, user_id, platform_code, payload)`，路径用 `state_path_for_key(platform_code, ...)`
   - 删除 `run_toutiao_browser_check`（headless 检查直接在 `check_account` 内联实现，使用 driver）
3. **check_account 改 headless**：
   ```python
   def check_account(db, account: Account, payload: AccountCheckRequest) -> Account:
       from playwright.sync_api import sync_playwright
       from server.app.services.drivers import get_driver

       platform_code, _ = account_key_from_state_path(account.state_path)
       driver = get_driver(platform_code)
       abs_state_path = get_data_dir() / account.state_path

       logged_in = False
       if payload.use_browser and abs_state_path.exists():
           with sync_playwright() as pw:
               browser = pw.chromium.launch(headless=True)
               context = browser.new_context(storage_state=str(abs_state_path))
               page = context.new_page()
               try:
                   page.goto(driver.home_url, wait_until="domcontentloaded", timeout=30000)
                   try:
                       page.wait_for_load_state("networkidle", timeout=8000)
                   except Exception:
                       pass
                   url = page.url
                   title = page.title()
                   try:
                       body = page.locator("body").inner_text(timeout=3000)
                   except Exception:
                       body = ""
                   logged_in = driver.detect_logged_in(url=url, title=title, body=body)
                   context.storage_state(path=str(abs_state_path))
               finally:
                   context.close()
                   browser.close()
       else:
           logged_in = abs_state_path.exists()

       now = utcnow()
       account.status = "valid" if logged_in else "expired"
       account.last_checked_at = now
       account.updated_at = now
       db.flush()
       return get_account(db, account.id) or account
   ```
4. **launch_options**：删 `sys.platform != "win32"` 分支，固定为 Linux args；删 `VALID_EXE_RE` Windows 路径正则。
5. **删除函数**：`run_toutiao_browser_check`、`detect_login_state_text`（行为已搬到 driver.detect_logged_in）
6. **`_start_remote_account_browser`** 重命名为 `_start_login_browser`，接受 `platform_code` 参数，传给 `state_dir_for_key` / `profile_dir_for_key`；不再 `if not remote_browser_enabled(): raise`（D2 删了该配置）
7. **finish/stop login session 系列函数**：把 `account_key_from_state_path` 调用从单返回值改为元组解构

**验收**：
- `grep -in toutiao server/app/services/accounts.py` 只剩极少非业务字面（如默认 display_name）
- `python -c "from server.app.services.accounts import start_login_session, register_account_from_storage_state, check_account"` 不炸
- `pytest server/tests/test_accounts_api.py` 由 F1 同步更新后通过

**协调注意**：
- 与 B1 约定 `account_key_from_state_path` 返回 `tuple[str, str]`、`profile_dir_for_key(platform_code, account_key)`
- 与 C2 约定 schema 字段名（C2 内改 schemas）
- 本任务**不**修改 routes 层（C2 做）
- `_register_context_for_cleanup` / `_launched_contexts` 不再 import（已经在 A2 中删）

---

### 🔲 C2 — accounts routes 路径含 platform_code + schemas 通用化

**上下文**：路由从 `/toutiao/...` 改为 `/{platform_code}/...`；请求体 schema 改名为通用形态。

**修改文件**：
- `server/app/api/routes/accounts.py`
- `server/app/schemas/account.py`

**实现要点**：

1. **schemas/account.py**：
   - `ToutiaoLoginRequest` → `PlatformLoginRequest`，字段不变（display_name / account_key / channel / executable_path / wait_seconds / use_browser / note）
   - `AccountCheckRequest`：删 `wait_seconds`（headless 不需要长等）；保留 channel / executable_path / use_browser
   - `AccountBrowserSessionRead`：增字段 `platform_code: str`（前端展示）

2. **routes/accounts.py**：
   - `POST /toutiao/login` → `POST /{platform_code}/login`
   - `POST /toutiao/login-session` → `POST /{platform_code}/login-session`
   - 端点签名加 `platform_code: str` 路径参数，校验：`if platform_code not in all_driver_codes(): raise HTTPException(404, "Unknown platform")`，调用 service 时传入
   - `_to_browser_session_read` 增 `platform_code` 字段填充
   - 其他端点（check / relogin / login-session/finish / login-session DELETE / DELETE account / PATCH account）不变路径，但内部从 `account.platform.code` 取 platform_code

3. **后端 router 注册**：`server/app/main.py` 中 `app.include_router(accounts_router, prefix="/api/accounts", ...)` 保持不变。

**验收**：
- `curl -X POST /api/accounts/toutiao/login-session -d ...` 行为等价于改前的 `/api/accounts/toutiao/login-session`
- `curl -X POST /api/accounts/sohu/login-session` 返回 404（driver registry 未注册）
- OpenAPI schema 中没有 `ToutiaoLoginRequest`，有 `PlatformLoginRequest`

**协调注意**：
- 前端 E2 依赖本任务确定的路径形态
- 与 F1 共同确认测试 URL

---

### 🔲 D1 — browser_sessions.py 删平台守卫

**上下文**：模块里 `_ensure_linux_runtime` / `_is_windows_runtime` 在 Linux-only 部署下成了死代码且会在开发机误触发 500。删之。

**修改文件**：
- `server/app/services/browser_sessions.py`

**实现要点**：
1. 删函数 `_ensure_linux_runtime`、`_is_windows_runtime`
2. 删 `start_remote_browser_session` 内 `_ensure_linux_runtime()` 调用（首行）
3. 删 import `sys`（如果只为此使用）
4. 模块顶部 docstring：把「Linux 专用，仅用于云端部署」改为「Xvfb + x11vnc + websockify → noVNC 流水线，本系统部署在 Linux 服务器，因此无需平台分支。」
5. 不删 `managed_remote_browser_session` 函数（B1 还用）
6. 删该 contextmanager 内 `if not remote_browser_enabled(): yield None; return`（D2 删了该配置）。改为直接进入会话。

**验收**：
- `grep -n "_ensure_linux_runtime\|_is_windows_runtime\|sys.platform" server/app/services/browser_sessions.py` 无结果
- `pytest server/tests/test_browser_sessions.py` 由 F2 更新后通过

**协调注意**：F2 跟进 monkeypatch 移除。

---

### 🔲 D2 — core/config.py 删冗余配置

**上下文**：`publish_remote_browser_enabled` 在 Linux-only 部署下恒为真，是历史负担。

**修改文件**：
- `server/app/core/config.py`

**实现要点**：
1. 删 `publish_remote_browser_enabled: bool = False` 字段
2. `publish_browser_channel: str = "chromium"`（已为该值，确认）
3. 不删 `publish_xvfb_path / publish_x11vnc_path / publish_websockify_path / publish_novnc_web_dir / publish_remote_browser_host / publish_remote_browser_display_base / publish_remote_browser_vnc_base_port / publish_remote_browser_novnc_base_port / publish_remote_browser_start_timeout_seconds / publish_remote_browser_idle_timeout_seconds`（这些还在用）

**验收**：
- `grep -rn publish_remote_browser_enabled server/ web/` 无结果
- `python -c "from server.app.core.config import get_settings; get_settings()"` 不炸
- 全部测试集仍通过（先在此任务内本地跑一遍 pytest）

**协调注意**：B1 / D1 中已假设此配置不存在；如果先做 D2、B1/D1 后做，B1/D1 任务卡所述「假设 session 非 None」可直接落地。

---

### 🔲 D3 — core/paths.py 删 Windows 默认

**上下文**：`%LOCALAPPDATA%/GeoCollab` 默认路径是 Windows 桌面 MVP 遗物。

**修改文件**：
- `server/app/core/paths.py`

**实现要点**：
1. 找到 `get_data_dir()` 中的默认路径逻辑，保留：从 `Settings().data_dir` 读，若为 None 则抛 `RuntimeError("GEO_DATA_DIR not set")`
2. 删除 `os.environ.get("LOCALAPPDATA")` 相关分支

**验收**：
- `grep -in LOCALAPPDATA server/` 无结果
- 启动时若未设 `GEO_DATA_DIR`，FastAPI 启动失败、消息清晰
- 测试用 utils 会 monkeypatch `GEO_DATA_DIR`，不受影响

**协调注意**：本任务**会破坏**没有 `GEO_DATA_DIR` 的本地开发场景；所有人需要更新本地 `.env` 或 shell（建议在协调日志记一条）。

---

### 🔲 D4 — 删 launcher.py / geo.spec / browser.py / toutiao_publisher.py

**上下文**：四个 Windows 桌面 MVP 遗物。

**修改文件**：删除以下四个文件
- `launcher.py`
- `geo.spec`
- `server/app/services/browser.py`
- `server/app/services/toutiao_publisher.py`

**实现要点**：直接删除，不留 alias。删除前确认：
- `grep -rn "from server.app.services.browser import\|from server.app.services.toutiao_publisher import" server/ web/` 无结果
- `grep -rn "launcher\|geo.spec\|PyInstaller" .` 仅剩 PLAN.md / CLAUDE.md（G1 会清）和历史 git log

**验收**：四个文件不存在；项目可启动。

**协调注意**：必须在 B1（接管 publisher 编排）、C1（删 run_toutiao_browser_check）、E2（前端不再访问任何 windows 概念）、F2（测试不 import browser.py）全部 ✅ 之后执行。

---

### 🔲 E1 — web/src/types.ts 类型更新

**上下文**：与后端 C2 schema 同步。

**修改文件**：
- `web/src/types.ts`

**实现要点**：
1. `AccountLoginPayload` 改名为 `PlatformLoginPayload`（字段同）
2. `AccountBrowserSession` 增字段 `platform_code: string`
3. `Account` 已有 `platform_code` 字段（无需改）

**验收**：`pnpm --filter @geo/web build` 通过

---

### 🔲 E2 — AccountsWorkspace 路径含 platform_code

**上下文**：前端账号工作区现在直接拼 `/api/accounts/toutiao/...`。改为按账号的 `platform_code` 拼路径；「添加授权」目前固定 `toutiao`（未来加平台时这里多一个平台选择器）。

**修改文件**：
- `web/src/features/accounts/AccountsWorkspace.tsx`

**实现要点**：
1. 新增本地常量 `const DEFAULT_PLATFORM_CODE = "toutiao";`（后期变成选择器）
2. `startNewRemoteLogin` 中 POST URL 改 `/api/accounts/${DEFAULT_PLATFORM_CODE}/login-session`
3. `login(useBrowser=false)` 中 POST URL 改 `/api/accounts/${DEFAULT_PLATFORM_CODE}/login`
4. `startExistingRemoteLogin` / `completeLoginSession` / `closeLoginSession` 用 `/api/accounts/${account.id}/login-session...`（这些已经按 account.id 路由，无需动）
5. UI 标题「头条号授权」改为「平台账号授权」；账号卡片用 `account.platform_name` 显示

**验收**：
- 浏览器内手动「添加授权」流程跑通（开发机连 Docker 起的 Linux 后端）
- `pnpm --filter @geo/web build` 通过

---

### 🔲 F1 — test_accounts_api.py 更新

**上下文**：测试用例的 URL / schema 字段 / monkeypatch 需要同步。

**修改文件**：
- `server/tests/test_accounts_api.py`

**实现要点**：
1. 所有 `/api/accounts/toutiao/...` URL 保留（实际值不变，只是参数化）
2. 字段 `ToutiaoLoginRequest` → `PlatformLoginRequest`（仅 import 名）
3. monkeypatch `_start_remote_account_browser` → `_start_login_browser`
4. 新增一个测试：`test_unknown_platform_returns_404`（POST `/api/accounts/sohu/login-session` 返回 404）

**验收**：`pytest server/tests/test_accounts_api.py` 全绿

---

### 🔲 F2 — test_browser_sessions.py 删守卫 mock

**上下文**：测试用 `monkeypatch.setattr(browser_sessions, "_is_windows_runtime", lambda: False)`，D1 删了该函数，监 patch 失败。

**修改文件**：
- `server/tests/test_browser_sessions.py`

**实现要点**：删除 `_is_windows_runtime` monkeypatch；删除 `Settings(publish_remote_browser_enabled=...)` 相关 setup；保留 `_reset_globals()` 调用。

**验收**：`pytest server/tests/test_browser_sessions.py` 全绿。

---

### 🔲 F3 — 新增 test_drivers.py

**上下文**：测 driver 注册表 + ToutiaoDriver 基本属性。

**修改文件**：
- 新建 `server/tests/test_drivers.py`

**实现要点**：

```python
def test_toutiao_driver_registered():
    from server.app.services.drivers import get_driver, all_driver_codes
    import server.app.services.drivers.toutiao  # 触发 register
    assert "toutiao" in all_driver_codes()
    driver = get_driver("toutiao")
    assert driver.code == "toutiao"
    assert driver.name == "头条号"
    assert driver.home_url.startswith("https://mp.toutiao.com")


def test_get_unknown_driver_raises():
    from server.app.services.drivers import get_driver
    import pytest
    with pytest.raises(ValueError):
        get_driver("nonexistent")


def test_detect_logged_in_logic():
    from server.app.services.drivers.toutiao import ToutiaoDriver
    d = ToutiaoDriver()
    assert d.detect_logged_in(url="https://mp.toutiao.com/profile_v4/x", title="x", body="") is True
    assert d.detect_logged_in(url="https://sso.toutiao.com/login", title="登录", body="扫码") is False
```

**验收**：`pytest server/tests/test_drivers.py` 全绿

---

### 🔲 F4 — 新增 test_publish_runner.py

**上下文**：用 stub driver 测发布编排，不真起 Playwright。

**修改文件**：
- 新建 `server/tests/test_publish_runner.py`

**实现要点**：

```python
def test_run_publish_uses_driver_for_account_platform(monkeypatch):
    """run_publish 按 account.state_path 中的 platform_code 取 driver。"""
    # monkeypatch managed_remote_browser_session 返回 stub session
    # monkeypatch sync_playwright 返回 stub
    # 注册一个 stub driver code="testplat"
    # 构造 Account.state_path = "browser_states/testplat/k1/storage_state.json"
    # 调 run_publish，断言 stub_driver.publish 被调用且参数正确
    ...


def test_run_publish_keep_session_on_user_input_required(monkeypatch):
    """driver.publish 抛 ToutiaoUserInputRequired 时 keep_session_alive 被调用，且异常附带 novnc_url。"""
    ...
```

**验收**：`pytest server/tests/test_publish_runner.py` 全绿

---

### 🔲 G1 — 重写 CLAUDE.md

**上下文**：CLAUDE.md 当前以「Windows 桌面 MVP」为叙述主线，本次重构后全部失效。

**修改文件**：
- `CLAUDE.md`

**实现要点**：
1. 「Project Overview」改为：「Geo 协作平台 — Linux 服务器多平台内容自动化发布平台。架构：FastAPI 后端 + React/TypeScript 前端 + Playwright 浏览器自动化 + Xvfb/x11vnc/noVNC 远程人工介入。当前已实现头条号 driver，架构按 PlatformDriver 协议支持快速接入新平台。」
2. 删 PyInstaller / launcher.py / .exe 段；改写「Dev Commands」用 docker-compose
3. 「Architecture」节添加 PlatformDriver 段
4. 「Playwright Automation」节改名「PlatformDriver — Toutiao 实现细节」，内容（byte-btn 等头条 DOM 细节）放到 driver 子节
5. 「Task Execution Model」段确认 publish_runner 是入口
6. 「Testing」段更新（提及 test_drivers / test_publish_runner）

**验收**：CLAUDE.md 不再出现 windows / pyinstaller / .exe / managed_browser_context / launcher / %LOCALAPPDATA% 字样。

---

### 🔲 G2 — 清理 .env 模板

**上下文**：`.env` 现含 Windows 桌面相关注释和 `GEO_PUBLISH_REMOTE_BROWSER_ENABLED`（D2 删该配置）。

**修改文件**：
- `.env`

**实现要点**：
1. 删 `GEO_PUBLISH_REMOTE_BROWSER_ENABLED=true` 行（D2 删了字段）
2. 删 `# 浏览器渠道（默认 chrome，Docker 内可用 chromium）` 注释中的「默认 chrome」字样
3. 加文件头注释：「本模板假设部署在 Linux 服务器（Docker Compose）。本项目不支持 Windows 桌面部署。」

**验收**：`docker-compose up` 启动后 app 服务正常。

---

## 7. 总体验证清单（全部任务 ✅ 后跑）

执行环境：Docker Compose 起的 Linux 容器（不在 Windows 主机）。

1. **后端 import 健康**：`python -c "import server.app.main; print('ok')"` 输出 `ok`
2. **测试全绿**：`pytest server/tests/` 全过
3. **前端构建**：`pnpm --filter @geo/web build` 通过
4. **Driver 注册**：`python -c "from server.app.services.drivers import all_driver_codes; print(all_driver_codes())"` 输出 `['toutiao']`
5. **集成手测**（在 Docker 里跑）：
   - 添加头条号账号 → 拿到 novnc_url → noVNC 中扫码登录 → 「完成登录」→ status=valid
   - 写一篇文章带封面 → 创建发布任务 → 任务执行成功 → 头条号后台可见
6. **404 测试**：`curl -i -X POST http://localhost:8000/api/accounts/sohu/login-session` 返回 404
7. **代码体检**：
   ```bash
   grep -rn "managed_browser_context\|_is_windows_runtime\|_ensure_linux_runtime\|publish_remote_browser_enabled\|LOCALAPPDATA\|launcher\.py\|geo\.spec\|ToutiaoPublisher\b\|run_toutiao_browser_check" server/ web/ CLAUDE.md .env
   ```
   预期：无结果，或仅命中 git 历史/PLAN.md（PLAN.md 可保留为执行记录）

---

## 8. 协调日志

> 任务执行中如发现影响其他任务的事项，在此追加一条（日期 / agent / 内容）。

- *（暂无）*

---

## 9. 风险与回退

| 风险 | 影响 | 应对 |
|------|------|------|
| 删 `publish_remote_browser_enabled` 后，本地无 Xvfb 的开发机跑不了发布器 | 中 | 文档说明开发改 Docker 起；本地纯单测不受影响 |
| `account_key_from_state_path` 改为 tuple 返回值，所有调用点必须同步 | 中 | C1 内全量更新；F1 / F4 测试兜底 |
| 注册表是模块级单例，多 worker 时重复 import OK，但要保证 `register` 幂等或仅首次执行 | 低 | 注册函数遇重复 raise ValueError，A1 已写；测试覆盖 |
| 重置数据库后忘记重新种子 Platform 表 | 低 | Alembic seed 已包含 toutiao platform；G2 / G1 文档强调首次部署需 `alembic upgrade head` |

回退策略：本计划所有任务在独立 commit / branch 上推进，最后一次性合并；如需回退，`git revert` 合并 commit 即可。

---

## 10. 待办（重构后）

- [ ] 接入第二个 driver（搜狐 / 网易 / 中关村 / 小红书任选其一）→ 检验 PlatformDriver 抽象的可扩展性
- [ ] 前端「添加授权」加平台选择器（当前固定 toutiao）
- [ ] 把 `ToutiaoUserInputRequired` / `ToutiaoPublishError` 抽到 `services/drivers/base.py` 改名为 `UserInputRequired` / `PublishError`，driver 共享
- [ ] 重构期老 PLAN.md 内容（项目历史快照）若仍有引用价值，搬到 `docs/HISTORY.md`
