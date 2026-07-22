---
name: xhs-note-creator
description: Use when turning an approved GEO article into a Xiaohongshu (Redbook)
  image-text note — 用户说「把已审文章做成小红书图文/卡片」时用。挑一篇已审文章，按
  用户指定的提示词精简成小红书风格文案，问用户选主题+分页后渲染封面/卡片图，把图片
  链接+文案拼成 markdown 落进未审核库（带「小红书图文」徽标）。不自动发布到小红书。
---

# 小红书图文创作

你把**一篇**已审核 GEO 文章变成一份小红书图文素材（封面图 + 若干卡片图 + 配套文案），
落进未审核库等待人工审核。GEO 后端**不调任何 LLM**——精简文案由你写，render-markdown
由你排版，等价于 `geo-article-writer` 写文章 markdown / `geo-video-composer` 写 storyboard
的地位；GEO 只用 headless Chromium 确定性截图 + 存 MinIO。

不要循环、不要碰其它文章、**不要自动发布到小红书**——只产这一篇的图文素材并落库。

**语言约定**：过程中对用户输出的一切自然语言用简体中文；技术标识符（工具名、
`job_id`/`article_id`/`theme`/`mode`/URL）保留原文。

# 流程

1. **选源文章**：`list_articles(review_status="approved")` 列已审核文章供挑选（或用户已
   直接给了 `article_id`）；`get_article(article_id)` 读全文（用 `plain_text` 做精简素材）。
2. **选精简提示词**：`list_prompt_templates(scope="generation", platform="xiaohongshu")`
   列可用模板（返回小红书专属 + 通用模板），**让用户指定用哪个**（不要替用户默认选一个；
   优先推荐小红书专属模板、没有则用通用模板）；按该模板的语气/结构要求，把正文精简成
   小红书风格短文案（口语化、分点、适度 emoji）。
   **换行很关键**：卡片文案**每个要点/短句单独一行**（render-markdown 里用真换行分隔），
   标题、正文各点、推荐指数、玩家评论都各占一行——渲染会把单换行转成断行（nl2br）。
   **千万别把整段文案写成一行/一大坨**，否则渲染出来挤成一片、可读性极差。
3. **问用户选主题 + 分页 —— 每次都问，不设强默认**：
   - **主题**（`theme`）：`sketch` / `default` / `playful-geometric` / `neo-brutalism` /
     `botanical` / `professional` / `retro` / `terminal`
   - **分页**（`mode`）：`separator`（推荐，用 `---` 手动分卡）/ `auto-split` / `auto-fit` /
     `dynamic`
   - **MVP 提示（必须明确告诉用户）**：不管选哪个 `mode`，当前渲染都按 **separator 语义**
     执行（有 `---` 就按 `---` 切、没有就整篇一张）——`auto-split`/`auto-fit`/`dynamic`
     暂不具备真实的自动排版精度。**所以务必在正文里手动打 `---` 控制分卡**，不要指望
     选了 auto-split 就能自动切得整齐。
4. **问用户是否给卡片配图**（opt-in，**默认关闭**——不问就当用户不要）：
   - 关闭 → 维持现状：纯文案卡，不 embed 任何图（跳到步骤 5，正文里不加 `![]()`）。
   - 开启 → 对**每张卡**都走下面的三层兜底链，找到第一张能用的图就停：
     1. **原文 body 图**：`get_article(source_article_id)` 读 `content_json`，找该卡对应
        游戏/主题的 image 节点，取其 `src`。
     2. **游戏/图片库**：没有原文图 → `list_stock_categories()` 找匹配该卡主题的栏目
        （按栏目名 / `kind="main"` 匹配）→ `list_stock_images(category_id)` 取一张 `url`。
     3. **联网兜底**：库里也没有 → `search_web_image(<该卡关键词，如游戏名>)`，拿返回的
        `url`（`url` 为 `null` 说明搜不到 / 未配置联网，直接跳过，该卡不配图，不重试）。
     - 三层里只要拿到一个 `url`，就把 `![](url)` 放进**该卡 render-markdown 文案末尾**
       （渲染在文字下方、限高，不占满卡片）；三层都空 → 该卡保持纯文案，不勉强凑图。
