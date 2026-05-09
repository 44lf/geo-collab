# Geo自动发布 上线前审查报告

## 背景与需求

**工具信息：**
- 工具名称：geo自动发布
- 主要用途：保存文章、保持平台多个账号的登录状态，创建发布任务后自动化发布文章
- 使用人群：公司内部产品宣传人员（5人团队）
- 典型使用场景：产品同时新写的文章用这个项目自动发布出去
- 技术栈：Python（FastAPI + Playwright）、React/TypeScript
- 是否联网：是
- 当前最担心的问题：项目像个玩具，给同事使用报错bug不断

**审查维度：** 用户流程 / 稳定性 / 测试可靠性 / 本地exe交付 / 维护成本

---

## A. 高风险问题清单

### P0 — 阻断级（上线前必须修复）

| 问题 | 触发场景 | 影响 | 检查方法 | 修复建议 |
|------|---------|------|---------|---------|
| 删除文章/账号无确认弹窗 | `ContentWorkspace.tsx:496` 删除按钮直接调用 DELETE，紧邻「保存」；`AccountsWorkspace.tsx:261` 删除账号直接调用 remove() | 内容/账号不可恢复；账号删除同时清除浏览器登录状态，全员重新登录 | 点击删除观察是否弹确认 | 两处均加确认 modal，账号删除说明「将清除本地授权状态」 |
| assets.py DB flush 在文件写入前 | 磁盘满、权限不足、反病毒锁文件时写文件失败 | DB 有 Asset 记录但文件不存在，封面引用失效，发布任务失败且错误难以定位 | `assets.py:108-112`：`db.flush()` 在 `path.write_bytes()` 之前 | 先写文件成功再 `db.flush()`；写失败时回滚 |
| Playwright 线程卡死时任务锁永久被占 | Chrome 崩溃、GPU 异常、用户手动关浏览器 | 300s 内任务锁被持有，其他 execute 请求全部返回「already being executed」；任务永久卡住 | `tasks.py:287-293`：`future.result(timeout=300)` 超时前锁不释放 | 超时后 `executor.shutdown(wait=False)`；将 `RECORD_TIMEOUT_SECONDS` 降至 120s |
| Playwright 浏览器未打包进 exe | 用户机器未安装 Chrome | 所有账号登录和文章发布功能全部报 Playwright 底层异常，用户无法自行定位 | `geo.spec:11`：只收集 Python 绑定，不含 Chrome 二进制；`launcher.py:63-75`：Chrome 缺失只 logging.warning | Chrome 缺失时弹 tkinter 对话框提示用户安装并退出，而非静默 warning |
| token 写入 sys._MEIPASS 只读临时目录 | exe 每次重启 | 每次重启生成新 token，旧 token 的前端请求全部 401；`launcher.py:106-108`：`env_path = project_root / '.env'`，打包后 project_root 是 `_MEIxxxxxx` 临时目录 | 重启 exe 两次，检查 launcher.log 中 token 是否变化 | token 写入 `data_dir/local_token.txt`；启动时 `os.environ['GEO_LOCAL_API_TOKEN']` = 读取或生成 |
| 头条号 DOM selector 硬编码散落各处 | 头条号任意前端迭代修改 `.add-icon`/`.close-btn`/`byte-btn` 类名 | 所有新任务发布失败，且不知道是哪个步骤 | `toutiao_publisher.py:90,157,164,170,176,181,192,204,214` 均为硬编码；`_fill_body:118-144` 用 bounding_box height<80 猜测编辑器 | 将所有 selector 提取到顶部常量 dict；每个步骤加结构化日志 |
| stop_before_publish 流程完全无测试 | 前端勾选「停在预览」后执行任务，状态机走 `waiting_manual_publish` | `manual_confirm_record()` 是核心人工审核入口，状态机 bug 无法自动发现 | `grep 'manual.confirm\|waiting_manual' server/tests/` → 0 结果 | 补充：stop_before_publish=True 后 record 状态断言；manual-confirm 推进下一条；非法状态返回 400 |
| recover_stuck_records 完全无测试 | 进程在 record=running 时崩溃重启 | 恢复逻辑 bug（如时区比较错误）导致任务永久卡死，用户无感知 | `grep 'recover_stuck' server/tests/` → 0 结果 | 插入 lease_until=过去的 running record，调用函数，断言变为 pending |

### P1 — 高风险（上线后首批用户会踩到）

