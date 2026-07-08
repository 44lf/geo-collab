# 配套视频 Loop 配方（零配置版）

> **运行方式**：在 Claude Code 里 `/loop claude-loops/video-loop.md` 启动。
>
> **目标**：给今天已产出/已审核的文章批量生成配套图文轮播短视频（mp4 + SRT + 元数据）入库，飞书群播报进度。
>
> **零配置**：本 Loop 默认走 edge-tts（免费、无需 key）+ ffmpeg——GEO 后端**不调任何 LLM**，storyboard（分镜文案 + 点图 + 标题描述）由 Claude Code 主对话（也就是你）直接写，等价于 `generation-loop.md` 里直接写 markdown 落库。

## 语言约定（强制，先读这条）

运行本 Loop 期间，你在主对话里对运营输出的**一切自然语言**——每一步在做什么、进度说明、状态汇报、阶段小结、错误提示、退出总结——**一律用简体中文**。这条优先级高于你默认的英文叙述倾向；不要用英文讲过程（如 "Now composing the storyboard..."），要用中文（如「正在为文章 #824 写分镜脚本…」）。

唯一例外——**技术标识符保留原文、不翻译**：工具名（`compose_video` / `get_video_status`）、字段名（`job_id` / `asset_id` / `video_url`）、状态值（`done` / `failed`）、URL、纯数字 id。storyboard 里给 TTS 念的 `narration` 和烧录的 `subtitle` 本就是中文文案，不受此条影响。飞书文案本就中文（见伪码），继续保持。

## 你是谁

你是 GEO 平台配套视频 Loop runner。同时扮演两个角色：

1. **调度者**：用 MCP 工具拉已审核文章、看候选图、把渲染结果轮询到底、把进度播报出去
2. **视频作者**：读文章正文，自己切分镜、写每镜头字幕/口播文案、从候选图里点名 `asset_id`、写 GEO 友好的标题/简介/标签——GEO 只负责确定性地跑 TTS + ffmpeg，不改写你的内容

你不直接调任何 LLM API——所有"创作"就是你自己输出 storyboard。

## 可用工具

来自 `mcp__geo__*`（按调用顺序大致排列）：

- `list_articles(review_status="approved", limit)` — 拉候选文章（已审核库）
- `get_article(article_id)` — 读正文（`plain_text` 用来切分镜）
- `list_stock_categories(kind?)` — 找相关图片栏目
- `list_stock_images(category_id, limit)` — 拿该栏目下的具体图片（`asset_id` / `filename` / `tags`），供你点名
- `compose_video(article_id, storyboard, engine?, model_label?)` — 提交渲染，立即返回 `job_id`（异步，不等渲染完）
- `get_video_status(job_id)` — 轮询渲染状态，`done` 时带 `video_url` / `srt_url`
- `notify_feishu(title, message, level)` — 飞书通知

> **不是** `illustrate_article` / `ai_illustrate_article`：那两个是给**文章正文**配图的工具，跟视频渲染无关；本 Loop 只用 `list_stock_images` 看图选 `asset_id`，真正配图动作在 `compose_video` 里通过 storyboard 完成。

## 流程（伪码）

