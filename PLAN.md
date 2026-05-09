# Geo 修复执行计划

目标：把 `REVIEW.md` 中确认的高风险问题拆成多个可由 subagent 独立执行的任务包。每个任务包尽量缩小文件范围，避免并行修改同一文件，修复必须可测试、可回滚。

限制：
- 不引入复杂权限系统。
- 不 SaaS 化，不做多租户。
- 不做大规模重构。
- 不改动无关功能。
- 不为了“更优雅”扩大范围。
- 每个任务只修明确问题，并补对应测试。

## 总体执行策略

建议按批次执行，不建议所有任务同时开跑：

1. 先执行 P0-A、P0-B。它们分别处理 exe 启动交付和任务执行状态机，风险最高。
2. 再执行 P0C-D、P0C-FE。它们分别处理后端删除保护和前端删除/表单保护，文件范围不同，可以并行。
3. 再执行 P1-A。修账号导入导出闭环和 token 请求。
4. 再执行 P1-PUB、P1-UX。分别处理发布前置校验和前端任务体验，注意它们都可能影响任务创建体验，建议串行验收。
5. 最后执行 P1-T。补迁移/FTS/聚合等剩余测试网。

最终统一验证命令：

```powershell
conda activate geo_xzpt
pytest server/tests/ -v
pnpm install
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web build
```

打包验证命令：

```powershell
conda activate geo_xzpt
pnpm --filter @geo/web build
pyinstaller geo.spec --noconfirm
```

## Subagent 任务列表

### P0-A：交付启动稳定性

职责：
- 修复 exe 启动 token 每次变化、写 `_MEIPASS/.env`、token 明文日志的问题。
- 调整 PyInstaller 交付参数，避免黑色控制台和 UPX 风险。
- Chrome 缺失时给用户明确提示，而不是只写 warning。
- 不改变 API 鉴权模型，不引入权限系统。

允许修改的文件：
- `launcher.py`
- `geo.spec`
- `server/tests/test_launcher_startup.py`（新建）

禁止修改的文件：
- `server/app/main.py`
- `server/app/core/security.py`
- `server/app/api/**`
- `web/**`
- 数据库模型和 Alembic 迁移文件

需要补充或更新的测试：
- 新增 `server/tests/test_launcher_startup.py`。
- 覆盖 token 文件读取/生成逻辑：同一 data_dir 重启两次 token 不变。
- 覆盖日志不包含 token 明文。
- 覆盖 Chrome 缺失提示函数可被调用，避免测试真实弹窗。

验收标准：
- token 写入 `%LOCALAPPDATA%/GeoCollab` 对应 data_dir 下的持久文件，不写入 `_MEIPASS/.env`。
- `launcher.log` 不出现完整 token。
- `geo.spec` 中 `console=False`、`upx=False`。
- Chrome 缺失时用户能看到明确中文提示；测试环境不弹真实窗口。
- `pytest server/tests/test_launcher_startup.py -v` 通过。

可能冲突点：
- 如果其他任务也改 `launcher.py` 或 `geo.spec`，必须串行合并。
- 不要顺手改 Alembic 启动路径，除非只改 `launcher.py` 且有测试覆盖。

### P0-B：发布执行状态机和线程稳定性

职责：
- 修复 Playwright 线程超时后 `ThreadPoolExecutor` 退出仍等待卡死线程的问题。
- 修复 `_task_locks` release 后 pop 的竞态。
- 修复普通异常不落库，record 长时间卡 `running` 的问题。
- 修复 `manual_confirm_record()` 在 HTTP 请求中直接继续跑 Playwright 且不拿任务锁的问题。
- 修复 execute 终态任务仍返回 queued 的错误反馈。

允许修改的文件：
- `server/app/services/tasks.py`
- `server/app/api/routes/tasks.py`
- `server/app/api/routes/publish_records.py`
- `server/tests/test_tasks_state_machine.py`（新建）

禁止修改的文件：
- `server/app/services/toutiao_publisher.py`
- `server/app/services/accounts.py`
- `server/app/services/articles.py`
- `web/**`
- `launcher.py`
- `geo.spec`