| 问题 | 触发场景 | 影响 | 检查方法 | 修复建议 |
|------|---------|------|---------|---------|
| 新建文章按钮静默丢弃未保存内容 | 编辑中误点「新建」 | `ContentWorkspace.tsx:234-239`：resetDraft() 无脏检测直接清空，内容无法找回 | 编辑一段文字后点「新建」 | 检测 draft 有未保存内容时弹确认 |
| 单篇发布多账号前端不阻断 | 用户在 single 模式勾选多个账号后提交 | 后端 `tasks.py:485` 抛 ValueError，表单不重置，用户不知道改什么 | `TasksWorkspace.tsx:311-316`：允许勾选多个 | single 模式改为单选 radio，或勾选第二个时立即提示 |
| 正文为空时无前置校验，发布流程走完但内容为空 | 文章正文为空字符串 | execCommand 插入空字符串，头条号校验拦截但 Playwright 无感知，record 被标记 succeeded | `toutiao_publisher.py:71`，_fill_body 无内容校验 | publish_article 入口校验正文非空；_fill_body 输入后验证 inner_text 非空 |
| launcher.py token 明文写入日志 + .env 追加不替换 | 每次重启 | `.env` 膨胀；token 泄漏到 `launcher.log`（若 data_dir 同步云盘） | `launcher.py:107-109` | 写入前检查已有则替换；删除日志中的 token 打印 |
| cancel 与 Playwright 线程竞态，可能重复发布 | 取消后 300s 内 Playwright 完成执行 | record 最终状态不可预测（cancelled 被 succeeded 覆盖）；更严重：取消后浏览器仍在操作账号 | `tasks.py:226,316-331`：两个 Session 在 running→cancelled/succeeded 上竞争 | cancel_task 改用条件 UPDATE `WHERE status='running'`，原子操作 |
| _task_locks 在 finally 中 pop 后有竞态窗口 | 用户快速双击「执行」 | 两个线程同时通过锁检查，可能并发操作同一账号重复发布 | `tasks.py:228`：finally 中 `_task_locks.pop` | 改为 `defaultdict(Lock)` 永久保留 lock，不 pop |
| _ensure_publish_page inner_text timeout 仅 3s | 慢网络 | 页面加载慢时超时，报 playwright TimeoutError，无业务含义 | `toutiao_publisher.py:244` | 提升至 10000ms |
| console=True 生产交付弹黑色命令行窗口 | 用户运行 exe | 误关窗口直接杀死后端进程 | `geo.spec:104` 注释已提示改为 False | 交付前改 `console=False` |
| Alembic 迁移 env.py import 打包后可能失败 | 首次启动 | `from server.app.core.paths import ...` 因 sys.path 缺失 _MEIPASS 崩溃，程序无法启动 | `launcher.py:91`；查看首次启动 launcher.log | `_run_migrations()` 前加 `sys.path.insert(0, str(project_root))` |
| FTS5 trigram tokenizer 依赖 SQLite 版本 | 旧版 SQLite 的用户机器 | migration 0003 建表失败，DB 初始化崩溃，程序白屏无法启动 | `alembic/versions/0003_fts5_indexes.py:28-30` | migration 中加 try/except，失败时 fallback 到无 tokenize 参数的 fts5 |
| execCommand('insertText') 为废弃 API | Chrome 未来版本移除 | 正文为空的文章被发布，无明确报错 | `toutiao_publisher.py:126,141` | 改用 `page.keyboard.type()` 作为首选；加输入后验证 |
| 账号导入（/accounts/import）完全无测试 | ZIP 格式错误、路径穿越、超大文件 | 安全校验逻辑无验证，任何回归不可见 | `grep '/accounts/import' server/tests/` → 0 结果 | 补充正常导入、超大 ZIP（413）、路径穿越（400）、无效 ZIP 测试 |

---

## B. 高 ROI 优化清单

### 立即做（成本低、收益高）