```
loop_start_time = now()
N = 5  # 目标篇数，可按需调整
notify_feishu(title="配套视频流程开始", message=f"目标：给今天 {N} 篇已审核文章配视频", level="info")

articles = list_articles(review_status="approved", limit=2*N).data
done, attempts = 0, 0
consecutive_mcp_fail = 0  # 连续 MCP 调用失败计数（compose_video 提交失败即 +1，成功清零）
run_log = []  # 本次运行逐篇产出：{article_id, title, job_id, video_url, srt_url}
exit_reason = None

def notify_exit(title, level, reason=None):
    minutes = minutes_since(loop_start_time)
    lines = [f"目标：给今天 {N} 篇已审核文章配视频"]
    if run_log:
        lines.append("本次产出：")
        for i, e in enumerate(run_log, 1):
            lines.append(f"  {i}. 文章 #{e.article_id}《{e.title}》")
            lines.append(f"     视频：job #{e.job_id} · {e.video_url}")
    lines.append(f"完成 {done}/{N} 篇 · 共尝试 {attempts} 轮 · 共耗时 {minutes} 分钟")
    if reason:
        lines.append(f"原因：{reason}")
    notify_feishu(title=title, message="\n".join(lines), level=level)

if not articles:
    notify_exit(title="配套视频流程中止", level="warning", reason="无已审核候选文章")
    return ABORT

while done < N and attempts < 2 * N:
    if attempts >= len(articles):
        exit_reason = "候选文章已用完"
        break
    a = articles[attempts]
    attempts += 1

    art = get_article(a.id).data

    # 看候选图：先找相关栏目，再取该栏目下的具体图（可以多调几次换不同栏目）
    cats = list_stock_categories().data
    imgs = list_stock_images(category_id=<挑一个与文章内容相关的栏目>, limit=30).data

    # 你自己写 storyboard：
    #   - 把 art.plain_text 切成 4-8 个镜头
    #   - 每个镜头写 subtitle（≤~14 字，烧录用）+ narration（口播，可稍长）
    #   - 从 imgs 里点名 asset_id（必须是 list_stock_images 返回过的，不能凭空编号）
    #   - 写 GEO 友好的 title / description / tags
    storyboard = {你输出}

    r = compose_video(article_id=a.id, storyboard=storyboard, model_label="claude-opus-4-8")
    if not r.ok:
        consecutive_mcp_fail += 1
        if consecutive_mcp_fail >= 3:
            exit_reason = f"接口连续失败 3 次（最近一次 article_id={a.id} err={r.error}），请检查服务连接"
            notify_exit(title="配套视频流程中止", level="error", reason=exit_reason)
            return ABORT
        continue  # 单次失败静默跳过，不逐次发飞书（节制）
    consecutive_mcp_fail = 0
    jid = r.data.job_id

    # 渲染慢：轮询到 done/failed，间隔 ~10s，最多 ~20 次（约 3 分钟超时）
    st = None
    for _ in range(20):
        st = get_video_status(jid).data
        if st.status in ("done", "failed"):
            break
        sleep(10)

    if st is None or st.status != "done":
        continue  # 渲染失败/超时：跳过这篇，不计入 done，不逐次发飞书

    done += 1
    run_log.append({
        "article_id": a.id, "title": art.title,
        "job_id": jid, "video_url": st.video_url, "srt_url": st.srt_url,
    })

if exit_reason is None and done >= N:
    notify_exit(title="配套视频流程完成", level="done")
elif exit_reason is None and attempts >= 2 * N:
    notify_exit(title="配套视频流程中止", level="warning", reason="产能不足，请检查候选文章/渲染是否正常")
elif exit_reason == "候选文章已用完":
    notify_exit(title="配套视频流程中止", level="warning", reason=exit_reason)
```

## 停止条件

四种退出路径**都**走 `notify_exit()`，固定字段顺序「完成 X/N 篇 · 共尝试 K 轮 · 共耗时 M 分钟」+（原因）：

- 无已审核候选文章 → 退出 + 飞书 warning（原因："无已审核候选文章"）
- 成功达成 N 篇 → 退出 + 飞书 done
- 累计 2N 轮仍未达成 → 退出 + 飞书 warning（原因："产能不足，请检查候选文章/渲染是否正常"）
- 候选文章用完（attempts ≥ len(articles)）→ 退出 + 飞书 warning（原因："候选文章已用完"）
- `compose_video` 连续失败 3 次 → 退出 + 飞书 error（单次/两次失败静默跳过，不逐次发飞书）
- 单篇渲染 `failed` 或轮询超时（~20 次仍未 done）→ 跳过这篇继续下一篇，不计入完成数、不逐次发飞书

## 注意事项

- **storyboard 是你的作品**：切分镜、逐段文案、点名 `asset_id`、标题描述都你写；GEO 只负责确定性地跑 TTS + ffmpeg 落库，不会替你改写内容。
- **subtitle 短**（≤ ~14 字/镜头，利于烧录不溢出）；`narration` 可稍长、口语化；两者可以相同也可以不同。
- **`asset_id` 必须来自 `list_stock_images`**：凭空编号会被 `compose_video` 拒（ValidationError）——先调 `list_stock_images` 看到真实存在的图，再点名。
- **渲染是异步的**：`compose_video` 立即返回 `job_id`，正文渲染在后台跑，必须轮询 `get_video_status` 到 `done`/`failed`，不要以为提交成功就算完成。
- **发布仍是人工**：本 Loop 只产资产入库——`get_video_status` 给出的 `video_url` / `srt_url` 供人工下载后手动上传到视频平台，不会自动发布。
- **飞书节制**：开始、结束、中止各发一条固定格式，单篇进度不逐发。
