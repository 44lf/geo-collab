"""手工维护的 bundle 版本号 + 已审核 sha 集合。

CI 测试 (test_loop_skill_bundle.py::test_bundle_sha_is_known) 会校验：
如果 build_bundle().bundle_sha256 没记录在 KNOWN_BUNDLE_SHAS 集合里，
fail + 提示开发者：把新 sha 加进 KNOWN_BUNDLE_SHAS 并 bump
LOOP_SKILL_BUNDLE_VERSION，强制「改模板必同步 bump 版本」纪律。
"""

LOOP_SKILL_BUNDLE_VERSION = "2026-07-07-v12"

KNOWN_BUNDLE_SHAS: frozenset[str] = frozenset(
    {
        # v1 (2026-06-24)
        "49f824a36606c285b84c71ade5aec406ea3c545599b7a3ccf59f863b521667dd",
        # v2 (2026-06-25): writer step 5 + 矩阵特例 + orchestrator 日志中文化
        "abd8416c51f0b591c85cee0c3635645a10a313a2cedbeb52b89953a2c41e7fea",
        # v3 (2026-06-25, PR #152): writer 矩阵段 + README step 5 改为 list_stock_categories
        # 自助路径。两个 sha 是同一份内容的两种行尾：CRLF（Windows 本地装的副本）与 LF（CI /
        # Linux）。build_bundle 读字节后直接 sha256，行尾不归一化——两边都要认。
        "58448672effda8290f97dc5afdfb6c4146ea9c8b7cc7c432b2d4a76274b65856",  # CRLF
        "515c9202f1e0880a1657e4da768954e83bb8a2a5cdfc220355a369862d2cefc6",  # LF (CI canonical)
        # v3 (2026-06-25, illustration_warnings PR #154): writer step 5 改 4 类信号检查 +
        # illustration_warnings 字段。
        "ee9659ae08d68a6bdabecfce9f60324fab2093b7ad4535f056f5cdc4ea6a77e8",  # LF
        "c8050b24111efc69b56259194bed5d4b236b537ce7effc777a5e2786b3e44acc",  # CRLF
        # v4 (2026-06-25, this PR): orchestrator skill 主对话叙述深度中文化
        # （6 行日志 / 伪码 echo / notify_feishu / subagent description + 新增叙述规范段）
        "506d2a045eee9106962e97bad0cdf287d6a36f0de2cf2b62265c904be3f22b5c",  # CRLF (Windows host)
        "3c668186ff4edcd42b95f6b59cdc79b2c9045c09bd00692f4f11990c2de6f53b",  # LF (CI canonical)
        # v5 (2026-06-26, web_fallback): writer SKILL 配图段 + 调用约定加
        # web_fallback=True（图库里没有对应栏目的游戏走百度联网补图）。
        "614fb5176177ec1dc703c625a2e2318f2634699d780e22b6cc78a1e5ae6e818d",  # CRLF (Windows host)
        "957ba2c02327a257cd34dc5eede070d30abbea3b7e6983aa9981a2c46cbcc12d",  # LF (CI canonical)
        # v6 (2026-06-26, 部分配图盲区): writer SKILL step5 加第 5 类信号 missed>0
        # （partial：应配 N 张只来 M 张，即便有图也记 illustration_warnings）。
        "091addcab9c96b0f78e72cc59d0b4b33f71cb272c8c13d35c75569b78f2debe6",  # CRLF (Windows host)
        "54b826b5273fe83546adffbc537a018e6f6b26c117e8e07f04b19742ca035b24",  # LF (CI canonical)
        # v6 merge 产物 (2026-06-26): 把 feat 分支 merge 进 main 时，GitLab 与 GitHub 两边的
        # 3-way merge 都会产出这第三种字节序列（语义=干净 v6，writer SKILL 不重复，仅行级合并
        # 产物 sha 不同）。GitLab pipeline #342 在 merge commit b7b5912 上实测算出此值；GitHub
        # PR #162 merge 也算出同值。两边 CI 都需要它放行（早前注释误判「GitLab 不需要」，已订正）。
        "9dfb1db0508d9f930d99e74a25ee6b257c78ed12c4caf2301b1faf4ed708be4b",  # LF (3-way merge product)
        # v7 (2026-06-29, 显式游戏清单产清单): writer SKILL step5 + 调用约定 加 game_positions —
        # 每款一标题的推荐 / 盘点文逐款产 game_positions 走确定性落图（修弱模型漏点缺图），
        # 散文 / 综述回退现有模型识别路径。配合已合并的端点 + MCP 工具 game_positions 形参。
        "06df0c1fb709cc3e5e8f4edbf110a7005e1fa1bce16597c9ef42dde9ee5a5c36",  # Windows 本地工作区 (autocrlf → uniform CRLF)
        # CI canonical = git blob 字节（模板全是 LF；Linux runner autocrlf=off，checkout 即 blob）。
        # 实测 GitHub Actions run 28347499953 backend-tests(2)。算法务必读 blob 而非工作区归一化。
        "23707c7eb05343471cd8bc313d2685822395a93fa9f75ebdff6d4d692d16d7c6",  # LF (CI canonical, blob)
        # v8 (2026-06-29, prompt_template_name 展示参数): writer SKILL step 4 + save_article
        # MCP 工具签名加 prompt_template_name + question_text_preview 两个可选展示参数。
        # Claude Code UI 在工具调用渲染时会同时显示中文名（如
        # `prompt_template_id: 13, prompt_template_name: "游戏情绪清单"`），运营在主对话里
        # 不用回头查数字。后端 SaveArticleFromMcpPayload 用 Pydantic 默认 extra='ignore'
        # 丢弃这两字段，无需后端 schema 改动。
        "128eb1d6a0198fe62313474ef47f0c63788068c348814177853cb790cec404b7",  # CRLF (Windows host)
        "e9da575b99e750f919713f1f8f678f0fbb17d71713ce91849bcf911321343111",  # LF (CI canonical)
        # v9 (2026-06-29, tpl_id 命令行覆盖): orchestrator SKILL Goal Parsing 表加 tpl_id
        # 字段 + 主循环 `tpl_id = target.tpl_id or templates[attempts % len(templates)].id`。
        # 用户在 /goal 里写 `生文提示词Id=13` / `生文提示词 #13` / `用生文提示词 13`（也
        # 兼容旧写法 `tpl=13` / `模板 #13`）即固定走该提示词；缺省仍是全提示词 round-robin
        # 轮转。启动检查日志加「生文提示词：<#id 写死|轮转>」字段让运营在主对话里一眼可见；
        # 中文叙述对照表把「模板」统一改成「生文提示词」，与后端 PromptTemplate schema
        # 命名一致。
        # 下面 4 个 sha 对应 build_bundle 的 sorted(rglob) 跨 OS 文件序 × 行尾两维（根因见
        # v7/v8 注释；本批为实测复现值，原 v9 登记的两值都对不上真实运行环境，没跑 CI 漏掉）。
        # 前两个是真实运行环境实测算出、必须认：
        "6eb8f07c8391a8ba400d2df4d5241ac40f8f4cc87b27e8ac9018e24726c364b9",  # Linux序+LF (GitHub/GitLab CI canonical, 实测 build_bundle)
        "0ab1a3f1a361435816549b58f83260ba45224745618e0ef264186934904c3984",  # Windows序+CRLF (autocrlf=true 本地工作区, 实测)
        # 下面两个是先前登记值：f40f9c28 实为 Windows序+LF（autocrlf=false 的 Windows checkout），
        # 早前误标「CI canonical」；b360c408 对不上任何标准组合（来源存疑/stale）。只增不删保留。
        "f40f9c28625e8e430fd4ebaa538d83a44d32b804d5c7e6cb5fc5662731218001",  # Windows序+LF (autocrlf=false Windows)
        "b360c4086ec0aca8db7ffdbd961172bd5c9be15951180236ec71944353045f61",  # 来源存疑(对不上标准组合), 保留不删
        # v10 (2026-07-01, 引号转义指引): writer SKILL「保存到 GEO」段加两行 —— markdown_content
        # 是正文自然文本不是 JSON 源码、正文引号不要写成 `\"`；中文优先用 “”/「」，需 ASCII 引号
        # 时直接写 `"`。修 loop 生文正文里混入反斜杠转义引号的问题（提交 a13ad91 改了模板忘了
        # 同步 bump 版本 → GitLab pipeline #419 test_bundle_sha_is_known 全红）。下面两值均为真
        # 环境实测：CI canonical 直接取自 pipeline #419 的 Linux runner 输出。
        "20ed5901f0e51ea21d1e889c692f26566a412b879b1adee784a4a389541ea42d",  # Linux序+LF (CI canonical, blob，实测 pipeline #419)
        "a4c3c4bb2a51a8f2ee7c84eeaf08530274790cc2a20ef723294fdd1633977501",  # Windows序+CRLF (autocrlf=true 本地工作区, 实测 build_bundle)
        # v11 (2026-07-02, 飞书通知统一 + 保留富文本): orchestrator SKILL Required Checklist
        # 加第 4 步「开始飞书播报」（此前完全没有开始通知，只在退出前发一次）+ 主循环加
        # notify_exit(title, level, reason=None) helper，五个退出闸门全部改走它。第一版
        # 曾把消息压成纯计数「累计通过 X/N 篇 · 共耗时 M 分钟」，但对照真实生产消息发现
        # 运营依赖的选题/文章标题/评审分数这些明细全被砍掉了——改成 run_log 逐轮累积
        # {qid, question, article_id, title, decision, score_total}，退出播报固定按
        # 「目标回显 → 本次产出明细（每条 3 行：选题/产出/评审）→ 累计通过 + 耗时 → 原因」
        # 渲染，格式统一的同时不再损失信息量。同批把配套的 claude-loops/generation-loop.md
        # （不在本 bundle 内，无需 sha 登记）也做了同构改造：补齐此前只有文字描述、没有
        # 实际 notify_feishu 调用的 3 条退出路径（15 轮耗尽/候选用完/MCP 连续失败 3 次），
        # 砍掉每次 save 失败就发一条 "save 失败" warning 的噪音通知，并同样加上 run_log
        # 逐篇明细。
        # 本次改动先用 pytest 实测拿到 Windows序+CRLF 值，再写脚本复刻 build_bundle 的
        # 排序/hash 算法，分别模拟 Linux序/Windows序 × LF/CRLF 四种组合，并用实测值校验
        # 脚本准确性（Windows序+CRLF 完全对上实测），据此推算 Linux序+LF 的 CI canonical——
        # 避免像 v10 那样漏跑 CI 先致 pipeline 全红。四值仍是推算，首次真实 CI 跑过后
        # 如与此处不符，以 CI 报的 current 值为准并在此补注「实测」。
        "e6a95d2fa2c0740c111c69842c9bcdb36b459f117e06e6a7d222293f0d535b92",  # Windows序+CRLF (autocrlf=true 本地工作区, 实测 pytest)
        "37b008734533f12c77b0209399559570b2d5ad3475295a49af1fcd3376564db3",  # Linux序+LF (推算 CI canonical，脚本复刻未跑真实 CI)
        "2c27fb2dd50043797c39232d285741e63b2daca38d81a1d7c90900ce45e0d743",  # Windows序+LF (推算，autocrlf=false Windows checkout)
        "cdc2d515eb9b84308ca278f6cf2a6c3d5cfb99b946943c6a296dd3c2c737f734",  # Linux序+CRLF (推算，理论组合，正常环境不应出现)
        # v12 (2026-07-07, 重试尊重锁定 + 重试失败通知): orchestrator SKILL 加「锁定与重试模型」
        # ——用户 `问题Id=` 精确锁问题词 / `生文提示词Id=` 锁模板时，评分不过按 worklist +
        # 「重写同一问题最多 REWRITE_CAP=2 次」而非随机换题；verifier 返回加 weak_dims +
        # reasoning 喂给 writer 的重写模式（rewrite_feedback）做针对性改进；退出播报加
        # retry_exhausted 段单列「锁定问题重写到上限仍未过审」。同批 commands/goal.md 加锁定
        # 用法示例，writer SKILL 加「重写模式」段。
        # 关键：本版**顺带根治了 service.build_bundle 的跨 OS 排序**——改为按 posix 串排序
        # （原先 sorted([Path]) 在 Win 大小写不敏感 / Linux 敏感 → 文件序不同），排序跨 OS
        # 确定后 bundle_sha 只剩行尾一个维度，从此每版只需登记 2 个 sha（LF + CRLF），不再有
        # Win序/Linux序 × LF/CRLF 的 4 组合噩梦（v7~v11 反复栽）。下面两值均由脚本复刻算法
        # 实测（scripts/_compute_bundle_sha.py，用完即删）：CRLF 与本机 real build_bundle 对上，
        # LF 为 Linux runner checkout（LF blob）的 CI canonical。
        "eb9edee125eba132fa5ff3ab60cbcc52b51d72d4c6ff41d912f43485f775d14e",  # posix序+LF (CI canonical, blob)
        "ff752ede9aee4eb14391f16c15ee9de4a88b00c347cd41584e9d6270abc06fcf",  # posix序+CRLF (autocrlf=true 本地工作区, 实测)
    }
)