| 优化项 | 解决的问题 | 用户收益 | 开发成本 |
|-------|---------|---------|---------|
| 发布前置校验（标题/正文/封面） | Playwright 启动后才失败；封面缺失无预警 | 任务秒失败+清晰提示，节省 5-10s 等待 | 低 |
| token 写到 data_dir，不追加 .env | 每次重启 token 失效导致 401 | 重启 exe 后无需刷新页面 | 低 |
| browser.py 改用 `context.new_page()` | Chrome profile 崩溃恢复 tab 干扰自动化 | 消除「偶发发布页面检测失败」 | 低 |
| assets.py flush 顺序修复 | 文件写失败后 DB 孤儿记录 | 避免封面资源引用丢失 | 低 |
| geo.spec 改 `upx=False` + `console=False` | UPX 在中文路径崩溃；黑色窗口被误关 | 任意路径稳定启动；纯 GUI 无控制台 | 低 |
| Playwright 步骤结构化日志 | 失败时只有异常消息，不知道哪个步骤 | 运营人员自行判断「封面超时」还是「DOM 变了」 | 低 |
| Selector 集中到常量文件 | 头条号改版时需逐个方法查找 selector | 改版时只改一处 | 低 |
| recover_stuck_records 异常改为日志记录 | 恢复静默失败，用户无感知 | 出问题有日志可查 | 低 |
| FTS 查询降级兜底 | SQLite 版本不支持 FTS5 时搜索返回 500 | 搜索降级可用，不影响核心发布 | 低 |
| Chrome 缺失时弹 tkinter 提示 | Chrome 未安装时报 Playwright 堆栈 | 用户明确知道需要装 Chrome | 低 |
| 任务创建后自动执行（合并两步） | 创建→选中→点执行共 3 次操作 | 创建即发布，符合心智模型 | 低 |
| 记住上次选择的账号 | 每次创建任务重新勾选账号 | 常规发布 0 步骤选账号 | 低 |
| 账号状态 badge 中文化（valid→有效） | 英文状态非技术用户难以理解 | 降低使用门槛 | 低 |
| 分组预览自动触发（去掉手动按钮） | 用户忘记点「预览分配」直接创建 | 减少一次操作，避免盲目创建 | 低 |
| 文章列表封面缺失标注 | 无封面文章选入任务后执行阶段才失败 | 任务创建时阻止选入无封面文章 | 低 |
| stop_before_publish + manual-confirm 测试 | 人工审核入口完全无测试 | 防止状态机 bug 导致任务卡住 | 低 |
| recover_stuck_records 单元测试 | 进程崩溃恢复逻辑从未验证 | 保证 exe 崩溃重启后自动恢复 | 低 |
| client_request_id 幂等性测试 | 弱网重试可能创建重复任务 | 确保重试不产生重复发布 | 低 |
| record 执行超时测试 | FutureTimeoutError 分支从未验证 | 超时后 record 正确 failed，不卡 running | 低 |
| published_count 聚合 SQL 测试 | articles.py 聚合 SQL 从未测试 | 确保文章列表发布次数准确 | 低 |

### 暂不做（5人团队规模不值得）

| 优化项 | 原因 |
|-------|------|
| saveArticle 成功后增量更新 state | 当前文章量小，体感不明显 |
| 任务列表轮询只调 /status 接口 | 100 条以内问题不明显 |
| 等待确认状态显示截图和账号名 | 需关联 screenshot_asset_id 渲染，工作量中等 |
| cancel_task 改条件 UPDATE 消除竞态 | 5人低频使用，实际触发概率极低 |
| 废弃 ArticleBodyAsset 表 | 5人团队暂无图片清理需求 |
| 添加版本号文件和 CHANGELOG | 重要但不阻断功能，第二版前补 |

---

## C. 测试用例清单

### 正常流程测试
- 创建文章 → 保存 → GET /api/articles 包含新文章，`published_count==0`
- single 任务：创建 → 执行 → record.status=='succeeded'，`published_count==1`
- group_round_robin：10篇文章 × 3账号 = 30条 record 全部 succeeded
- `stop_before_publish=True` → record.status=='waiting_manual_publish'
- manual-confirm outcome=succeeded → record.status=='succeeded'，任务推进下一条
- manual-confirm outcome=failed → record.status=='failed'，任务继续下一条
- 任务所有 record succeeded → task.status=='succeeded'
- 任务部分 record failed → task.status=='partial_failed'
- retry failed record → 新 record.retry_of_record_id 正确引用原记录
- GET /api/tasks/{id}/logs after_id 增量拉取返回正确顺序
- PATCH /api/accounts/{id} 修改 display_name，返回新名称
- POST /api/accounts/export → ZIP 含 manifest + storage_state