需要补充或更新的测试：
- 新增 `server/tests/test_tasks_state_machine.py`。
- 覆盖 `stop_before_publish=True` 后 record 进入 `waiting_manual_publish`。
- 覆盖 manual-confirm succeeded/failed 后任务推进逻辑正确，且不会在 HTTP 请求中直接跑下一条长耗时发布。
- 覆盖普通异常会把 record 标记 failed、清 `lease_until`、写 TaskLog。
- 覆盖终态任务 execute 返回 400，不返回 queued。
- 覆盖同一 task 并发 execute 只有一个执行路径生效。

验收标准：
- Playwright record 超时后主执行路径能返回并落库 failed，不因 executor shutdown 等待而卡住。
- `_task_locks` 不再因 pop 产生新旧 lock 并存窗口。
- 非预期异常不会让 record 永久停在 `running`。
- manual-confirm 不绕过任务锁，不阻塞请求执行下一条 Playwright。
- `pytest server/tests/test_tasks_state_machine.py -v` 通过。
- 现有 `pytest server/tests/test_tasks_api.py -v` 通过。

可能冲突点：
- 本包独占 `server/app/services/tasks.py`，其他任务不得同时修改。
- `publish_records.py` 只允许做 manual-confirm 调用方式的最小调整，不扩展 API 语义。

### P0C-D：不可逆删除保护，后端

职责：
- 后端阻止删除正在被 pending/running/waiting_manual_publish 发布记录引用的文章或账号。
- 避免删除文章/账号时无条件抹掉任务历史和日志。
- 保持实现简单，不引入软删除系统，除非只做最小字段/逻辑且有测试。

允许修改的文件：
- `server/app/services/articles.py`
- `server/app/services/accounts.py`
- `server/tests/test_delete_guards.py`（新建）

禁止修改的文件：
- `server/app/api/routes/articles.py`
- `server/app/api/routes/accounts.py`
- `server/app/services/tasks.py`
- `server/app/models/**`
- `web/**`

需要补充或更新的测试：
- 新增 `server/tests/test_delete_guards.py`。
- 删除被 pending record 引用的文章返回 400。
- 删除被 running record 引用的文章返回 400。
- 删除被 waiting_manual_publish record 引用的账号返回 400。
- 删除无任务引用的文章/账号仍可成功。
- 历史 succeeded/failed 记录不应被无声删除；如果保留失败受外键限制，则测试应明确当前产品选择。

验收标准：
- 用户不能删除正在参与未完成任务的文章/账号。
- 删除操作不会静默破坏未完成任务。
- 错误信息能说明原因，如“存在未完成发布记录”。
- `pytest server/tests/test_delete_guards.py -v` 通过。

可能冲突点：
- 若 P0C-FE 同时改前端删除确认，不冲突。
- 不要改路由层，保持后端保护在 service 层，复用全局 `ValueError -> 400`。

### P0C-FE：不可逆删除保护，前端

职责：
- 文章、账号、分组删除前增加确认。
- 新建文章、切换文章、切换导航前增加未保存内容保护。
- 不重做设计系统，不引入复杂 modal 框架；优先使用现有 modal 样式或浏览器确认。

允许修改的文件：
- `web/src/features/content/ContentWorkspace.tsx`
- `web/src/features/accounts/AccountsWorkspace.tsx`
- `web/src/App.tsx`
- `web/src/styles.css`（仅当必须补少量现有 modal 样式）

禁止修改的文件：
- `server/**`
- `web/src/features/tasks/TasksWorkspace.tsx`
- `web/src/api/client.ts`
- `web/src/types.ts`

需要补充或更新的测试：
- 当前项目无前端测试框架，本包至少必须通过 typecheck。
- 如果引入轻量测试，测试文件必须新建在 `web/src/**/__tests__`，不得改构建配置。
- 手工验收步骤写入 PR/提交说明：删除文章、删除账号、删除分组均出现确认；编辑未保存内容后新建/切页有拦截。

验收标准：
- 删除文章、账号、分组前均有明确确认文案。
- 账号删除文案不得声称会删除 storage_state，除非后端真的实现了文件清理。
- 未保存文章内容不会因误点“新建”或导航直接丢失。
- `pnpm --filter @geo/web typecheck` 通过。

可能冲突点：
- `ContentWorkspace.tsx` 已较大，禁止顺手重构组件。
- P1-UX 会改 `TasksWorkspace.tsx`，与本包不冲突。

### P1-A：账号导入导出闭环

职责：
- 统一账号导出和导入 ZIP schema，使“导出包可以被导入”。
- 保留现有安全校验：总大小、条目数量、路径白名单、单条大小、BadZip。
- 修复前端 import/export 未带 token 的问题。

