# 游戏库语料底座 · 代码核对修复清单

> 来源:对 `feat/game-library-corpus` 分支 Codex 实现(commits `b349783`→`90a17f9`)的只读代码核对
> (5 个 code-reviewer subagent 分模块并行 + 主对话独立精读交叉验证)。
> 日期:2026-07-20。本清单**只列问题不改代码**,供另一窗口按条执行。
> 配套实现计划:`docs/superpowers/plans/2026-07-20-game-library-corpus.md`。
>
> **核对结论:核心业务闭环跑得通、核心算法都写对了**(见文末「✅ 已验证正确,勿动」)。
> 以下是逻辑 / 健壮性 / 安全层面的待修项,行号以核对当时为准,动手前先核对是否漂移。

---

## 执行建议(排序)

1. **先做一次聚焦重构**:把 `upsert_game` 的「下载→建栏目→存图」移出 DB 事务
   —— 一改同时收掉 **C2 + M1 + M2(games 侧)**。
2. 再各自独立小改:**C1**(SSRF)、**C3**(LRU 守卫)、**M3**(scheduler)、**M4**(ORM 约束)、**M6**(schema 放松)。
3. 最后补 **M7** 测试缺口 + Minor。

每修一条建议:先补/改测试锁行为 → 跑该测试红 → 改实现 → 跑绿 → 提交(尾行保留
`Co-Authored-By: <你的模型署名>`)。DB 测试需 `GEO_TEST_DATABASE_URL` 指向含 `test` 的库,
用 `env python -m pytest`(conda activate 在工具 shell 不生效)。

---

## 🔴 Critical — 合并前必修

### [ ] C1 · `image_download.py` 的 SSRF 防线未真正生效
- **文件**:`server/app/shared/image_download.py:21-28`(`_urlopen_same_host`)、`:40-61`(`download_image`)
- **问题**:
  - urllib 默认自动跟随重定向,`_urlopen_same_host` 是**请求发出之后**才比对 host
    → 内网请求 / 文件读取已经发生;`build_opener` 默认挂 `FileHandler`,`file://` 也会跟。
  - `download_image` 对**初始 URL** 不做 scheme / 私网校验,而截图 URL 来自第三方抓包
    (`sources/taptap.py`、`sources/baidu.py`,不可信,可被投毒 / 劫持)。
  - 实害有边界(响应要过图片魔数校验才返回 → `file://` 内容读后即弃、内网 HTTP 是盲 SSRF),
    但「请求本身不该发出」没挡住。
- **修**:
  - 自建**不挂 `HTTPRedirectHandler`** 的 `OpenerDirector`,手动取 30x 的 `Location`,
    在**发起下一跳前**校验其 host。
  - 连接前对 host 做 DNS 解析,用 `ipaddress` 拒 `is_private` / `is_loopback` /
    `is_link_local` / `is_reserved`(顺带防 DNS rebinding)。
  - scheme 白名单只允许 `http` / `https`(硬拒 `file://` / `ftp://`)。
- **测**:补直接调 `download_image` / `_urlopen_same_host`、用可控 fake opener 模拟
  「30x 跳到不同 host / 私网 IP」的用例,断言被拒(理想上断言未对第二跳发起连接)。
  当前 `server/tests/test_image_download.py` 3 个用例**全把 `_urlopen_same_host` monkeypatch 掉了**,
  这条安全特性零覆盖。

### [ ] C2 · `upsert_game` 持有 DB 连接期间做同步 HTTP + MinIO 上传
- **文件**:`server/app/modules/game_library/service.py:73-81`
- **问题**:每游戏最多 6 张图 ×(10s 下载 + MinIO 上传)全在**未提交事务内**跑,
  和 web 请求共用 `SessionLocal` 连接池(`bg_session_factory = SessionLocal`)
  —— 正是项目吃过的 db-pool-exhaustion 反模式,设计稿明文要求「HTTP 在 session 之外做」。
  最坏单游戏占住一条 MySQL 连接近 60s。
- **修**:先在**无 session** 阶段把所有截图字节下好(`download_image` 无状态,可先收
  `list[(url, data, mime)]`),再开短事务批量 `store_image_bytes(..., commit=False)` + 一次 flush。
- **一并处理**:见 M1、M2 —— 三条同源(照抄 `ai_format` web_fallback 写法带进来的)。