### 异常输入测试
- single 任务提交 2 个账号 → 400 'Single task requires exactly one account'
- group_round_robin 传空分组 → 400 'Article group has no articles'
- group_round_robin 缺 group_id → 400
- single 任务缺 article_id → 400
- manual-confirm 对非 waiting_manual_publish record → 400
- retry 对非 failed record → 400
- execute 已是 terminal 状态的任务 → 400
- execute 不存在的 task_id → 404
- 正文为空字符串的文章发布 → ToutiaoPublishError，record.failed，error_message 含「正文」
- 封面为 None 的文章发布 → ToutiaoPublishError，record.failed，error_message 含「封面」

### 文件路径测试
- `recover_stuck_records()`：lease_until 已过期的 running record → 变为 pending
- `recover_stuck_records()`：lease_until 未过期的 running record → 不变
- storage_state.json 文件被删除后 execute → record.failed 含 'storage state not found'
- cover asset 文件从磁盘删除后 execute → record.failed
- data_dir 路径含中文/空格时 assets 上传和读取正常

### 权限测试
- POST /api/accounts/import 上传非 ZIP 文件 → 400
- POST /api/accounts/import ZIP 含路径穿越条目（`../../etc/passwd`）→ 400
- POST /api/accounts/import ZIP 超过 MAX_ZIP_BYTES → 413
- DELETE /api/articles/{id} 在文章有 pending publish_record 时行为验证
- 非管理员账户运行 exe，写入 `%LOCALAPPDATA%` 无需 UAC 提权

### 大文件/批量测试
- group_round_robin 首条 failed，其余继续 → partial_failed
- group_round_robin 所有 record failed → task.status=='failed'
- POST /api/tasks 含 20 个账号的 accounts 数组（压测边界）
- POST /api/assets 上传 20MB 图片（接近上限）
- POST /api/accounts/import 上传合法大 ZIP（接近 MAX_ZIP_BYTES）

### 输出结果测试
- 任务执行成功后：finished_at 非空、started_at 非空、task.status=='succeeded'
- GET /api/articles published_count 在 succeeded record 后正确递增
- TaskLog created_at 按时间升序，after_id 增量拉取不重复、不遗漏
- retry record 的 retry_of_record_id 指向原 record

### 打包安装测试
- 全新无 Chrome 的 Windows 机器：运行 exe → 弹出友好提示，不是 Playwright 堆栈
- 路径含中文（`C:\用户\张三\Desktop\`）运行 exe → 正常启动
- 路径含空格（`C:\test user\app\`）运行 exe → 正常启动
- 首次启动后查看 `%LOCALAPPDATA%\GeoCollab\launcher.log` → 'DB migrations complete' 无 ERROR
- 重启 exe 两次 → GEO_LOCAL_API_TOKEN 不变
- `alembic upgrade head` 在全新 SQLite 上正常建表（所有 5 个迁移顺序执行）

### 回归测试
- cancel pending 任务 → 所有 pending record 变 cancelled，任务不可再执行
- execute 后立即 cancel → 之后 task 状态稳定（不出现 running/cancelled 混合）
- 同一 client_request_id 连续两次 POST /api/tasks → 返回同一 task id
- 同一 client_request_id 连续两次 POST /api/articles → 返回同一 article id
- FTS 搜索中文三元组能正确命中；搜索无匹配返回空数组而非 500
- GET /api/tasks/{id}/logs 不存在的 task_id → 404
- 取消已是 cancelled 状态的任务 → 返回原任务不报错

---

## D. 上线前最低检查清单

```
交付物检查
[ ] geo.spec console=False，重新打包确认无黑色命令行窗口
[ ] geo.spec upx=False，在含中文/空格路径下运行 exe 确认启动正常
[ ] token 持久化到 data_dir（重启 exe 两次，launcher.log 中 token 不变）
[ ] launcher.log 中无 token 明文打印（grep 'token' launcher.log）
[ ] web/dist 已构建（pnpm --filter @geo/web build）再打包