5. **组 render-markdown**：YAML frontmatter + 正文，卡片之间用 `---` 分隔：

   ```markdown
   ---
   emoji: "🎮"
   title: "标题(<=15字)"
   subtitle: "副标题(<=15字)"
   ---
   🥇 换装+养成首选｜餐厅养成记
   不止换衣服，长安多套造型+上千种家具随便搭 🏮
   古风/宫廷/森系随便拼，五大图鉴记录进度 🍚
   合成经营+剧情三线并行，护肝不逼氪 🌙
   推荐指数：★★★★★
   🗣 玩家：冲国风换装来的，一晚上没了
   ![](上一步该卡拿到的图 url，配图关闭或三层都没有则不加这行)

   ---
   第二张卡片正文……
   ```

   frontmatter 只用于**封面**（`emoji`/`title`/`subtitle`）；正文部分从第一个 `---` 之后
   开始才是卡片内容，卡片间再用 `---` 分隔。配图图片只放在**卡片文案末尾**，不要放进
   frontmatter、也不要替换封面图逻辑。
6. `compose_xhs_cards(render_markdown=<上一步>, theme=<用户选的>, mode=<用户选的>,
   source_article_id=<源文章 id>)` → 返回 `job_id`（异步提交，不等渲染完）。
7. **轮询** `get_xhs_status(job_id)`（间隔 ~5-10s）直到 `status` 为 `done` 或 `failed`：
   - `done` → 取 `cover_url` + `card_urls`（有序数组）
   - `failed` / 长时间未 done → 记录 `error`，不重试、不阻塞，如实告知用户
8. **拼落库 markdown**：封面图 + 各卡片图（按 `card_urls` 顺序，`![](url)`）+ 末尾放**可直接
   发布的成品文案**：小红书标题(一行) → 空行 → 正文 → 空行 → 5-10 个 SEO `#标签`。
   **⚠️ 文案必须是复制粘贴就能直接发的成品,不含任何脚手架/标签**：
   - **不要**加「小红书文案（可直接复制）」这类说明性抬头；
   - **不要**给内容打「标题：」「正文：」「标签：」这种字段标签；
   - **不要**任何解释/元信息文字。用户复制就能发到小红书 App,不用再手动删任何词。
9. `save_xhs_note(source_article_id=<源文章 id>, prompt_template_id=<步骤2 选的模板 id>,
   title=<小红书标题>, markdown_content=<步骤8 拼好的 markdown>)` → 落**未审核库**
   （`review_status="pending"`），内容列表会显示「小红书图文」徽标。
   **⚠️ `title` 硬上限 20 字**（含标点 / emoji，小红书发布强制 ≤20，后端也会挡）：写标题时
   就控制在 20 字内。若返回 400「标题超过20字」，**精简标题到 ≤20 后重新调用本工具**
   （封面 / 卡片图已渲染好、无需重来，只改 `title` + 重拼 markdown 再发一次即可）。

# 约束 / 注意

- **不做自动发布**：本 skill 只产素材 + 落未审核库，不调用任何 distribute / publish 相关
  工具；发到小红书由人工在小红书 App 内完成。
- **主题/分页必须问用户**：不要因为"看起来差不多"就替用户挑一个默认值——每次都问。
- **分页 MVP caveat 必须讲给用户听**（见流程第 3 步），并在实际排版时始终手动打 `---`。
- **小红书文案风格**：标题**必须 ≤20 字**（硬上限，含标点 / emoji；超 20 后端 400 拒绝、
  发布也会被平台拒）；每段落 1-2 个 emoji（不要堆砌）；结尾 5-10 个 SEO `#标签`
  （贴合文章主题 + 小红书常见热门标签）。
- **`prompt_template_id` 要传对**：是步骤 2 用户挑的那个模板的 id，不是随便传 1。
- **卡片图片链接**：`card_urls` / `cover_url` 是 GEO 后端 `/api/xhs-cards/file/...` 形式的
  站内地址，直接原样拼进 `![](url)` 即可，不要自己改写路径。

# 失败处理

- `compose_xhs_cards` 提交失败（如 `render_markdown` 为空 / 参数非法）→ 如实告知用户
  错误信息，不重试。
- `get_xhs_status` 长时间停在 `pending`/`running`（远超预期渲染耗时）或返回 `failed` →
  告知用户 `job_id` + `error`，让用户决定是否重新 `compose_xhs_cards`。
- `save_xhs_note` 失败（如标题超长 / 服务端校验错误）→ 告知用户错误信息，不自动重试；
  已渲染的 `cover_url`/`card_urls` 仍然有效，可以修正 markdown 后重新调用。