### [ ] C3 · `ai_illustrate` 阶段 1.5 对 no-op 重调重复计数旧图 → 软 LRU 被反噬
- **文件**:`server/app/modules/articles/ai_illustrate_svc.py:222-241`
- **问题(已亲自验证)**:`ai_format.py:610-613` 有 `if has_images_in_content(content_json):
  return content_json, 0` 早退 —— 文章**已有图**时再次配图恒 0 张 no-op。而阶段 1.5
  **不看 `images_inserted`**、无条件重扫最终 `content_json` 把**旧图**的
  `use_count`/`last_used_at`/`last_used_article_id` 再刷一遍。超时重试(该模块历史有
  138–300s + 客户端重试记录)会精确命中,把「刚用过的图」错标成「更近使用」,直接架空
  LRU 公平轮换,且**静默失败**(不报错、不出现在返回里)。
- **修(最小)**:阶段 1.5 前加 `if images_inserted > 0:` 门槛(`images_inserted` 在此作用域已可读),
  no-op 时整段跳过。
- **修(更稳)**:让 `_maybe_insert_images` / `run_ai_format_from_game_list` 回传本轮**实际插入**的
  `image_id` 列表(沿用 `hook.py` 记录 `refs` 的思路),而非事后从 content 反推。
- **测**:`server/tests/test_image_lru_usage.py` 补「对已有图的文章重复调 `illustrate_one`,
  断言旧图 `use_count` 不再增长」。

---

## 🟡 Major — 建议合并前处理(可与 Critical 分开验收)

### [ ] M1 · `get_or_create_companion_category` 内部提前 commit,打破每游戏原子提交
- **文件**:`server/app/modules/image_library/service.py:83-84`(被 `game_library/service.py:73` 调用)
- **问题**:某游戏**首次**建陪衬栏目时,内部 `db.commit()` 把半成品 `row`(score/tags/sources)
  连同新栏目提前落库,发生在 `row.stock_category_id = cat.id` 赋值 + 截图入库**之前**。
  若其后步骤抛错,scheduler 的 `rollback` 已回滚不掉 → 部分持久化(下一轮幂等自愈,
  但违反「每游戏一次 commit」契约,且 `expire_on_commit` 会让 `row` 中途过期、多打 SELECT)。
- **修**:随 C2 重构一并解决 —— 把「找/建栏目」挪到 `upsert_game` **最前**单独提交(合并字段之前),
  或给它一个不内部 commit 的变体(`store_image_bytes` 已证明 `commit: bool` 模式可行)。

### [ ] M2 · SELECT-then-INSERT 竞态无 `IntegrityError` 兜底(多处同病)
- **文件**:
  - `server/app/modules/game_library/service.py:38-51`(Game 行「先查后插」)、`:67-71`(GameTag)
  - `server/app/modules/image_library/service.py:107-117`(dedup 查)+ `:138-144`(插入,`commit`/`flush` 未捕获)
- **问题**:多实例 web 各起入库线程(CLAUDE.md 已承认)/ 生文 `ThreadPoolExecutor(max_workers=4)`
  下两篇文章命中**同一陪衬游戏 + 同一图 URL**时,两线程都过 SELECT 再各自 INSERT
  → 后到者撞 `uq_games_name_normalized` / `uq_game_tags_game_tag` /
  `uq_stock_images_category_source_hash`,抛**未捕获** `IntegrityError`。`store_image_bytes` 异常
  会一路上抛让**整轮配图作废**(不止丢一张)。同 PR 复用的 `get_or_create_companion_category`
  反而做了兜底 —— 防御水位不一致。
- **修**:给 Game 行创建、GameTag 插入、`store_image_bytes` 的 `commit()`/`flush()` 都补
  `except IntegrityError: rollback + 回退查询已有行`(参照 `get_or_create_companion_category`),
  或换 `INSERT ... ON DUPLICATE KEY` / `INSERT IGNORE` 语义。

### [ ] M3 · `scheduler.py` 隔离不达标(target 解析在 try 外 + 无 per-game try)
- **文件**:`server/app/modules/game_library/scheduler.py:46-51`、`:52-74`
- **问题**:
  - `target["source"]` / `int(target.get("max_games"))` 在 `try` **之外** → 某个 target 配置写错
    key / 非数字会未捕获冒泡,**整轮入库腰斩**(违反 docstring「逐目标隔离」)。
  - for-game 循环无 per-game try/except → 单个游戏异常(如 M2 的 IntegrityError)会跳过该 target
    **后续所有**游戏。spec 要求 game 级 + target 级双隔离,现只有 target 级
    (测试恰好把失败注入在 try 内的 `collect_pool`,所以一直绿)。
