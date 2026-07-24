# 文章 → 配套视频（Claude Code Loop 版）设计

- 日期：2026-07-07
- 状态：设计定稿，待实现计划
- 相关：`docs/superpowers/specs/2026-06-24-goal-loop-engineering-design.md`（生文 loop）、`CLAUDE.md` 的「MCP Server」「AI 生文模块」章节

## 1. 背景与命题

GEO 平台目前产出的是**文章**（`content_json` / `content_html` / `plain_text` 三份并行结构），
通过 Toutiao / 微信公众号等驱动分发。近期观察到豆包等生成式引擎（GEO 语境下的
「被 AI 检索/引用」目标）里**视频形态的露出占比越来越高**。命题：

> 如何让我们产出的文章**快速生成配套视频**，从而在 AI 引擎里以视频形式被检索、引用、露出。

### 核心判断：GEO 已具备"文章→视频"的大半原料

文章转视频最贵的部分（脚本、画面素材、配图逻辑）GEO 现有资产已覆盖：

- `plain_text` / `content_json` → 现成的逐段脚本 / 字幕文案
- `image_library`（MinIO 分桶、`StockCategory` 分类）→ 现成的画面素材库
- `pipelines`（节点注册表）+ `tasks/drivers`（驱动注册表）+ MCP tools → 现成的编排 + 分发骨架

所以"快速生成配套视频"的本质不是从零做视频引擎，而是在既有流水线上挂一个
**复用图库和配音的渲染环节**。

## 2. 目标与非目标

### 目标（MVP）

- 由 **Claude Code 主对话触发**（镜像 `/goal` 生文 loop），调 GEO MCP 工具，把一篇文章
  变成一个**可发布的视频资产**：`mp4 + SRT 字幕 + GEO 友好标题/描述/话题标签`。
- 视频形态：**图文轮播短视频** = 逐段字幕 + TTS 配音 + 图库逐段选图（Ken Burns 缓推）
  + 烧录字幕 + 可选 BGM。
- **真零配置**：默认链路（edge-tts + ffmpeg）不需要任何外部 API key，和生文 loop
  "不需要 `GEO_AI_API_KEY`" 的卖点对齐。
- 产物落库、关联 Article，供人工上传到视频平台。

### 非目标（YAGNI，明确不做）

- **数字人口播**（HeyGen / 腾讯智影类 avatar）——留 `video_engine` 可插拔接口，以后挂。
- **文生视频**（可灵 / 即梦 / Sora 类）——贵、慢、不可控，不适合每篇量产。
- **视频平台自动发布驱动**（抖音 / 西瓜 / 视频号的 Playwright 自动上传）——反爬严、
  维护贵；对 GEO 露出而言核心价值在"产出可被索引的资产"，发布人工即可。
- Web UI 的"生成视频"按钮 / pipeline `compose_video` 节点——**fast-follow**，非 MVP。
  三个入口未来共用同一个 `video/service.py`，本设计只交付 MCP loop 入口。

## 3. 关键设计原则：借用生文 loop 的"零配置"分工

生文 loop 最核心的约定不是"用 MCP"，而是：

> 凡是 host 端 Claude 自己能干的"智能活"，就不要让 GEO 后端再调一次 LLM（冗余）。
> GEO 只干确定性 / 重活 / 有状态的事。`save_article(markdown)` 存文章时**全程不调 LiteLLM**——
> markdown 是 Claude 写的。

套到视频上，核心洞察：

> **「分镜脚本 storyboard」之于视频，正如「markdown」之于文章。**
> 它是 Claude 在主对话里写出的确定性产物，GEO 拿着它跑 TTS+ffmpeg 落库，**GEO 不调任何 LLM**。

### 分工表

| 环节 | 谁干 | 说明 |
|---|---|---|
| 读文章 | GEO（`get_article`，已有） | 提供 `plain_text` / `content_json` |
| 切分镜、写逐段字幕/口播文案 | **Claude 主对话** | 文章原文即脚本，Claude 擅长 |
| 逐段选图 | **Claude 主对话** | `list_stock_images` 看候选 → storyboard 里点名 `asset_id` |
| 写标题/描述/话题标签 | **Claude 主对话** | GEO 友好、和写 markdown 一样 |
| 看候选图 | GEO（`list_stock_images`，新增） | 返回文件名/标签/URL 供 Claude 点名 |
| TTS 配音 | GEO（`compose_video`，新增） | 密钥留服务端；默认 edge-tts 无 key |
| ffmpeg 合成 | GEO（`compose_video`，新增） | 容器内二进制 |
| 落库 + 状态 | GEO（`compose_video` / `get_video_status`） | 异步；产物关联 Article |

**GEO 在整条链路里不调任何 LLM。** 所有"创作智能"都在 Claude 主对话。

## 4. Storyboard 数据结构（Claude 写，GEO 渲染）

`compose_video` 的 `storyboard` 参数是 Claude 主对话产出的确定性 JSON，等价于
`save_article` 的 `markdown_content`。结构：