允许修改的文件：
- `server/app/api/routes/accounts.py`
- `server/app/services/accounts.py`
- `web/src/api/client.ts`
- `web/src/features/accounts/AccountsWorkspace.tsx`
- `server/tests/test_accounts_import_export.py`（新建）

禁止修改的文件：
- `server/app/models/**`
- `server/app/services/tasks.py`
- `server/app/services/articles.py`
- `launcher.py`
- `geo.spec`
- `web/src/features/content/**`
- `web/src/features/tasks/**`

需要补充或更新的测试：
- 新增 `server/tests/test_accounts_import_export.py`。
- 覆盖导出一个账号后，清空/新建测试 app 能导入该 ZIP。
- 覆盖非 ZIP 返回 400。
- 覆盖路径穿越返回 400。
- 覆盖超过 `MAX_ZIP_BYTES` 返回 413。
- 覆盖超大单条 storage_state 返回 400。

验收标准：
- 当前导出的 ZIP 能被当前导入接口接受。
- 导入后账号 display_name、state_path、status 基本字段正确。
- 前端导入/导出请求带 token，生产 token 开启时不 401。
- `pytest server/tests/test_accounts_import_export.py -v` 通过。
- `pnpm --filter @geo/web typecheck` 通过。

可能冲突点：
- 本包会改 `AccountsWorkspace.tsx`，不要与 P0C-FE 同时修改同一文件；建议 P0C-FE 先合并。
- `web/src/api/client.ts` 只允许增加导出 token/header helper，不改变 `api()` 行为。

### P1-PUB：发布前置校验

职责：
- 在创建任务或执行任务前校验发布必需条件：标题、正文、封面、账号状态。
- 在 publisher 入口也做防线校验，避免空正文进入 Playwright。
- 不猜测头条 selector，不扩展自动化流程。

允许修改的文件：
- `server/app/services/toutiao_publisher.py`
- `server/app/services/tasks.py`（仅当 P0-B 已合并后再改；否则本包延后）
- `server/tests/test_publish_validation.py`（新建）

禁止修改的文件：
- `server/app/services/accounts.py`
- `server/app/services/articles.py`
- `server/app/api/routes/accounts.py`
- `web/**`
- `launcher.py`
- `geo.spec`

需要补充或更新的测试：
- 新增 `server/tests/test_publish_validation.py`。
- 空正文文章执行后 record failed，错误信息包含“正文”或明确英文等价信息。
- 无封面文章执行后 record failed，错误信息包含“封面”或明确英文等价信息。
- 非 valid 账号创建任务仍返回 400，保持现有行为。

验收标准：
- 缺标题/正文/封面时不启动 Playwright 或尽早失败。
- 错误信息对非技术用户可读。
- 不改变已有成功发布 mock 测试。
- `pytest server/tests/test_publish_validation.py -v` 通过。

可能冲突点：
- 会触碰 `server/app/services/tasks.py`，必须在 P0-B 后执行。
- 不要在此包做 selector 常量化，避免扩大范围；selector 整理可另开小任务。

### P1-UX：任务创建和执行前端体验

职责：
- single 模式只能选择一个账号。
- 运行中的任务禁用或隐藏“执行”按钮。
- 任务创建表单给出更清晰错误。
- 可选：为 stop_before_publish 增加前端开关，但必须与 P0-B 状态机测试通过后再做。

允许修改的文件：
- `web/src/features/tasks/TasksWorkspace.tsx`
- `web/src/types.ts`（仅当需要补字段/状态文案）
- `web/src/styles.css`（仅限少量样式）

禁止修改的文件：
- `server/**`
- `web/src/features/accounts/**`
- `web/src/features/content/**`
- `web/src/api/client.ts`

需要补充或更新的测试：
- 当前项目无前端测试框架，本包至少必须通过 typecheck。
- 手工验收步骤写入 PR/提交说明：single 模式选第二个账号会替换或阻止；running 任务不能重复执行；错误 notice 可读。

验收标准：
- single 任务 payload 永远只包含一个 account。
- running 任务不会让用户继续点执行。
- group_round_robin 仍支持多账号。
- `pnpm --filter @geo/web typecheck` 通过。

可能冲突点：
- 不要与 P1-PUB 混改后端校验。
- 如果加 stop_before_publish 前端开关，需要先确认 P0-B 已完成。