- **修**:①把目标解析挪进 `try`(或单独 try 跳过坏 target);②for-game 循环内加
  per-game `try/except: db.rollback(); continue`,只在 target 级异常(如 `collect_pool` 本身失败)才整体放弃。
- **测**:补「坏 target 不拖垮后续 target」+「坏 game 跳过后继续同 target 后续 game」。

### [ ] M4 · `StockImage` ORM 模型缺 `UNIQUE(category_id, source_url_hash)` 声明
- **文件**:`server/app/modules/image_library/models.py`(StockImage `__table_args__`)
  对照 `server/alembic/versions/0065_game_library.py:102-106`(迁移已建 `uq_stock_images_category_source_hash`)
- **问题**:测试库走 `Base.metadata.create_all`(`server/tests/utils.py:149`)而非 alembic
  → 该约束在**测试环境根本不存在**,去重只靠应用层 SELECT(有竞态,见 M2),并发兜底既
  「代码里没 catch、测试 schema 里也没约束」双重落空。生产/测试 schema 漂移。
- **修**:给 `StockImage` 补
  `__table_args__ = (UniqueConstraint("category_id", "source_url_hash", name="uq_stock_images_category_source_hash"),)`
  (约束名与迁移一致)。

### [ ] M5 · `query_games_by_tags` 对 `g.tags` 触发 N+1
- **文件**:`server/app/modules/game_library/service.py:139-150`(`_game_to_dict` + diversity 重排都读 `g.tags`)
- **问题**:`Game.tags` 默认懒加载,limit≤100 就多打 ≤100 次 SELECT;这是 writer 每篇文章都调的高频 MCP 路径。
- **修**:`select(Game)...` 加 `.options(selectinload(Game.tags))` 一次性批量拉回。

### [ ] M6 · `SelectedGame.game_id` 必填 int 与文档「留空」矛盾 → 422 连累整篇保存
- **文件**:`server/app/modules/articles/routers/mcp.py:194-196`(`SelectedGame`)、
  `server/mcp/tools/action.py:114-115`(文档措辞)
- **问题**:MCP 文档写「回退 websearch 的游戏无 game_id,**留空**」,但 `game_id: int` 必填。
  LLM 若按字面传 `{"game_id": null, "name": "..."}`,FastAPI/Pydantic 在进 handler **之前**
  422 整个请求 → 把本轮主对话写好的**全部 markdown 正文一起打回**(零配置生文正文只在对话上下文,
  不落库即丢)。而 `bump_game_usage` 本已过滤 falsy id(`service.py:155`),后端逻辑本能兼容空 id。
- **修**:`SelectedGame.game_id: int | None = None`,让「websearch 无 id 的游戏」可合法随列表传,
  把附属功能(用量回写)容错性与主功能(保存文章)解耦。同步把 `action.py` 文档措辞改清楚。

### [ ] M7 · 关键路径测试缺口
- **文件**:`test_game_upsert.py` / `test_game_query.py` / `test_image_lru_usage.py` /
  `test_image_store_dedup.py` / `test_game_sources.py`
- **补**:
  - [ ] upsert 第二源用 `score=None` / 无 `description` → 断言**不覆盖**已有非空值(None-safe 合并)。
  - [ ] 同 URL 重复 upsert → 断言 `StockImage` 只 1 条(截图去重)。
  - [ ] `query_games_by_tags` 断言**返回顺序**(`last_used_at ASC` + diversity 命中排前),非只断言集合。
  - [ ] `exclude_tags` / `min_score` 生效。
  - [ ] 空 `relevant_tags` 服务层返回 `[]`(现只靠 router 层 Pydantic `min_length=1` 兜)。
  - [ ] `taptap.get_detail` 字段映射(尤其 `data.app` 嵌套层级 —— 注释自述踩过坑)。
  - [ ] C1 / C3 的用例(见上)。

---

## 💭 Minor / Nits(择机)

- [ ] **TapTap `get_detail` N+1 无节流**:`scheduler.py:60-65` 对 pool 内每个缺图 taptap 游戏背靠背调
  `get_detail`(单轮 30+ 连续请求),无 sleep/退避 —— 呼应千帆搜图限流教训,易触发 UA/IP 封禁。
  加 0.3~1s 固定/随机间隔。**运维上可能升级到 Major。**
- [ ] `registry.collect_pool` 分页中途 `search()` 失败会丢弃已收集的部分(`registry.py:17-30`);
  可改「失败返回已收集部分 + 记警告」。