```jsonc
{
  "title": "视频标题（GEO 友好、含关键词）",
  "description": "视频描述/简介，人工上传时粘贴到平台",
  "tags": ["话题标签1", "话题标签2"],
  "aspect_ratio": "9:16",          // 竖屏默认；可 "16:9"
  "bgm": "default" | "none",       // MVP 用内置 BGM 或无
  "shots": [
    {
      "subtitle": "本镜头烧录到画面的字幕文案（简短）",
      "narration": "本镜头 TTS 念的口播文案（可与 subtitle 相同或更口语）",
      "asset_id": 12345,           // Claude 从 list_stock_images 点名的图；null=用文章封面兜底
      "duration_hint": 4.0         // 秒，可选；缺省由 narration 时长决定
    }
    // ... 逐镜头
  ]
}
```

约定：
- `subtitle` 用于**画面烧录** + 生成 **SRT**；`narration` 用于 **TTS**。二者可相同。
- `asset_id` 为空 → GEO 用文章 `cover_asset` 兜底；仍为空 → 纯色/模板背景卡兜底（不失败）。
- 镜头时长优先取 TTS 音频真实时长，`duration_hint` 仅作下限提示。
- GEO 对 storyboard 只做**结构校验 + 确定性渲染**，不改写、不"创作"。

## 5. 新增 MCP Tools（3 个，21 → 24）

改 `mcp_catalog/connect_router.py:MCP_TOOLS_COUNT` 为 24。三个 tool 落在
`server/mcp/tools/video.py`，后端对应 sub-router `video_mcp_router`（`Depends(require_mcp_token)`）。

### 5.1 `compose_video`（action）

```
compose_video(article_id: int,
              storyboard: dict,
              engine: str | None = None,     # None=默认 edge-tts；"volcano" 等可选升级
              model_label: str | None = None # 溯源，存 metrics
) -> {"ok": True, "data": {"job_id": str, "article_id": int, "status": "pending"}}
```

- 校验 article 存在、storyboard 结构合法、`asset_id` 属于允许的图库。
- 异步：立即返回 `job_id`，后台线程跑 TTS+ffmpeg。**GEO 不调 LLM。**
- 未捕获异常走 `core/mcp_errors.mcp_exception_response(exc, context=...)`（上游 TTS/网络错 → 502）。

### 5.2 `get_video_status`（catalog）

```
get_video_status(article_id: int | None = None,
                 job_id: str | None = None
) -> {"ok": True, "data": {
        "job_id": str, "article_id": int,
        "status": "pending" | "running" | "done" | "failed",
        "progress": float,            // 0..1
        "video_url": str | None,      // done 时可下载
        "srt_url": str | None,
        "title": str | None, "description": str | None, "tags": [str],
        "error": str | None
}}
```

Claude loop 轮询到 `done` 取产物 URL / 文案；`failed` 拿 `error` 重试或换图。

### 5.3 `list_stock_images`（catalog）

```
list_stock_images(category_id: int, limit: int = 50
) -> {"ok": True, "data": [
        {"asset_id": int, "filename": str, "tags": [str], "url": str, "w": int, "h": int}
]}
```

补齐现有 `list_stock_categories`（只到类别粒度）到**具体图**粒度，让 Claude 在
storyboard 里点名 `asset_id`。Claude 按 filename/tags 推理选图（看不到像素但可从
标签/文件名判断）。

## 6. GEO 后端：`video/` 模块

新增 `server/app/modules/video/`，自包含 `models.py` + `schemas.py` + `service.py` + `router.py`。

### 6.1 数据模型

- `VideoJob`（新表）：`id / job_id(uuid) / article_id(FK) / status / progress / storyboard(JSON) /
  engine / video_asset_id(FK, nullable) / srt_asset_id(FK, nullable) / title / description /
  tags(JSON) / error / created_at / updated_at`。
- 产物（mp4 / srt）复用现有 `Asset` + MinIO 存储，`VideoJob` 引用其 id。
- 迁移：新建 `video_jobs` 表（Alembic，头部随最新版本，不写死版本号）。

### 6.2 渲染服务 `service.py`

纯函数式 pipeline，全确定性、无 LLM：

1. **切段/校验**：读 storyboard，校验结构 + `asset_id` 归属。
2. **配音**：逐镜头 `narration` → TTS → 逐段音频 wav/mp3（引擎注册表，见 6.3）。
   镜头时长 = max(音频时长, `duration_hint`)。
3. **画面**：按 `asset_id` 从 MinIO 取图；空则文章封面 → 模板背景卡兜底。
4. **字幕**：`subtitle` → SRT（精确时间轴，累加镜头时长）+ ffmpeg 烧录（`subtitles` / `drawtext`）。
5. **合成**：ffmpeg 逐镜头 Ken Burns（`zoompan` 缓推）+ 拼接 + 烧录字幕 + 可选 BGM（`amix`）
   → mp4（竖屏 1080x1920 默认）。
6. **落库**：mp4 + SRT 存 `Asset`，写 `VideoJob.status='done'` + 产物 id + Claude 传的 title/description/tags。