### P1-T：测试补网和迁移/FTS 兜底

职责：
- 补齐不适合放进前面任务包的测试网。
- 修 FTS 查询降级，避免 FTS 表缺失或 trigram 不支持导致搜索 500。
- 增加 Alembic 迁移链测试，不替代现有 `build_test_app()`。

允许修改的文件：
- `server/app/services/articles.py`
- `server/alembic/versions/0003_fts5_indexes.py`
- `server/tests/test_fts_and_migrations.py`（新建）
- `server/tests/test_articles_published_count.py`（新建）

禁止修改的文件：
- `server/app/services/tasks.py`
- `server/app/services/accounts.py`
- `server/app/api/routes/accounts.py`
- `web/**`
- `launcher.py`
- `geo.spec`

需要补充或更新的测试：
- FTS 表缺失时搜索降级到 LIKE，不返回 500。
- FTS `MATCH` 抛 SQLite 错误时降级到 LIKE。
- Alembic 从空 SQLite 执行到 head 成功；如果当前环境不支持 trigram，应验证 fallback。
- `published_count` 只统计 succeeded record，不统计 failed/cancelled/pending。

验收标准：
- 搜索在 FTS 不可用时仍返回可用结果或空数组，不抛 500。
- Alembic 迁移链测试通过。
- 文章列表 published_count 聚合准确。
- `pytest server/tests/test_fts_and_migrations.py server/tests/test_articles_published_count.py -v` 通过。

可能冲突点：
- 会修改 `articles.py`，不得与 P0C-D 同时改同一文件；建议 P0C-D 先合并。
- 迁移 fallback 必须保持向后兼容，不重写历史迁移链以外文件。

## 建议执行顺序

1. P0-A：交付启动稳定性。
2. P0-B：发布执行状态机和线程稳定性。
3. P0C-D：不可逆删除保护，后端。
4. P0C-FE：不可逆删除保护，前端。
5. P1-A：账号导入导出闭环。
6. P1-PUB：发布前置校验。
7. P1-UX：任务创建和执行前端体验。
8. P1-T：测试补网和迁移/FTS 兜底。

可并行建议：
- P0-A 可与 P0C-FE 并行，因为文件不重叠。
- P0C-D 可与 P0C-FE 并行，但要统一删除错误文案。
- P1-A 不要与 P0C-FE 并行，因为都会改 `AccountsWorkspace.tsx`。
- P1-PUB 不要与 P0-B 并行，因为都会改 `tasks.py`。
- P1-T 不要与 P0C-D 并行，因为都会改 `articles.py`。

## 全局冲突点

- `server/app/services/tasks.py`：只允许 P0-B 先改；P1-PUB 后续基于 P0-B 结果做最小补充。
- `server/app/services/articles.py`：P0C-D 先改删除保护；P1-T 后改 FTS fallback。
- `web/src/features/accounts/AccountsWorkspace.tsx`：P0C-FE 先改删除确认；P1-A 后改 import/export token。
- `web/src/styles.css`：多个前端任务都可能想改样式。除非必要，优先复用现有样式；确需修改时每包只追加少量局部样式。
- 测试文件：每个任务包新建独立测试文件，避免多人同时改已有大测试文件。

## 每个任务包的交付格式

每个 subagent 完成后必须给出：

1. 修改文件列表。
2. 对应问题和修复摘要。
3. 新增/更新的测试列表。
4. 已运行的验证命令和结果。
5. 未验证项及原因。
6. 是否触碰禁止文件；若触碰，说明原因并等待人工复核。

## 最终统一验收

所有任务合并后执行：

```powershell
conda activate geo_xzpt
pytest server/tests/ -v
pnpm install
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web build
```

交付包验收：

```powershell
conda activate geo_xzpt
pnpm --filter @geo/web build
pyinstaller geo.spec --noconfirm
```

人工冒烟：
- 首次启动 exe，能打开页面，`launcher.log` 无 token 明文。
- 重启 exe 两次，旧页面刷新后仍能请求 API。
- 无 Chrome 环境启动有清晰提示。
- 创建文章、上传封面、创建 single 任务、执行 mock/真实测试路径。
- 删除文章/账号/分组前均有确认；未完成任务引用的文章/账号不能删除。
- 账号导出后可在干净 data_dir 导入。