环境兼容性检查
[ ] 在无 Chrome 的 Windows 机器运行 exe → 弹出友好提示，不是 Playwright 堆栈
[ ] 在 %TEMP% 指向中文目录的机器上运行 exe → 启动不崩溃
[ ] 验证 FTS5 trigram 可用：
    python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute(\"CREATE VIRTUAL TABLE t USING fts5(a, tokenize='trigram')\")"
[ ] 首次启动后查看 %LOCALAPPDATA%\GeoCollab\launcher.log → 'DB migrations complete' 无 ERROR
[ ] 以非管理员账户运行 exe → 写入 %LOCALAPPDATA% 无需 UAC 提权

核心功能验证
[ ] 删除文章弹确认弹窗
[ ] 删除账号弹确认弹窗（提示将清除授权状态）
[ ] 新建文章时若有未保存内容弹确认
[ ] 无封面的文章在任务创建时有警告，或发布前校验失败有清晰错误
[ ] 正文为空的文章发布时 record.failed，error_message 可读
[ ] single 任务只能选一个账号（前端阻断）

稳定性验证
[ ] assets.py 确认 db.flush() 在 path.write_bytes() 之后
[ ] launcher.py .env 写入逻辑改为替换而非追加
[ ] browser.py 确认使用 context.new_page() 而非 context.pages[0]
[ ] main.py recover_stuck_records 异常改为 logging.exception

测试通过
[ ] pytest server/tests/ 全部通过
[ ] stop_before_publish + manual-confirm 流程测试通过
[ ] 账号导入安全校验测试通过（路径穿越、超大 ZIP）
```

---

## E. 不建议现在做的事情

| 事项 | 原因 |
|------|------|
| ArticleBodyAsset 表的数据同步维护 | 5人团队无图片清理需求；数据已与 content_json 不同步；维护成本大于价值 |
| client_request_id 后端幂等机制 | 内部工具重试场景极少；前端加 disabled 按钮即可；后端 IntegrityError 分支复杂，出 bug 难追踪 |
| 任务分配预览接口 POST /api/tasks/preview | 前端可本地计算轮询映射展示，去掉减少维护路径 |
| Account.note 字段 | 前端无任何 UI 展示或编辑，5人团队口头沟通足够 |
| Article.status 三态（draft/ready/archived）| 无自动流转逻辑，全靠手动维护；对5人团队维护成本大于收益 |
| recover_stuck_records 续租心跳机制 | 5人团队崩溃率极低，重启手动重试即可；半成品心跳比没有更危险 |
| 任务日志历史分页加载（before_id） | 5人团队每次任务记录数不多，limit=100 已足够 |
| SaaS 化、多租户、权限系统 | 明确不在范围内 |
| 自动升级机制 | 发新 exe 发群里即可 |
| 操作审计日志 | 内部工具，无合规要求 |

---

## 最高优先修复路径

按顺序执行，每项均为独立可合并的改动：

1. `launcher.py` — token 持久化到 data_dir + 移除明文打印（P0，约 10 行）
2. `geo.spec` — `console=False` + `upx=False`（P0，2 行）
3. `assets.py` — flush 顺序修复：先 write_bytes 再 flush（P0，1 行）
4. `browser.py` — 改用 `context.new_page()`（P0，1 行）
5. 前端三处删除操作加确认弹窗：文章、账号、分组（P0，UI 改动）
6. `toutiao_publisher.py` — selector 提取到常量 + 步骤日志 + 发布前置校验（P0/P1，核心稳定性）
7. 补充两个核心测试：stop_before_publish + recover_stuck_records（P0，约 90 行）
8. Chrome 缺失友好提示 + FTS5 降级兜底（P1，交付兼容性）

---

## F. Codex 复核补充

本节为对 Claude 审查结果的二次核对补充。结论：Claude 报告的大方向正确，但有少数表述偏重，同时遗漏了几个会直接影响交付和数据可靠性的高风险点。

### 新增高风险遗漏

| 问题 | 触发场景 | 影响 | 证据 | 修复建议 |
|------|---------|------|------|---------|
| 账号导出/导入格式不兼容 | 用户导出授权包后，在另一台机器导入 | 自己导出的授权包会被导入路由拒绝，账号迁移功能不可用 | 导出写 `manifest.json`、`accounts/.../account.json`、`accounts/.../storage_state.json`；导入预校验只允许 `browser_states/toutiao/.../storage_state.json` | 统一 ZIP schema：建议导入接受当前导出格式，并补充导出→导入闭环测试 |
| 前端账号导入/导出未带 token | exe 启动后设置 `GEO_LOCAL_API_TOKEN` | 导入/导出按钮返回 401，用户以为功能坏了 | `AccountsWorkspace.tsx` 中 import/export 使用裸 `fetch`；后端 `/api/accounts` 路由依赖 `require_local_token` | 改用统一 `api()`/`withAuthHeaders()`，文件下载场景手动加 `X-Geo-Token` |
| manual-confirm 在 HTTP 请求中继续跑 Playwright，且不拿任务锁 | 人工确认一条记录后还有下一条 pending record | 请求阻塞很久；同时点执行可能并发发布同一任务 | `manual_confirm_record()` 直接调用 `_run_next_pending_record()`，没有走 `execute_task()` 的 `_task_locks` | manual-confirm 只推进状态并返回；后续执行通过后台队列/统一执行入口触发，或复用同一任务锁 |
| 后台发布线程普通异常不落库为 failed | publisher 构建、截图保存、result 访问等抛非 `ToutiaoPublishError` 异常 | record 保持 running，需等 lease 过期/重启恢复；用户看到任务卡住 | `_run_next_pending_record()` 只捕获 `FutureTimeoutError` 和 `ToutiaoPublishError` | 增加 broad exception 兜底：记录 failed、写 TaskLog、清 lease；同时保留 traceback 到日志 |
| 删除文章/账号会删除历史发布记录和日志 | 用户删除旧文章或账号 | 发布历史和审计记录被抹掉；任务聚合状态可能失真 | `delete_article()` / `delete_account()` 直接删除 `PublishRecord` 和 `TaskLog` | 上线前至少阻止删除被 pending/running 任务引用的文章/账号；历史记录建议保留或软删除 |
| execute 终态任务 API 先返回 queued，异常只进后台日志 | 用户对 succeeded/failed/cancelled 任务点执行 | 前端显示已排队但实际不会执行，反馈不一致 | `POST /api/tasks/{id}/execute` 直接返回 202；终态校验在后台线程内抛错 | 路由层同步校验终态和 running 状态；不可执行时直接返回 400 |
| 前端切换导航会卸载工作区并丢状态 | 编辑文章/创建任务中切到别的模块 | 未保存内容、任务表单、账号表单直接丢失 | `App` 对工作区使用 `key={activeNav}` 强制 remount | 去掉强制 remount；对文章编辑增加 dirty guard |

### 原报告需修正的表述

| 原表述 | 复核结论 | 建议修正 |
|------|---------|---------|
| `assets.py` 先 flush 会导致 DB 有 Asset 但文件不存在 | 表述偏重。请求异常会 rollback；更真实的风险是顺序不理想、异常路径难测，以及文件写入成功但 DB 提交失败会留下孤儿文件 | 保留为 P1 稳定性问题，不必定性为必现 DB 孤儿记录 |
| cancel 后 `cancelled` 可能被 `succeeded` 覆盖 | DB 覆盖风险较低，因为成功更新带 `WHERE status='running'` 条件；但浏览器线程仍可能继续外部发布 | 改为“取消无法中止 Playwright 外部动作，可能取消后仍发布到头条” |
| Alembic 打包后 sys.path 必然失败 | 证据不足。`script_location` 是绝对路径，PyInstaller 通常能设置 bundle import path | 降级为实机验证项；可顺手把 `prepend_sys_path` 改为 `project_root` |
| 删除账号会清除浏览器登录状态 | 当前后端只删 DB 记录和关联发布记录/日志，没有删除 storage_state 文件 | 改为“删除账号会丢失应用内账号记录和发布历史；是否清理授权文件需明确产品语义” |
| 账号导入完全没有安全校验 | 不准确。已有 ZIP 大小、条目数、路径、单条大小、BadZip 校验 | 改为“安全校验有但无测试，且校验 schema 与导出 schema 冲突” |

### 调整后的最高优先级建议

1. 先修交付启动：token 持久化到 data_dir、移除 token 日志、`geo.spec` 设置 `console=False`/`upx=False`、Chrome 缺失弹窗提示。
2. 再修发布执行稳定性：ThreadPoolExecutor 超时不等待卡死线程、普通异常落库 failed、任务锁不 pop、manual-confirm 复用统一执行锁。
3. 再修不可逆数据操作：文章/账号/分组删除确认；阻止删除 running/pending 引用；明确是否软删除历史发布记录。
4. 再修账号迁移闭环：统一导出/导入 ZIP schema，前端 import/export 带 token，补导出→导入测试。
5. 再修发布前校验：标题、正文、封面、账号状态在创建任务或执行前给清晰错误；single 模式前端限制单账号。
6. 最后补测试网：manual-confirm、recover_stuck_records、accounts import/export、安全 ZIP、FTS fallback、timeout/异常兜底。