**运行环境（服务器侧，非用户本地）**：异步渲染跑在 **GEO 服务器的 app(web) 容器**后台线程
（`bg_session_factory`，和生文一样**没有独立 worker**），因此 ffmpeg / edge-tts / 中文字体装进
**app 镜像（`Dockerfile.app`）**，不是 worker 镜像。**普通用户本地零依赖**——本地 Claude Code 只经
MCP over HTTP 调远端 GEO，渲染全在服务器完成（与生文 loop `save_article` 同模式）。DB session
非线程安全，后台线程内自建 session。**生产部署要点**：升级时重建 app 镜像才带上 ffmpeg/字体
（`docker compose build app` 后 `up -d`）；Windows 本地无 ffmpeg 时端到端渲染跑不了（与发布同限制），
但纯函数/mock 测试可跑。

### 6.3 TTS 引擎注册表（可插拔，镜像 drivers / ai_models）

`video/engines/`：`register(code, engine)` + `base.py` 的 `TtsEngine` Protocol（`synthesize(text) -> bytes+duration`）。

- 默认 `edge-tts`：微软免费端点，**无需 key** → 真零配置。
- 可选 `volcano`（火山引擎）等：配了对应 env key 才启用，音色更好。
- 密钥永不入 storyboard / 不回传；从 `os.environ` 取，缺失回落 edge-tts。

## 7. Loop 生态（镜像 `/goal`）

### 7.1 配方 `claude-loops/video-loop.md`

结构（同现有配方）：你是谁 / 可用工具 / 流程伪码 / 停止条件 / 注意事项。流程伪码：

```
拉候选文章（list_today_loop_articles 或 list_articles review_status=approved）
for 每篇:
    get_article 读正文
    自己写 storyboard（切分镜 + 逐段文案 + list_stock_images 点名 asset_id + 标题/描述/标签）
    compose_video(article_id, storyboard) → job_id
    轮询 get_video_status 到 done/failed
    done → 记录产物 URL；failed → 换图/重试一次
飞书播报（notify_feishu）本轮产出 N 个视频
```

### 7.2 Skill `geo-video-composer`

等价于 `geo-article-writer`：读一篇文章 → 写 storyboard → 调 `compose_video` → 轮询 →
返回产物。可选一个 orchestrator skill 做批量（"给这 10 篇已审核文章都配视频"）。

### 7.3 定位

**独立 loop、消费已产出/已审核文章**（镜像 `distribute-loop` 消费已审核文章的模式），
对新老文章都能配视频；不与生文 loop 强耦合。

## 8. GEO 命题的答案（为什么这条路对"被豆包引用"最优）

- 豆包 = 字节系，视频语料高度偏向**抖音/西瓜**，其次视频号喂微信搜一搜。目标平台优先字节生态。
- AI 引擎主要吃**文本**：标题、描述、ASR 转写、画面烧录字幕。而我们**配音稿=字幕=转写=文章原文
  三位一体**，天然对齐，不用靠平台 ASR 猜 → 视频对引擎的文本可读性天然最优。
- 因此把 **SRT + 结构化标题/描述/标签**当一等产物输出，人工上传时直接粘贴，引擎拿到高质量结构化文本。

## 9. 测试

- `server/tests/test_video_*.py`（`@pytest.mark.mysql`，用 `build_test_app`）：
  - storyboard 结构校验 / `asset_id` 归属校验的正反用例。
  - `compose_video` → `VideoJob` 落库、状态流转（mock TTS + mock ffmpeg，不在 CI 真跑二进制）。
  - `list_stock_images` 分页 / 类别过滤。
  - MCP token 鉴权边界（无 token 401）。
- TTS/ffmpeg 真实渲染只在容器/手动验证，CI 用 stub（和发布驱动测试同思路 monkeypatch）。

## 10. 分期

- **M1（本设计交付）**：`video/` 模块 + 3 个 MCP tool + `VideoJob` 迁移 + edge-tts 引擎 +
  ffmpeg 渲染 + `video-loop.md` 配方 + `geo-video-composer` skill。真零配置端到端跑通。
- **M2（fast-follow，另开 spec）**：Web 文章详情页"生成视频"按钮 + pipeline `compose_video` 节点，
  共用 `video/service.py`。
- **M3（可选）**：火山引擎等更优 TTS/音色；数字人 `video_engine`（路线 B）。

## 11. 已解决的设计选择

| 岔路 | 决定 |
|---|---|
| 核心目的 | GEO 露出/被 AI 引擎引用 |
| 视频形态 | 路线 A：图文轮播 + TTS + 烧录字幕（非数字人/非文生视频） |
| 自动化边界 | 只产出视频资产（mp4+SRT+元数据），发布人工 |
| 画面素材 | 复用图库逐段选图 |
| 选图智能放哪 | Claude 点名（`list_stock_images` + storyboard 指定 asset_id），GEO 全程不调 LLM |
| 触发方式 | Claude Code loop + MCP tools（镜像 /goal 生文 loop），非 Web UI |
| 同步/异步 | 异步（job_id + get_video_status 轮询），TTS+ffmpeg 慢 |
| TTS 默认 | edge-tts（无 key，真零配置），火山等可选升级 |