- [ ] `search_by_name` 死代码:`sources/baidu.py:118`、`sources/taptap.py:278`(+ `_bootstrap_xsrf`/
  `_multipart_encode`/`_to_game_from_brand`)在 game_library 内零调用、零测试(含复杂 CSRF/multipart)。
  要么排上「按名精确查」用例,要么模块注释注明「当前未接线」。
- [ ] `icon_url` 用 `_prefer_longer`「取更长」启发式对 URL 略随意(`service.py:56`);
  改「优先非空、都非空取新源」更贴设计原文。不影响正确性。
- [ ] `_normalize_game_name` 是 `articles/formatting/document.py` 的私有函数,被本模块当**身份键**
  用(`service.py:9,37`);耦合脆(改 heading 匹配会悄改游戏合并语义)。提升为公开 API 并注明,
  或在 game_library 自建归一化。
- [ ] diversity 重排在 SQL `.limit()` **之后**(`service.py:139-148`),只能在已选前 N 内打散、
  捞不回排名靠后但更匹配的游戏。若后续发现 diversity 效果弱,先看这里。
- [ ] `scheduler.py:1` 模块 docstring 缺 `sync_scheduler.py:1-11` 同款「多进程部署注意/建议单进程跑 web」告警。
- [ ] CLI 失败无非零退出码:`server/scripts/ingest_games.py:29-30`,`result["failed"]` 非零仍退 0
  → cron 无法判失败。加 `if result["failed"]: raise SystemExit(1)`。
- [ ] `pages` 是死参数:CLI `--pages` / `SEED_TARGETS` 的 `"pages"` 键从没被读
  (`run_ingest_once` 只取 source/category/max_games/max_shots;`collect_pool` 按 pool_size 自算翻页)。
  删掉或真正接入 `page_size_cap`。
- [ ] `registry.py:19` `cap = page_size_cap or ...` 用 `or`,`page_size_cap=0` 会被短路;
  改 `... if page_size_cap is not None else ...` 更严谨(当前无调用方传 0)。
- [ ] `Game.is_active` 模型漏 `index=True`(`models.py:46-48`),迁移建了 `ix_games_is_active`
  → create_all 测试库无此索引(仅性能,schema 漂移)。
- [ ] `types.py` 的 `android_package` / `raw` 字段 `upsert_game` 完全不消费、`games` 表也无对应列;
  在 service 或设计文档注明「暂不持久化」,免被误当遗漏去修。
- [ ] `bump_game_usage` / `bump_stock_image_usage` 用 `synchronize_session=False` 批量 UPDATE
  (现状无 bug);未来若有调用方 bump 后立刻读内存 `use_count` 会读到 stale 值,需 `db.refresh()`。仅留意。

---

## ✅ 已验证正确 —— 勿动(避免「修」坏)

- **跨源并集合并**:tags 走「先查后插」并集(**非** delete-all 覆盖)、score/comment_count 取 max 且
  None-safe、sources/platforms/screenshots 集合并集且都**重新赋值**写回 JSON 列(正确避开 JSON 就地改不落库)。
- **relevant / diversity 严格分离**:准入子查询只用 `relevant_tags`,diversity 只 Python 侧重排,**绝不放行偏题游戏**。
- **save_article 用量回写**:`bump_game_usage` 按 `game_id` 匹配、在 `db.commit()` 前与 `create_article`
  **同一事务**、未知 id 天然跳过(`mcp.py:292-296`)。
- **图片回写链路**:`ai_illustrate` 阶段 1.5 扫**最终** content 的 stockImageId(路径无关、best-effort);
  `ai_illustrate_svc.py:227` 从 `document` 导入 `loads_content_json` **能解析**(document.py:13 转导出自 parser,非 bug)。
- **软 LRU** `pick_image_id` 排序 `last_used_at ASC`(NULL 优先)= 新图优先(`selector.py:63-67`)。
- **MCP**:两个新 tool 都 `async def` + `_aget/_apost`(anyio.to_thread,无自调用死锁);
  `MCP_TOOLS_COUNT=35` 与两个计数测试**三处一致**;两个新端点走 `mcp_exception_response`。
- **爬虫折叠**:`types.py`/`sources/*.py` 与 `E:\1\game-search` 逐字节忠实(仅 ruff 格式差异),`registry.py` 裁剪干净。
- **迁移 0065**:down_revision 链、FK/约束命名、downgrade 对称顺序(约束→FK→列→子表→父表)、索引字节上限均正确;
  `use_count` 加 `server_default="0"` 使既有行不违反 NOT NULL。**迁移本身无需改。**
