# 文章 → 配套视频（Claude Code Loop 版）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Claude Code 主对话把一篇 GEO 文章变成一个可发布的图文轮播短视频资产（mp4 + SRT + 标题/描述/标签），全程通过 GEO MCP 工具、GEO 后端不调任何 LLM。

**Architecture:** 新增 `server/app/modules/video/` 自包含模块，暴露 3 个 MCP-token 端点。核心 `compose_video_job(job_id)` 是确定性 pipeline：Claude 写的 storyboard（分镜 JSON）→ edge-tts 逐段配音 → 复用图库 MinIO 取图 → ffmpeg（Ken Burns + 烧录字幕 + BGM）合成 mp4 → 连同精确 SRT 存 MinIO，写 `VideoJob` 行。异步执行沿用 `bg_session_factory` 后台线程（和 `ai_format` / 方案运行同款，无独立 worker）。3 个新 MCP tool（`compose_video` / `get_video_status` / `list_stock_images`）薄封装这些端点。

**Tech Stack:** FastAPI + SQLAlchemy/Alembic（MySQL）、MinIO（复用 image_library store）、edge-tts（无 key 免费 TTS）、ffmpeg（系统二进制，容器内）、FastMCP。

## Global Constraints

从 spec（`docs/superpowers/specs/2026-07-07-article-to-video-loop-design.md`）与 `CLAUDE.md` 提炼，每个 task 隐含遵守：

- **GEO 后端全程不调 LLM。** 所有"创作智能"（切分镜/写文案/选图/标题描述）都在 Claude 主对话。`compose_video` 之于视频，等价于 `save_article` 之于文章。
- **真零配置默认链路**：默认 TTS 引擎 `edge-tts` 无需任何外部 key；缺 key 才回落它。ffmpeg 免费。
- **MySQL only**；测试需 `GEO_TEST_DATABASE_URL`（DB 名含 `test`），用 `build_test_app(monkeypatch)`，`finally` 里 `test_app.cleanup()`，测试类打 `@pytest.mark.mysql`。
- **MCP 端点鉴权**：`dependencies=[Depends(require_mcp_token)]`，**不复用 user JWT**。未捕获异常一律走 `server.app.core.mcp_errors.mcp_exception_response(exc, context=...)`，不抛裸 Exception。
- **service 层抛命名异常**（`ClientError` / `ConflictError` / `ValidationError`），**不抛裸 `ValueError`**（无全局兜底）。
- **DB session 非线程安全**：后台线程内自建 session（`SessionLocal`），所有 flush/commit/refresh 在该线程内完成。
- **MCP tool 一律 `async def`** + 阻塞 HTTP 调用经 `anyio.to_thread.run_sync` 丢线程池（见 `catalog.py` 模块 docstring 的自调用死锁说明）。
- **MCP_TOOLS_COUNT 唯一真值**在 `server/app/modules/mcp_catalog/connect_router.py`：本计划把 21 改为 24。
- **ffmpeg 运行环境**：async job 跑在 app 容器（`Dockerfile.app`）后台线程，故 ffmpeg + CJK 字体必须装进 `Dockerfile.app`。Windows 本地无 ffmpeg 时端到端渲染跑不了（与发布同限制），但纯函数/mock 测试可本地跑。
- **ruff/mypy 门禁**：line-length=100，选 E/F/I/B/UP，`from __future__ import annotations` 打头，类型标注齐全。
- **Alembic 版本号不写死**：迁移 `down_revision` 取 `server/alembic/versions/` 当前最新一个文件的 revision，`alembic upgrade head` 后跑测试。

---

### Task 1: 依赖与系统包（ffmpeg / CJK 字体 / edge-tts）+ 二进制解析

给 app 镜像装 ffmpeg 与中文字体、给 Python 装 edge-tts；再写一个可测的二进制路径解析函数（尊重 `GEO_FFMPEG_PATH` / `GEO_FFPROBE_PATH` 覆盖），后续 ffmpeg 调用都经它。

**Files:**
- Modify: `requirements.txt`（追加 `edge-tts`）
- Modify: `Dockerfile.app`（`apt-get install ffmpeg fonts-noto-cjk`）
- Create: `server/app/modules/video/__init__.py`
- Create: `server/app/modules/video/binaries.py`
- Test: `server/tests/test_video_binaries.py`

**Interfaces:**
- Produces:
  - `video.binaries.ffmpeg_binary() -> str`（返回 ffmpeg 可执行路径，默认 `"ffmpeg"`，被 `GEO_FFMPEG_PATH` 覆盖）
  - `video.binaries.ffprobe_binary() -> str`（同理，默认 `"ffprobe"`，被 `GEO_FFPROBE_PATH` 覆盖）
  - `video.binaries.cjk_font_path() -> str`（烧录字幕用字体文件路径，默认 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`，被 `GEO_VIDEO_FONT_PATH` 覆盖）

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_binaries.py`：

```python
from __future__ import annotations

import server.app.modules.video.binaries as b


def test_ffmpeg_binary_default(monkeypatch):
    monkeypatch.delenv("GEO_FFMPEG_PATH", raising=False)
    assert b.ffmpeg_binary() == "ffmpeg"


def test_ffmpeg_binary_env_override(monkeypatch):
    monkeypatch.setenv("GEO_FFMPEG_PATH", "/opt/bin/ffmpeg")
    assert b.ffmpeg_binary() == "/opt/bin/ffmpeg"


def test_ffprobe_binary_env_override(monkeypatch):
    monkeypatch.setenv("GEO_FFPROBE_PATH", "/opt/bin/ffprobe")
    assert b.ffprobe_binary() == "/opt/bin/ffprobe"


def test_cjk_font_path_env_override(monkeypatch):
    monkeypatch.setenv("GEO_VIDEO_FONT_PATH", "/fonts/my.ttf")
    assert b.cjk_font_path() == "/fonts/my.ttf"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_binaries.py -q`
Expected: FAIL（`ModuleNotFoundError: server.app.modules.video`）

- [ ] **Step 3: 实现**

`server/app/modules/video/__init__.py`：

```python
"""视频生成模块：文章 → 图文轮播短视频（mp4 + SRT + 元数据）。

Claude 主对话写 storyboard，本模块确定性地跑 TTS + ffmpeg 落库，全程不调 LLM。
"""
```

`server/app/modules/video/binaries.py`：

```python
"""ffmpeg / ffprobe / 字体路径解析。集中一处，便于测试与容器外覆盖。"""

from __future__ import annotations

import os

_DEFAULT_CJK_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def ffmpeg_binary() -> str:
    return os.environ.get("GEO_FFMPEG_PATH") or "ffmpeg"


def ffprobe_binary() -> str:
    return os.environ.get("GEO_FFPROBE_PATH") or "ffprobe"


def cjk_font_path() -> str:
    return os.environ.get("GEO_VIDEO_FONT_PATH") or _DEFAULT_CJK_FONT
```

`requirements.txt` 末尾追加一行：

```
edge-tts
```

`Dockerfile.app` 在 `WORKDIR /app` 之后、`COPY requirements.txt .` 之前插入（装 ffmpeg + Noto CJK 字体，用于烧录中文字幕）：

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_video_binaries.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add requirements.txt Dockerfile.app server/app/modules/video/__init__.py \
        server/app/modules/video/binaries.py server/tests/test_video_binaries.py
git commit -m "feat(video): 模块骨架 + ffmpeg/字体依赖 + 二进制路径解析"
```

---

### Task 2: VideoJob 模型 + Alembic 迁移

存视频异步任务的状态机与产物引用。产物 mp4/srt 以 MinIO 对象 key 记录（不复用 Asset，避免其 article-attachment 语义耦合）。

**Files:**
- Create: `server/app/modules/video/models.py`
- Create: `server/alembic/versions/<new>_create_video_jobs.py`
- Modify: `server/app/main.py`（顶部 import 触发模型注册到 Base.metadata —— 找到现有各模块 models 的 import 区，追加一行）
- Test: `server/tests/test_video_model.py`

**Interfaces:**
- Produces: ORM `video.models.VideoJob`，字段：
  - `id: int`（PK）
  - `job_id: str`（uuid hex，unique index）
  - `article_id: int`（FK articles.id, index）
  - `status: str`（`pending` / `running` / `done` / `failed`，默认 `pending`）
  - `progress: float`（0..1，默认 0）
  - `storyboard: dict`（JSON 列，Claude 传入的原始 storyboard）
  - `engine: str | None`（TTS 引擎 code，None=默认）
  - `video_key: str | None` / `srt_key: str | None`（MinIO 对象 key）
  - `title: str | None` / `description: str | None`（≤ 长度见下）/ `tags: list | None`（JSON）
  - `error: str | None`
  - `created_at / updated_at: datetime`

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_model.py`：

```python
from __future__ import annotations

import uuid

import pytest

from server.app.modules.video.models import VideoJob


@pytest.mark.mysql
def test_video_job_insert_and_query(build_test_app_fixture):
    # build_test_app_fixture 见 conftest：返回 (test_app, session_factory)
    test_app, SessionLocal = build_test_app_fixture
    try:
        db = SessionLocal()
        try:
            jid = uuid.uuid4().hex
            job = VideoJob(
                job_id=jid,
                article_id=1,
                storyboard={"title": "t", "shots": []},
                status="pending",
                progress=0.0,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            assert job.id is not None
            assert job.status == "pending"
            fetched = db.query(VideoJob).filter(VideoJob.job_id == jid).one()
            assert fetched.storyboard == {"title": "t", "shots": []}
        finally:
            db.close()
    finally:
        test_app.cleanup()
```

> 注：`build_test_app_fixture` 需在 `server/tests/conftest.py` 里存在。若无同名 fixture，参考现有测试里 `build_test_app(monkeypatch)` 的用法内联构造（见 `test_articles_api.py` 头部），本步骤按现有约定调整调用形态即可——关键是断言 `VideoJob` 能建表、增查。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_model.py -q`
Expected: FAIL（`ModuleNotFoundError`：`video.models`）

- [ ] **Step 3: 实现模型**

`server/app/modules/video/models.py`：

```python
"""视频生成任务 ORM。产物 mp4/srt 以 MinIO 对象 key 记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    storyboard: Mapped[dict] = mapped_column(JSON, nullable=False)
    engine: Mapped[str | None] = mapped_column(String(50), nullable=True)
    video_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    srt_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
```

在 `server/app/main.py` 现有各模块 `models` import 区（与其它 `from server.app.modules.*.models import ...` 相邻）追加，确保建表期模型已注册到 `Base.metadata`：

```python
import server.app.modules.video.models  # noqa: F401  (register VideoJob table)
```

- [ ] **Step 4: 生成并写迁移**

先看当前最新迁移取其 revision 作 `down_revision`：

Run: `ls server/alembic/versions/`（挑最新一个文件，读它的 `revision = "xxxx"`）

`server/alembic/versions/<new>_create_video_jobs.py`（把 `down_revision` 换成上面查到的最新 revision）：

```python
"""create video_jobs

Revision ID: v1create_video_jobs
Revises: <LATEST_REVISION>
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v1create_video_jobs"
down_revision = "<LATEST_REVISION>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("storyboard", sa.JSON(), nullable=False),
        sa.Column("engine", sa.String(length=50), nullable=True),
        sa.Column("video_key", sa.String(length=500), nullable=True),
        sa.Column("srt_key", sa.String(length=500), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_video_jobs_job_id", "video_jobs", ["job_id"], unique=True)
    op.create_index("ix_video_jobs_article_id", "video_jobs", ["article_id"])


def downgrade() -> None:
    op.drop_index("ix_video_jobs_article_id", table_name="video_jobs")
    op.drop_index("ix_video_jobs_job_id", table_name="video_jobs")
    op.drop_table("video_jobs")
```

Run: `alembic upgrade head`
Expected: 迁移成功（`Running upgrade ... -> v1create_video_jobs`）

- [ ] **Step 5: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test pytest server/tests/test_video_model.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add server/app/modules/video/models.py server/alembic/versions/ \
        server/app/main.py server/tests/test_video_model.py
git commit -m "feat(video): VideoJob 模型 + video_jobs 迁移"
```

---

### Task 3: Storyboard schema + 校验

Claude 传入的 storyboard 走 Pydantic 结构校验；`asset_id` 归属校验（图必须在图库里）单独一个纯函数，便于 service 与端点复用。

**Files:**
- Create: `server/app/modules/video/schemas.py`
- Test: `server/tests/test_video_schemas.py`

**Interfaces:**
- Consumes: `image_library.models.StockImage`（校验 asset_id 归属）
- Produces:
  - `video.schemas.Shot`（`subtitle: str`, `narration: str`, `asset_id: int | None = None`, `duration_hint: float | None = None`）
  - `video.schemas.Storyboard`（`title: str`, `description: str = ""`, `tags: list[str] = []`, `aspect_ratio: Literal["9:16","16:9"] = "9:16"`, `bgm: Literal["default","none"] = "default"`, `shots: list[Shot]` 至少 1 个）
  - `video.schemas.validate_asset_ids(db, storyboard) -> None`（任一 `asset_id` 不存在 → 抛 `ValidationError`）
  - `video.schemas.ComposeVideoRequest`（`article_id: int`, `storyboard: Storyboard`, `engine: str | None = None`, `model_label: str | None = None`）

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_schemas.py`：

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError as PydValidationError

from server.app.modules.video.schemas import Shot, Storyboard


def test_storyboard_minimal_ok():
    sb = Storyboard(title="标题", shots=[Shot(subtitle="第一段", narration="第一段口播")])
    assert sb.aspect_ratio == "9:16"
    assert sb.bgm == "default"
    assert sb.description == ""
    assert sb.tags == []
    assert len(sb.shots) == 1


def test_storyboard_rejects_empty_shots():
    with pytest.raises(PydValidationError):
        Storyboard(title="标题", shots=[])


def test_storyboard_rejects_bad_aspect_ratio():
    with pytest.raises(PydValidationError):
        Storyboard(title="标题", aspect_ratio="1:1", shots=[Shot(subtitle="a", narration="a")])


def test_shot_asset_id_optional():
    s = Shot(subtitle="a", narration="a")
    assert s.asset_id is None
    assert s.duration_hint is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_schemas.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现**

`server/app/modules/video/schemas.py`：

```python
"""Storyboard 结构校验（Claude 写、GEO 渲染的契约）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.app.modules.image_library.models import StockImage
from server.app.shared.errors import ValidationError


class Shot(BaseModel):
    subtitle: str = Field(min_length=1, max_length=200)
    narration: str = Field(min_length=1, max_length=1000)
    asset_id: int | None = None
    duration_hint: float | None = Field(default=None, ge=0.5, le=30.0)


class Storyboard(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list)
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    bgm: Literal["default", "none"] = "default"
    shots: list[Shot] = Field(min_length=1, max_length=30)


class ComposeVideoRequest(BaseModel):
    article_id: int
    storyboard: Storyboard
    engine: str | None = None
    model_label: str | None = None


def validate_asset_ids(db: Session, storyboard: Storyboard) -> None:
    """任一 shot.asset_id 指向的图不存在 → ValidationError（service 层命名异常）。"""
    wanted = {s.asset_id for s in storyboard.shots if s.asset_id is not None}
    if not wanted:
        return
    found = {
        row[0] for row in db.query(StockImage.id).filter(StockImage.id.in_(wanted)).all()
    }
    missing = wanted - found
    if missing:
        raise ValidationError(f"storyboard 引用了不存在的图片 asset_id: {sorted(missing)}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_video_schemas.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/video/schemas.py server/tests/test_video_schemas.py
git commit -m "feat(video): storyboard schema + asset_id 归属校验"
```

---

### Task 4: SRT 构建（纯函数）

镜头列表 + 每镜头时长 → 标准 SRT 字符串。纯函数，精确 TDD。

**Files:**
- Create: `server/app/modules/video/srt.py`
- Test: `server/tests/test_video_srt.py`

**Interfaces:**
- Produces: `video.srt.build_srt(cues: list[tuple[str, float]]) -> str`
  - 入参：`[(subtitle_text, duration_seconds), ...]`，按顺序累加时间轴
  - 出参：标准 SRT（`\n` 换行，序号从 1，时间戳 `HH:MM:SS,mmm`）

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_srt.py`：

```python
from __future__ import annotations

from server.app.modules.video.srt import build_srt


def test_build_srt_two_cues():
    out = build_srt([("第一段", 2.5), ("第二段", 3.0)])
    assert out == (
        "1\n"
        "00:00:00,000 --> 00:00:02,500\n"
        "第一段\n"
        "\n"
        "2\n"
        "00:00:02,500 --> 00:00:05,500\n"
        "第二段\n"
    )


def test_build_srt_empty():
    assert build_srt([]) == ""


def test_build_srt_hour_rollover():
    out = build_srt([("x", 3661.0)])  # 1h 1m 1s
    assert "00:00:00,000 --> 01:01:01,000" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_srt.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现**

`server/app/modules/video/srt.py`：

```python
"""SRT 字幕构建：镜头文案 + 时长 → 标准 SRT。纯函数。"""

from __future__ import annotations


def _fmt_ts(seconds: float) -> str:
    ms_total = int(round(seconds * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(cues: list[tuple[str, float]]) -> str:
    """cues: [(text, duration_seconds), ...]，按顺序累加时间轴生成 SRT。"""
    lines: list[str] = []
    cursor = 0.0
    for idx, (text, dur) in enumerate(cues, start=1):
        start = cursor
        end = cursor + dur
        cursor = end
        lines.append(str(idx))
        lines.append(f"{_fmt_ts(start)} --> {_fmt_ts(end)}")
        lines.append(text)
        lines.append("")  # cue 之间空行
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_video_srt.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/video/srt.py server/tests/test_video_srt.py
git commit -m "feat(video): SRT 构建纯函数"
```

---

### Task 5: TTS 引擎注册表 + edge-tts 引擎

镜像 `drivers/` / `ai_models/` 的注册表模式，引擎可插拔；默认 edge-tts 无 key。

**Files:**
- Create: `server/app/modules/video/engines/__init__.py`
- Create: `server/app/modules/video/engines/base.py`
- Create: `server/app/modules/video/engines/edge.py`
- Test: `server/tests/test_video_engines.py`

**Interfaces:**
- Produces:
  - `video.engines.base.TtsEngine`（Protocol：`code: str`；`synthesize(self, text: str) -> bytes` 返回 mp3 字节）
  - `video.engines.base.register(engine)` / `get_engine(code: str | None) -> TtsEngine`（`None` → 默认 `edge`；未知 code → 抛 `ClientError`）
  - `video.engines.edge.EdgeTtsEngine`（`code="edge"`，`synthesize` 用 edge-tts 合成中文语音 mp3）

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_engines.py`：

```python
from __future__ import annotations

import pytest

from server.app.modules.video import engines
from server.app.shared.errors import ClientError


def test_default_engine_is_edge():
    eng = engines.get_engine(None)
    assert eng.code == "edge"


def test_unknown_engine_raises():
    with pytest.raises(ClientError):
        engines.get_engine("nope-not-real")


def test_edge_synthesize_mocked(monkeypatch):
    # 不打真实网络：mock EdgeTtsEngine 内部的合成实现，只验证契约（返回 bytes）
    from server.app.modules.video.engines import edge

    monkeypatch.setattr(edge, "_synthesize_bytes", lambda text, voice: b"ID3fake-mp3")
    eng = engines.get_engine("edge")
    out = eng.synthesize("你好世界")
    assert isinstance(out, bytes)
    assert out == b"ID3fake-mp3"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_engines.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现**

`server/app/modules/video/engines/base.py`：

```python
"""TTS 引擎注册表（可插拔，镜像 tasks/drivers）。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from server.app.shared.errors import ClientError

_DEFAULT_CODE = "edge"
_REGISTRY: dict[str, "TtsEngine"] = {}


@runtime_checkable
class TtsEngine(Protocol):
    code: str

    def synthesize(self, text: str) -> bytes:
        """把文本合成为 mp3 字节。失败抛异常（由调用方翻译）。"""
        ...


def register(engine: "TtsEngine") -> None:
    _REGISTRY[engine.code] = engine


def get_engine(code: str | None) -> "TtsEngine":
    resolved = code or _DEFAULT_CODE
    engine = _REGISTRY.get(resolved)
    if engine is None:
        raise ClientError(f"未知 TTS 引擎: {resolved}（可用: {sorted(_REGISTRY)}）")
    return engine
```

`server/app/modules/video/engines/edge.py`：

```python
"""edge-tts 引擎：微软免费端点，无需 key（真零配置默认引擎）。"""

from __future__ import annotations

import asyncio
import os

from server.app.modules.video.engines.base import register

_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def _synthesize_bytes(text: str, voice: str) -> bytes:
    """调 edge-tts 合成 mp3。edge_tts.Communicate 是 async，这里包一层同步执行。

    单独抽出便于测试 monkeypatch（不打真实网络）。
    """
    import edge_tts  # 懒导入：模块加载期不拉依赖

    async def _run() -> bytes:
        chunks: list[bytes] = []
        communicate = edge_tts.Communicate(text, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    return asyncio.run(_run())


class EdgeTtsEngine:
    code = "edge"

    def synthesize(self, text: str) -> bytes:
        voice = os.environ.get("GEO_VIDEO_TTS_VOICE") or _DEFAULT_VOICE
        return _synthesize_bytes(text, voice)


register(EdgeTtsEngine())
```

`server/app/modules/video/engines/__init__.py`（导入触发注册，转出 API）：

```python
"""TTS 引擎包：导入即注册各引擎。"""

from __future__ import annotations

from server.app.modules.video.engines import edge  # noqa: F401  (register edge)
from server.app.modules.video.engines.base import TtsEngine, get_engine, register

__all__ = ["TtsEngine", "get_engine", "register"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_video_engines.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/video/engines/ server/tests/test_video_engines.py
git commit -m "feat(video): TTS 引擎注册表 + edge-tts 默认引擎"
```

---

### Task 6: ffmpeg 合成 + ffprobe 时长探测

把"逐镜头（图 + 音频 + 烧录字幕）→ 拼接 → mp4"落成可执行代码。命令构建拆成纯函数（可测），执行经 subprocess（mock 测）。

**Files:**
- Create: `server/app/modules/video/ffmpeg_compose.py`
- Test: `server/tests/test_video_ffmpeg.py`

**Interfaces:**
- Consumes: `video.binaries.ffmpeg_binary / ffprobe_binary / cjk_font_path`
- Produces:
  - `video.ffmpeg_compose.probe_duration(audio_path: str) -> float`（ffprobe 读音频秒数）
  - `video.ffmpeg_compose.wrap_subtitle(text: str, max_chars: int = 14) -> str`（按字数折行，返回含 `\n` 的多行文本）
  - `video.ffmpeg_compose.build_shot_command(*, image_path, audio_path, out_path, duration, subtitle, width, height, font_path) -> list[str]`（单镜头 ffmpeg 参数表：缩放铺满 + Ken Burns zoompan + drawtext 烧录字幕 + 音频）
  - `video.ffmpeg_compose.build_concat_command(*, list_file, out_path) -> list[str]`（concat demuxer 拼接）
  - `video.ffmpeg_compose.run(cmd: list[str]) -> None`（执行，非 0 退出抛 `ClientError`）

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_ffmpeg.py`：

```python
from __future__ import annotations

import pytest

from server.app.modules.video import ffmpeg_compose as fc
from server.app.shared.errors import ClientError


def test_wrap_subtitle_breaks_by_chars():
    assert fc.wrap_subtitle("一二三四五六七八九十", max_chars=4) == "一二三四\n五六七八\n九十"


def test_wrap_subtitle_short_unchanged():
    assert fc.wrap_subtitle("短句", max_chars=14) == "短句"


def test_build_shot_command_has_core_flags():
    cmd = fc.build_shot_command(
        image_path="/tmp/a.jpg",
        audio_path="/tmp/a.mp3",
        out_path="/tmp/a.mp4",
        duration=3.0,
        subtitle="字幕",
        width=1080,
        height=1920,
        font_path="/f.ttf",
    )
    assert cmd[0].endswith("ffmpeg")
    assert "/tmp/a.jpg" in cmd
    assert "/tmp/a.mp3" in cmd
    assert cmd[-1] == "/tmp/a.mp4"
    # 滤镜串里应包含缩放/zoompan/drawtext 关键片段
    vf = " ".join(cmd)
    assert "zoompan" in vf
    assert "drawtext" in vf
    assert "1080" in vf and "1920" in vf


def test_build_concat_command():
    cmd = fc.build_concat_command(list_file="/tmp/list.txt", out_path="/tmp/out.mp4")
    assert "concat" in cmd
    assert "/tmp/list.txt" in cmd
    assert cmd[-1] == "/tmp/out.mp4"


def test_run_raises_on_nonzero(monkeypatch):
    class _Proc:
        returncode = 1
        stderr = b"boom"

    monkeypatch.setattr(fc.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(ClientError):
        fc.run(["ffmpeg", "-x"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_ffmpeg.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现**

`server/app/modules/video/ffmpeg_compose.py`：

```python
"""ffmpeg 合成：逐镜头渲染 + concat 拼接。命令构建为纯函数，便于测试。

画质细节（zoompan 参数、字幕位置）在容器内 /verify 时按实际观感调，本文件给出可跑骨架。
"""

from __future__ import annotations

import json
import subprocess

from server.app.modules.video.binaries import ffmpeg_binary, ffprobe_binary
from server.app.shared.errors import ClientError


def probe_duration(audio_path: str) -> float:
    cmd = [
        ffprobe_binary(), "-v", "error", "-show_entries", "format=duration",
        "-of", "json", audio_path,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise ClientError(f"ffprobe 失败: {proc.stderr.decode('utf-8', 'ignore')[:300]}")
    data = json.loads(proc.stdout or b"{}")
    return float(data.get("format", {}).get("duration") or 0.0)


def wrap_subtitle(text: str, max_chars: int = 14) -> str:
    """按字数折行（中文按字符）。返回含 \\n 的多行文本。"""
    if len(text) <= max_chars:
        return text
    lines = [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
    return "\n".join(lines)


def _escape_drawtext(text: str) -> str:
    # drawtext text 需转义特殊字符
    return (
        text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
    )


def build_shot_command(
    *,
    image_path: str,
    audio_path: str,
    out_path: str,
    duration: float,
    subtitle: str,
    width: int,
    height: int,
    font_path: str,
) -> list[str]:
    """单镜头：图片循环成视频（时长=音频） + 缩放铺满 + Ken Burns + 烧录字幕 + 音频。"""
    wrapped = _escape_drawtext(wrap_subtitle(subtitle))
    fps = 30
    total_frames = max(1, int(round(duration * fps)))
    # 缩放到覆盖画布 → 裁剪 → zoompan 缓慢放大（Ken Burns）→ 底部 drawtext 字幕
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='min(zoom+0.0006,1.15)':d={total_frames}:s={width}x{height}:fps={fps},"
        f"drawtext=fontfile='{font_path}':text='{wrapped}':"
        f"fontcolor=white:fontsize=54:box=1:boxcolor=black@0.5:boxborderw=16:"
        f"x=(w-text_w)/2:y=h-text_h-160:line_spacing=12"
    )
    return [
        ffmpeg_binary(), "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        out_path,
    ]


def build_concat_command(*, list_file: str, out_path: str) -> list[str]:
    """concat demuxer 拼接逐镜头 mp4（list_file 为 ffconcat 清单）。"""
    return [
        ffmpeg_binary(), "-y",
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy",
        out_path,
    ]


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        stderr = getattr(proc, "stderr", b"") or b""
        raise ClientError(f"ffmpeg 失败: {stderr.decode('utf-8', 'ignore')[:500]}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_video_ffmpeg.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/video/ffmpeg_compose.py server/tests/test_video_ffmpeg.py
git commit -m "feat(video): ffmpeg 逐镜头合成 + concat + ffprobe 时长探测"
```

---

### Task 7: MinIO 视频存储 + service 编排（compose_video_job）

把 storyboard → TTS → 取图 → ffmpeg → 存 MinIO → 写 VideoJob 串起来。产物存独立 bucket。整段有 mock 依赖的集成测试。

**Files:**
- Create: `server/app/modules/video/store.py`
- Create: `server/app/modules/video/service.py`
- Test: `server/tests/test_video_service.py`

**Interfaces:**
- Consumes: `image_library.store`（`ensure_bucket` / `upload_image` / `get_object_bytes`）、`image_library.models.StockImage`、`articles.models.Article`（封面兜底）、`video.engines.get_engine`、`video.ffmpeg_compose`、`video.srt.build_srt`、`video.schemas`
- Produces:
  - `video.store.VIDEO_BUCKET`（常量 `"geo-videos"`）、`put_video(key, data) -> None`、`put_srt(key, data) -> None`、`get_object(key) -> bytes`
  - `video.service.create_video_job(db, req: ComposeVideoRequest) -> VideoJob`（校验 + 建 pending 行，返回；不启动渲染）
  - `video.service.run_video_job(job_id, session_factory) -> None`（后台线程入口：自建 session 跑整条 pipeline，更新状态/进度/产物 key/error）
  - `video.service.spawn_video_job(job_id) -> None`（起 daemon 线程调 run_video_job，用模块级 `bg_session_factory`）

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_service.py`：

```python
from __future__ import annotations

import pytest

from server.app.modules.video import service as vsvc
from server.app.modules.video.models import VideoJob
from server.app.modules.video.schemas import ComposeVideoRequest, Shot, Storyboard


@pytest.mark.mysql
def test_run_video_job_success(build_test_app_fixture, monkeypatch):
    test_app, SessionLocal = build_test_app_fixture
    try:
        # 造一篇文章 + 一张图库图（article_id/asset_id 用 helper 或直接建 ORM）
        db = SessionLocal()
        article_id, asset_id = _seed_article_and_image(db)  # 见下方 helper 约定
        db.close()

        # mock 掉真实 TTS / ffmpeg / MinIO
        monkeypatch.setattr(
            "server.app.modules.video.service.get_engine",
            lambda code: type("E", (), {"code": "edge", "synthesize": lambda self, t: b"mp3"})(),
        )
        monkeypatch.setattr(
            "server.app.modules.video.service.fc.probe_duration", lambda p: 3.0
        )
        monkeypatch.setattr("server.app.modules.video.service.fc.run", lambda cmd: None)
        # ffmpeg run 是 mock 的，产物文件不会真生成 → 读产物字节也 mock
        monkeypatch.setattr(
            "server.app.modules.video.service._read_file", lambda p: b"FAKEMP4"
        )
        stored = {}
        monkeypatch.setattr(
            "server.app.modules.video.store.put_video",
            lambda key, data: stored.__setitem__("video", (key, data)),
        )
        monkeypatch.setattr(
            "server.app.modules.video.store.put_srt",
            lambda key, data: stored.__setitem__("srt", (key, data)),
        )
        monkeypatch.setattr("server.app.modules.video.store.ensure_video_bucket", lambda: None)

        req = ComposeVideoRequest(
            article_id=article_id,
            storyboard=Storyboard(
                title="标题",
                description="描述",
                tags=["标签"],
                shots=[Shot(subtitle="第一段", narration="第一段口播", asset_id=asset_id)],
            ),
        )
        db = SessionLocal()
        job = vsvc.create_video_job(db, req)
        jid = job.job_id
        db.close()

        vsvc.run_video_job(jid, SessionLocal)

        db = SessionLocal()
        done = db.query(VideoJob).filter(VideoJob.job_id == jid).one()
        assert done.status == "done"
        assert done.progress == 1.0
        assert done.video_key and done.srt_key
        assert done.title == "标题"
        db.close()
        assert "video" in stored and "srt" in stored
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_run_video_job_failure_marks_failed(build_test_app_fixture, monkeypatch):
    test_app, SessionLocal = build_test_app_fixture
    try:
        db = SessionLocal()
        article_id, asset_id = _seed_article_and_image(db)
        db.close()

        def _boom(code):
            raise RuntimeError("tts down")

        monkeypatch.setattr("server.app.modules.video.service.get_engine", _boom)
        monkeypatch.setattr("server.app.modules.video.store.ensure_video_bucket", lambda: None)

        req = ComposeVideoRequest(
            article_id=article_id,
            storyboard=Storyboard(title="t", shots=[Shot(subtitle="a", narration="a", asset_id=asset_id)]),
        )
        db = SessionLocal()
        jid = vsvc.create_video_job(db, req).job_id
        db.close()

        vsvc.run_video_job(jid, SessionLocal)

        db = SessionLocal()
        failed = db.query(VideoJob).filter(VideoJob.job_id == jid).one()
        assert failed.status == "failed"
        assert failed.error
        db.close()
    finally:
        test_app.cleanup()
```

> `_seed_article_and_image(db)` 需返回 `(article_id, asset_id)`：建一条最小 `Article`（参考 `articles.service.create_article` 或直接 ORM）+ 一条 `StockCategory` + `StockImage`。若测试库已有 seed helper 就复用；否则在测试文件内用 ORM 直接插入并 commit。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_service.py -q`
Expected: FAIL（`ModuleNotFoundError`：`video.service`）

- [ ] **Step 3: 实现 store**

`server/app/modules/video/store.py`：

```python
"""视频产物 MinIO 存储：复用 image_library.store 的底层 client，独立 bucket。"""

from __future__ import annotations

from server.app.modules.image_library import store as minio_store

VIDEO_BUCKET = "geo-videos"


def ensure_video_bucket() -> None:
    minio_store.ensure_bucket(VIDEO_BUCKET)


def put_video(key: str, data: bytes) -> None:
    minio_store.upload_image(VIDEO_BUCKET, key, data, "video/mp4")


def put_srt(key: str, data: bytes) -> None:
    minio_store.upload_image(VIDEO_BUCKET, key, data, "application/x-subrip")


def get_object(key: str) -> bytes:
    return minio_store.get_object_bytes(VIDEO_BUCKET, key)
```

- [ ] **Step 4: 实现 service**

`server/app/modules/video/service.py`：

```python
"""视频生成 service：storyboard → TTS → 取图 → ffmpeg → MinIO → VideoJob。

后台线程执行（bg_session_factory），全程确定性、不调 LLM。
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import uuid

from sqlalchemy.orm import Session

from server.app.modules.articles.models import Article
from server.app.modules.image_library.models import StockCategory, StockImage
from server.app.modules.image_library import store as image_store
from server.app.modules.video import ffmpeg_compose as fc
from server.app.modules.video import store as video_store
from server.app.modules.video.engines import get_engine
from server.app.modules.video.models import VideoJob
from server.app.modules.video.schemas import ComposeVideoRequest, Storyboard, validate_asset_ids
from server.app.modules.video.srt import build_srt
from server.app.shared.errors import ClientError, ValidationError

logger = logging.getLogger(__name__)

# 由 create_app() 注入（与 scheme_router / pipelines.router 同款）
bg_session_factory = None

_DIMENSIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def create_video_job(db: Session, req: ComposeVideoRequest) -> VideoJob:
    """校验 + 建 pending 行（不渲染）。校验失败抛命名异常。"""
    article = db.get(Article, req.article_id)
    if article is None:
        raise ValidationError(f"文章不存在: {req.article_id}")
    validate_asset_ids(db, req.storyboard)
    job = VideoJob(
        job_id=uuid.uuid4().hex,
        article_id=req.article_id,
        storyboard=req.storyboard.model_dump(),
        engine=req.engine,
        status="pending",
        progress=0.0,
        title=req.storyboard.title,
        description=req.storyboard.description or None,
        tags=req.storyboard.tags or None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _load_image_bytes(db: Session, asset_id: int | None, article: Article) -> bytes:
    """按 asset_id 取图；空则文章封面兜底；再空抛错（MVP 不做纯色卡，先要求有图）。"""
    img: StockImage | None = None
    if asset_id is not None:
        img = db.get(StockImage, asset_id)
    if img is None:
        raise ClientError("镜头缺少可用图片（asset_id 为空且无封面兜底）")
    cat = db.get(StockCategory, img.category_id)
    if cat is None:
        raise ClientError("图片所属栏目不存在")
    return image_store.get_object_bytes(cat.bucket_name, img.minio_key)


def run_video_job(job_id: str, session_factory) -> None:
    """后台线程入口：自建 session 跑整条 pipeline。异常兜底写 failed。"""
    db = session_factory()
    try:
        job = db.query(VideoJob).filter(VideoJob.job_id == job_id).one()
        job.status = "running"
        db.commit()

        storyboard = Storyboard.model_validate(job.storyboard)
        article = db.get(Article, job.article_id)
        width, height = _DIMENSIONS[storyboard.aspect_ratio]
        engine = get_engine(job.engine)
        video_store.ensure_video_bucket()
        font_path = fc.__dict__  # placeholder replaced below

        from server.app.modules.video.binaries import cjk_font_path

        with tempfile.TemporaryDirectory() as tmp:
            cues: list[tuple[str, float]] = []
            shot_paths: list[str] = []
            for i, shot in enumerate(storyboard.shots):
                # 1) 配音
                audio_bytes = engine.synthesize(shot.narration)
                audio_path = os.path.join(tmp, f"a{i}.mp3")
                with open(audio_path, "wb") as fh:
                    fh.write(audio_bytes)
                duration = fc.probe_duration(audio_path)
                if shot.duration_hint:
                    duration = max(duration, shot.duration_hint)
                if duration <= 0:
                    duration = 3.0
                # 2) 取图
                img_bytes = _load_image_bytes(db, shot.asset_id, article)
                img_path = os.path.join(tmp, f"img{i}.jpg")
                with open(img_path, "wb") as fh:
                    fh.write(img_bytes)
                # 3) 单镜头渲染
                out_path = os.path.join(tmp, f"shot{i}.mp4")
                cmd = fc.build_shot_command(
                    image_path=img_path, audio_path=audio_path, out_path=out_path,
                    duration=duration, subtitle=shot.subtitle,
                    width=width, height=height, font_path=cjk_font_path(),
                )
                fc.run(cmd)
                shot_paths.append(out_path)
                cues.append((shot.subtitle, duration))
                job.progress = round((i + 1) / (len(storyboard.shots) + 1), 3)
                db.commit()

            # 4) 拼接
            list_file = os.path.join(tmp, "list.txt")
            with open(list_file, "w", encoding="utf-8") as fh:
                for p in shot_paths:
                    fh.write(f"file '{p}'\n")
            final_path = os.path.join(tmp, "final.mp4")
            fc.run(fc.build_concat_command(list_file=list_file, out_path=final_path))

            # 5) 落库 MinIO
            video_key = f"{job.job_id}.mp4"
            srt_key = f"{job.job_id}.srt"
            video_store.put_video(video_key, _read_file(final_path))
            video_store.put_srt(srt_key, build_srt(cues).encode("utf-8"))

        job.video_key = video_key
        job.srt_key = srt_key
        job.progress = 1.0
        job.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 后台线程兜底，任何失败落 failed
        logger.exception("视频生成失败: job_id=%s", job_id)
        db.rollback()
        try:
            job = db.query(VideoJob).filter(VideoJob.job_id == job_id).one()
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
        except Exception:
            logger.exception("写 failed 状态也失败: job_id=%s", job_id)
    finally:
        db.close()


def spawn_video_job(job_id: str) -> None:
    if bg_session_factory is None:
        raise ClientError("bg_session_factory 未注入（create_app 未执行？）")
    factory = bg_session_factory
    threading.Thread(target=run_video_job, args=(job_id, factory), daemon=True).start()
```

> 实现说明：上面 `font_path = fc.__dict__` 那行是占位残留，删掉它，改用后面 `from ...binaries import cjk_font_path` + `cjk_font_path()`（已在渲染循环里调用）。提交前确认该占位行已删除。

- [ ] **Step 5: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test pytest server/tests/test_video_service.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: 提交**

```bash
git add server/app/modules/video/store.py server/app/modules/video/service.py \
        server/tests/test_video_service.py
git commit -m "feat(video): MinIO 视频存储 + compose 编排 service（后台线程）"
```

---

### Task 8: video 路由（MCP 端点 + 公开文件服务）

3 个端点：`POST /api/videos/compose`（建 job + 起线程，202）、`GET /api/videos/status/{job_id}`（状态+产物 URL）、公开 `GET /api/videos/file/{job_id}` / `srt/{job_id}`。路径前缀全用静态段避免 `{job_id}` 冲突。

**Files:**
- Create: `server/app/modules/video/router.py`
- Test: `server/tests/test_video_api.py`

**Interfaces:**
- Consumes: `video.service`（`create_video_job` / `spawn_video_job`）、`video.store.get_object`、`video.models.VideoJob`、`require_mcp_token`、`mcp_exception_response`
- Produces:
  - `video.router.video_mcp_router`（MCP token）：`POST /compose`、`GET /status/{job_id}`
  - `video.router.video_files_router`（公开）：`GET /file/{job_id}`、`GET /srt/{job_id}`
  - `video.router.bg_session_factory`（模块级，由 main 注入 → 转写进 service.bg_session_factory）

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_api.py`：

```python
from __future__ import annotations

import pytest


@pytest.mark.mysql
def test_compose_requires_mcp_token(build_test_client_fixture):
    client, ctx = build_test_client_fixture  # 见 conftest：TestClient + 上下文
    try:
        resp = client.post("/api/videos/compose", json={"article_id": 1, "storyboard": {}})
        assert resp.status_code == 401
    finally:
        ctx.cleanup()


@pytest.mark.mysql
def test_compose_and_status_roundtrip(build_test_client_fixture, monkeypatch):
    client, ctx = build_test_client_fixture
    try:
        article_id, asset_id = ctx.seed_article_and_image()
        # 不真正起渲染线程：mock spawn 只置 running
        monkeypatch.setattr(
            "server.app.modules.video.router.spawn_video_job", lambda jid: None
        )
        headers = {"X-MCP-Token": ctx.mcp_token}
        body = {
            "article_id": article_id,
            "storyboard": {
                "title": "标题",
                "shots": [{"subtitle": "a", "narration": "a", "asset_id": asset_id}],
            },
        }
        resp = client.post("/api/videos/compose", json=body, headers=headers)
        assert resp.status_code == 202
        jid = resp.json()["data"]["job_id"]

        st = client.get(f"/api/videos/status/{jid}", headers=headers)
        assert st.status_code == 200
        data = st.json()["data"]
        assert data["job_id"] == jid
        assert data["status"] in ("pending", "running")
    finally:
        ctx.cleanup()
```

> `build_test_client_fixture` / `ctx.mcp_token` / `ctx.seed_article_and_image()`：按 `server/tests/conftest.py` 现有测试客户端与 MCP token 约定接入（参考 `test_auto_review*.py` / 现有 MCP 端点测试如何拿 token 与 TestClient）。若现有测试用的是别的构造形态，照抄那套，保持断言不变。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_api.py -q`
Expected: FAIL（404/ModuleNotFound：路由未挂）

- [ ] **Step 3: 实现**

`server/app/modules/video/router.py`：

```python
"""video 路由：MCP token 的 compose/status + 公开的文件服务。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.video import store as video_store
from server.app.modules.video.models import VideoJob
from server.app.modules.video.schemas import ComposeVideoRequest
from server.app.modules.video.service import create_video_job, spawn_video_job
from server.app.shared.errors import ClientError, ConflictError, ValidationError

logger = logging.getLogger(__name__)

# main.py 注入（同时转写进 service.bg_session_factory）
bg_session_factory = None

video_mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])
video_files_router = APIRouter()  # 公开：产物供人工上传时下载


def _to_status(job: VideoJob) -> dict:
    return {
        "job_id": job.job_id,
        "article_id": job.article_id,
        "status": job.status,
        "progress": job.progress,
        "video_url": f"/api/videos/file/{job.job_id}" if job.video_key else None,
        "srt_url": f"/api/videos/srt/{job.job_id}" if job.srt_key else None,
        "title": job.title,
        "description": job.description,
        "tags": job.tags or [],
        "error": job.error,
    }


@video_mcp_router.post("/compose", status_code=202)
def compose(req: ComposeVideoRequest, db: Session = Depends(get_db)) -> dict:
    """[MCP] 建视频任务 + 起后台渲染线程。202 立即返回 job_id。"""
    try:
        job = create_video_job(db, req)
    except (ValidationError, ClientError, ConflictError):
        raise
    except HTTPException:
        raise
    except Exception as exc:
        raise mcp_exception_response(exc, context=f"create_video_job article_id={req.article_id}") from exc
    spawn_video_job(job.job_id)
    return {"ok": True, "data": _to_status(job), "error": None}


@video_mcp_router.get("/status/{job_id}")
def status(job_id: str, db: Session = Depends(get_db)) -> dict:
    """[MCP] 查视频任务状态 + 产物 URL。"""
    job = db.query(VideoJob).filter(VideoJob.job_id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="视频任务不存在")
    return {"ok": True, "data": _to_status(job), "error": None}


def _serve(job_id: str, db: Session, kind: str) -> Response:
    job = db.query(VideoJob).filter(VideoJob.job_id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="视频任务不存在")
    key = job.video_key if kind == "video" else job.srt_key
    if not key:
        raise HTTPException(status_code=404, detail="产物尚未生成")
    try:
        data = video_store.get_object(key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO 读取失败: {exc}") from exc
    media = "video/mp4" if kind == "video" else "application/x-subrip"
    return Response(content=data, media_type=media)


@video_files_router.get("/file/{job_id}")
def serve_video(job_id: str, db: Session = Depends(get_db)) -> Response:
    return _serve(job_id, db, "video")


@video_files_router.get("/srt/{job_id}")
def serve_srt(job_id: str, db: Session = Depends(get_db)) -> Response:
    return _serve(job_id, db, "srt")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_video_api.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/video/router.py server/tests/test_video_api.py
git commit -m "feat(video): compose/status MCP 端点 + 公开文件服务路由"
```

---

### Task 9: list_stock_images MCP 端点（catalog）

让 Claude 看到某栏目下的具体图，好在 storyboard 里点名 `asset_id`。加在 `mcp_catalog/router.py`（与 stock-categories 同处）。

**Files:**
- Modify: `server/app/modules/mcp_catalog/router.py`（新增 `StockImageBrief` + `GET /stock-images`）
- Test: `server/tests/test_video_stock_images.py`

**Interfaces:**
- Produces: `GET /api/mcp/stock-images?category_id=&limit=` → `list[StockImageBrief]`（`asset_id / filename / tags / url / w / h`）

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_stock_images.py`：

```python
from __future__ import annotations

import pytest


@pytest.mark.mysql
def test_list_stock_images_requires_token(build_test_client_fixture):
    client, ctx = build_test_client_fixture
    try:
        assert client.get("/api/mcp/stock-images?category_id=1").status_code == 401
    finally:
        ctx.cleanup()


@pytest.mark.mysql
def test_list_stock_images_returns_briefs(build_test_client_fixture):
    client, ctx = build_test_client_fixture
    try:
        _article_id, asset_id = ctx.seed_article_and_image()
        cat_id = ctx.last_category_id  # seed helper 暴露刚建的栏目 id
        resp = client.get(
            f"/api/mcp/stock-images?category_id={cat_id}",
            headers={"X-MCP-Token": ctx.mcp_token},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert any(it["asset_id"] == asset_id for it in items)
        first = items[0]
        assert set(first) >= {"asset_id", "filename", "tags", "url", "w", "h"}
        assert first["url"].startswith("/api/stock-images/")
    finally:
        ctx.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_stock_images.py -q`
Expected: FAIL（404：端点不存在）

- [ ] **Step 3: 实现**

在 `server/app/modules/mcp_catalog/router.py` 的 `# ── stock-categories ──` 区块之后追加：

```python
# ── stock-images ───────────────────────────────────────────────────────────


class StockImageBrief(BaseModel):
    asset_id: int
    filename: str
    tags: list[str]
    url: str
    w: int | None
    h: int | None


@router.get("/stock-images", response_model=list[StockImageBrief])
def mcp_list_stock_images(
    category_id: int = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[StockImageBrief]:
    """[MCP] 列某栏目下的图，供 Claude 在 storyboard 里点名 asset_id。

    只返回轻量字段（含 filename/tags 供无像素判断选图）。url 是公开代理地址。
    """
    rows = (
        db.query(StockImage)
        .filter(StockImage.category_id == category_id)
        .order_by(StockImage.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        StockImageBrief(
            asset_id=img.id,
            filename=img.filename,
            tags=img.tags or [],
            url=f"/api/stock-images/{img.id}/file",
            w=img.width,
            h=img.height,
        )
        for img in rows
    ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_video_stock_images.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/mcp_catalog/router.py server/tests/test_video_stock_images.py
git commit -m "feat(video): list_stock_images MCP catalog 端点"
```

---

### Task 10: 挂路由 + 注入 bg_session_factory + bump MCP_TOOLS_COUNT

把 video 两个 router 挂进 main.py，注入后台 session 工厂，MCP 工具数 21→24。

**Files:**
- Modify: `server/app/main.py`
- Modify: `server/app/modules/mcp_catalog/connect_router.py`（`MCP_TOOLS_COUNT = 24`）
- Test: `server/tests/test_video_wiring.py`

**Interfaces:**
- Consumes: `video.router.video_mcp_router / video_files_router`
- Produces: 挂载后 `/api/videos/*` 可路由；`video.service.bg_session_factory` 被赋值 `SessionLocal`

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_wiring.py`：

```python
from __future__ import annotations

from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT


def test_mcp_tools_count_is_24():
    assert MCP_TOOLS_COUNT == 24


def test_video_routers_importable_and_paths():
    from server.app.modules.video.router import video_files_router, video_mcp_router

    mcp_paths = {r.path for r in video_mcp_router.routes}
    file_paths = {r.path for r in video_files_router.routes}
    assert "/compose" in mcp_paths
    assert "/status/{job_id}" in mcp_paths
    assert "/file/{job_id}" in file_paths
    assert "/srt/{job_id}" in file_paths
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_wiring.py -q`
Expected: FAIL（`MCP_TOOLS_COUNT == 21`）

- [ ] **Step 3: 实现**

`server/app/modules/mcp_catalog/connect_router.py`：把 `MCP_TOOLS_COUNT = 21` 改成 `MCP_TOOLS_COUNT = 24`。

`server/app/main.py`：
1. 顶部 import 区加：

```python
from server.app.modules.video.router import video_files_router, video_mcp_router
```

2. 在 MCP-token 路由组（`articles_mcp_router` 附近）挂 video MCP router：

```python
app.include_router(
    video_mcp_router,
    prefix="/api/videos",
    tags=["video-mcp"],
    # 不挂 get_current_user — MCP token 在 router dependency 内校验
)
```

3. 在公开文件路由组（`stock_files_router` 附近）挂 video 文件 router：

```python
app.include_router(video_files_router, prefix="/api/videos", tags=["video-files"])
```

4. 在 `_scheme_routes.bg_session_factory = SessionLocal` 那一段附近，注入 video 后台工厂（router 与 service 都要拿到）：

```python
import server.app.modules.video.router as _video_routes
import server.app.modules.video.service as _video_service

_video_routes.bg_session_factory = SessionLocal
_video_service.bg_session_factory = SessionLocal
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_video_wiring.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/main.py server/app/modules/mcp_catalog/connect_router.py \
        server/tests/test_video_wiring.py
git commit -m "feat(video): 挂 video 路由 + 注入 bg session + MCP_TOOLS_COUNT→24"
```

---

### Task 11: MCP tools（compose_video / get_video_status / list_stock_images）

在 `server/mcp/tools/video.py` 用 `@mcp.tool()` 定义 3 个 LLM-facing 工具，`server.py` 触发注册。

**Files:**
- Create: `server/mcp/tools/video.py`
- Modify: `server/mcp/server.py`（追加 `from server.mcp.tools import video as _video`）
- Test: `server/tests/test_video_mcp_tools.py`

**Interfaces:**
- Consumes: `server.mcp.server.mcp`、`GeoApiClient`（`_apost` / `_aget`）
- Produces（LLM-facing）：
  - `compose_video(article_id, storyboard, engine=None, model_label=None)` → `_apost("/api/videos/compose")`
  - `get_video_status(job_id)` → `_aget("/api/videos/status/{job_id}")`
  - `list_stock_images(category_id, limit=50)` → `_aget("/api/mcp/stock-images")`

- [ ] **Step 1: 写失败测试**

`server/tests/test_video_mcp_tools.py`：

```python
from __future__ import annotations

from server.mcp.server import mcp


def test_video_tools_registered():
    names = set(mcp._tool_manager._tools.keys())
    assert {"compose_video", "get_video_status", "list_stock_images"} <= names


def test_total_tools_at_least_24():
    assert len(mcp._tool_manager._tools) >= 24
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_video_mcp_tools.py -q`
Expected: FAIL（工具未注册）

- [ ] **Step 3: 实现**

`server/mcp/tools/video.py`：

```python
"""视频生成 Action/Catalog 工具。

compose_video 之于视频 = save_article 之于文章：Claude 主对话写 storyboard，
GEO 后端确定性跑 TTS+ffmpeg，全程不调 LLM。tool 一律 async + 丢线程池（见 catalog.py docstring）。
"""

from __future__ import annotations

from typing import Any

import anyio

from server.mcp.config import get_config
from server.mcp.http_client import ApiError, GeoApiClient
from server.mcp.server import mcp


def _client() -> GeoApiClient:
    cfg = get_config()
    return GeoApiClient(base_url=cfg.internal_api_url, token=cfg.token, timeout=cfg.timeout_seconds)


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _fail(error: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": error}


async def _apost(path: str, *, json: dict[str, Any]) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        try:
            return _ok(_client().post(path, json=json))
        except ApiError as exc:
            return _fail(str(exc))

    return await anyio.to_thread.run_sync(_impl)


async def _aget(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        try:
            return _ok(_client().get(path, params=params))
        except ApiError as exc:
            return _fail(str(exc))

    return await anyio.to_thread.run_sync(_impl)


@mcp.tool()
async def compose_video(
    article_id: int,
    storyboard: dict[str, Any],
    engine: str | None = None,
    model_label: str | None = None,
) -> dict[str, Any]:
    """Compose a slideshow short-video for one article from a storyboard YOU author.

    This is the **zero-config video path**, mirroring save_article: you (the calling
    Claude Code conversation) write the storyboard yourself — GEO makes NO LLM call.
    It deterministically runs TTS + ffmpeg and stores mp4 + SRT + metadata.

    Workflow:
        1. get_article(article_id) to read the article body.
        2. list_stock_categories() + list_stock_images(category_id) to see candidate images.
        3. Author the storyboard yourself: split the article into shots; per shot write
           `subtitle` (burned on screen) + `narration` (TTS voice) + point at an
           `asset_id` from list_stock_images. Also write a GEO-friendly title/description/tags.
        4. compose_video(article_id, storyboard) → returns job_id.
        5. Poll get_video_status(job_id) until status == "done" (or "failed").

    Args:
        article_id: Target article (must exist).
        storyboard: dict with:
            - title: str (GEO-friendly, keyword-rich)
            - description: str (upload caption)
            - tags: list[str]
            - aspect_ratio: "9:16" (default) | "16:9"
            - bgm: "default" | "none"
            - shots: list of {subtitle: str, narration: str, asset_id: int|null, duration_hint: float|null}
              (at least 1, at most 30). asset_id must come from list_stock_images.
        engine: TTS engine code. None = edge (free, no key). Only set if you know an
            alternative engine is configured server-side.
        model_label: Optional author label for traceability.

    Returns:
        {"ok": True, "data": {"job_id": str, "article_id": int, "status": "pending", ...}, "error": None}
    """
    body: dict[str, Any] = {"article_id": article_id, "storyboard": storyboard}
    if engine:
        body["engine"] = engine
    if model_label:
        body["model_label"] = model_label
    return await _apost("/api/videos/compose", json=body)


@mcp.tool()
async def get_video_status(job_id: str) -> dict[str, Any]:
    """Poll a video job's status and fetch product URLs when done.

    Args:
        job_id: From compose_video.

    Returns:
        {"ok": True, "data": {
            "job_id": str, "article_id": int,
            "status": "pending"|"running"|"done"|"failed", "progress": float,
            "video_url": str|null, "srt_url": str|null,
            "title": str|null, "description": str|null, "tags": [str], "error": str|null
        }, "error": None}
    """
    return await _aget(f"/api/videos/status/{job_id}")


@mcp.tool()
async def list_stock_images(category_id: int, limit: int = 50) -> dict[str, Any]:
    """List concrete images in a stock category so you can point at asset_id in a storyboard.

    Args:
        category_id: From list_stock_categories.
        limit: Max images (1-200).

    Returns:
        {"ok": True, "data": [
            {"asset_id": int, "filename": str, "tags": [str], "url": str, "w": int|null, "h": int|null}
        ], "error": None}
    """
    return await _aget(
        "/api/mcp/stock-images",
        params={"category_id": category_id, "limit": max(1, min(200, limit))},
    )
```

`server/mcp/server.py`：在现有 `from server.mcp.tools import action / catalog / meta` 三行后追加：

```python
from server.mcp.tools import video as _video  # noqa: F401,E402
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_video_mcp_tools.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add server/mcp/tools/video.py server/mcp/server.py server/tests/test_video_mcp_tools.py
git commit -m "feat(video): compose_video / get_video_status / list_stock_images MCP tools"
```

---

### Task 12: Loop 配方 + 技能（`video-loop.md` + `geo-video-composer`）

镜像 `/goal` 那套：一个 loop 配方消费已产出/已审核文章，逐篇让 Claude 写 storyboard 调 `compose_video`；一个 skill 封装单篇产视频流程。纯文档，人工验证。

**Files:**
- Create: `claude-loops/video-loop.md`
- Create: `.claude/skills/geo-video-composer/SKILL.md`
- Modify: `CLAUDE.md`（在「加新 Loop 配方」列表加一行 `video-loop.md`；「MCP Server」章节 tool 计数 21→24、三组清单补 3 个 tool）

**Interfaces:**
- Consumes（文档层引用）：MCP tools `list_articles` / `get_article` / `list_stock_categories` / `list_stock_images` / `compose_video` / `get_video_status` / `notify_feishu`

- [ ] **Step 1: 写 loop 配方**

`claude-loops/video-loop.md`（结构照 `generation-loop.md`：你是谁 / 可用工具 / 流程伪码 / 停止条件 / 注意事项）。关键内容：

```markdown
# 配套视频 Loop 配方（零配置版）

> **运行方式**：Claude Code 里 `/loop claude-loops/video-loop.md`。
> **目标**：给今天已产出/已审核的文章批量生成配套图文轮播短视频（mp4+SRT+元数据）入库，飞书播报。
> **零配置**：默认 edge-tts 无 key + ffmpeg，GEO 后端不调 LLM——storyboard 由你（主对话）写。

## 你是谁
GEO 平台配套视频 Loop runner。你自己读文章、切分镜、写逐段文案、点名选图、写标题描述——
GEO 只负责确定性地跑 TTS+ffmpeg 落库。你不调任何 LLM API，"创作"就是你输出 storyboard。

## 可用工具（mcp__geo__*）
- list_articles(review_status="approved", limit) / get_article(article_id) — 拉文章 + 读正文
- list_stock_categories(kind?) / list_stock_images(category_id, limit) — 看候选图，点名 asset_id
- compose_video(article_id, storyboard, engine?, model_label?) — 提交渲染，返回 job_id
- get_video_status(job_id) — 轮询到 done/failed，取 video_url/srt_url
- notify_feishu(title, message, level) — 播报

## 流程（伪码）
```
notify_feishu("配套视频流程开始", "目标：给 N 篇已审核文章配视频", "info")
articles = list_articles(review_status="approved", limit=10).data
done, attempts = 0, 0
run_log = []
while done < N and attempts < 2*N:
    a = articles[attempts]; attempts += 1
    art = get_article(a.id).data
    # 看候选图：先 list_stock_categories 找相关栏目，再 list_stock_images 取图
    cats = list_stock_categories().data
    imgs = list_stock_images(category_id=<挑一个相关栏目>, limit=30).data
    # 你自己写 storyboard：把 art.plain_text 切 4-8 个镜头，每镜头写 subtitle+narration，
    # 从 imgs 里点名 asset_id；写 GEO 友好 title/description/tags
    storyboard = {你输出}
    r = compose_video(article_id=a.id, storyboard=storyboard, model_label="claude-opus-4-8")
    if not r.ok: continue
    jid = r.data.job_id
    # 轮询（渲染慢，间隔 ~10s，最多 ~20 次）
    while True:
        st = get_video_status(jid).data
        if st.status in ("done","failed"): break
        sleep(10)
    if st.status == "done":
        done += 1
        run_log.append({article_id:a.id, job_id:jid, video_url:st.video_url})
notify_feishu("配套视频流程完成", <run_log 明细 + 计数>, "done")
```

## 停止条件
- 达成 N 篇 → done 播报
- attempts ≥ 2N 仍不足 → warning 播报（产能不足）
- 无已审核文章 → warning 播报（无候选）

## 注意事项
- **storyboard 是你的作品**：切分镜、逐段文案、点名 asset_id、标题描述都你写；GEO 不改写。
- **subtitle 短**（≤ ~14 字/镜头利于烧录）；narration 可稍长口语化；二者可相同。
- **asset_id 必须来自 list_stock_images**（不存在会被 compose 拒 ValidationError）。
- **渲染慢**：compose_video 立即返回 job_id，必须轮询 get_video_status 到 done/failed。
- **发布人工**：产物只入库，get_video_status 给的 video_url/srt_url 供人工下载上传视频平台。
- **飞书节制**：开始/结束各一条，单篇不逐发。
```

- [ ] **Step 2: 写 skill**

`.claude/skills/geo-video-composer/SKILL.md`（YAML frontmatter + 正文）：

```markdown
---
name: geo-video-composer
description: Use when spawned to compose one GEO article into a slideshow short-video, or when manually turning an article into a video. Reads the article + candidate stock images from MCP, authors a storyboard, calls compose_video, polls get_video_status, returns the product URLs.
---

# GEO 配套视频作者

你把**一篇** GEO 文章变成一个图文轮播短视频（mp4+SRT+元数据）。GEO 后端不调 LLM——
storyboard 由你写，等价于写文章 markdown。

## 步骤
1. `get_article(article_id)` 读正文（用 plain_text 切分镜）。
2. `list_stock_categories()` 找相关栏目 → `list_stock_images(category_id)` 取候选图（看 filename/tags 判断）。
3. 写 storyboard：
   - `title` / `description` / `tags`：GEO 友好、含关键词，供人工上传时粘贴。
   - `shots`（4-8 个）：每个 `subtitle`（≤~14 字，烧录用）+ `narration`（口播）+ `asset_id`（从候选图点名）。
4. `compose_video(article_id, storyboard, model_label="claude-opus-4-8")` → job_id。
5. 轮询 `get_video_status(job_id)`（间隔 ~10s）到 `done`/`failed`；返回 `video_url` / `srt_url` 或 error。

## 约束
- asset_id 必须来自 list_stock_images，否则 compose 报 ValidationError。
- subtitle 简短；narration 可稍长。二者可相同。
- 只产资产、不发布；产物 URL 供人工下载上传。
```

- [ ] **Step 3: 更新 CLAUDE.md**

在 `CLAUDE.md`「POC 期已有的 3 个 Loop 配方」列表加：

```markdown
- `video-loop.md` — 配套视频 Loop（拉已审核文章 → 写 storyboard → compose_video → 轮询 → 飞书）
```

在「MCP Server」章节把 tool 总数 21 改 24（两处提及 `MCP_TOOLS_COUNT` 的说明句），并在「Tool 三组」清单补：catalog 加 `list_stock_images`、action 加 `compose_video`，meta/独立列 `get_video_status`（放 catalog 更合适，作为读端）——即 catalog 9→11、action 8→9，合计 24。

- [ ] **Step 4: 人工验证（无自动化测试）**

Run: `pnpm --filter @geo/web build` 不涉及；本任务是文档。做一致性自查：
- `grep -rn "MCP_TOOLS_COUNT" server/` 确认代码常量已是 24（Task 10 改过）。
- 目视 `claude-loops/video-loop.md` 与 `generation-loop.md` 结构齐平。
Expected: 三处 tool 计数（代码常量、CLAUDE.md、配方说明）一致为 24。

- [ ] **Step 5: 提交**

```bash
git add claude-loops/video-loop.md .claude/skills/geo-video-composer/SKILL.md CLAUDE.md
git commit -m "docs(video): video-loop 配方 + geo-video-composer skill + CLAUDE.md 同步"
```

---

### Task 13: 全量回归 + lint/mypy + 容器内端到端验证

**Files:** 无新增（收尾）

- [ ] **Step 1: 后端全量测试**

Run: `GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test pytest server/tests/ -q`
Expected: 全绿（含新增 `test_video_*.py`）

- [ ] **Step 2: lint + 类型**

Run: `ruff check server/ && ruff format --check server/ && mypy server/app`
Expected: 无 error（mypy 宽松；video 模块有类型标注）

- [ ] **Step 3: 容器内端到端（真 ffmpeg/edge-tts/MinIO）**

> 见 memory：Python 工具进 dev 容器跑；ffmpeg 只在容器内。本步在 app 容器里手动跑一次真实渲染验证画质与字幕。

Run（容器内）：
```bash
docker-compose exec app python - <<'PY'
from server.app.db.session import SessionLocal
from server.app.modules.video.schemas import ComposeVideoRequest, Shot, Storyboard
from server.app.modules.video import service as v
v.bg_session_factory = SessionLocal
db = SessionLocal()
req = ComposeVideoRequest(article_id=<真实已审核文章id>, storyboard=Storyboard(
    title="测试视频", description="d", tags=["t"],
    shots=[Shot(subtitle="第一段字幕", narration="第一段口播文案", asset_id=<真实图id>),
           Shot(subtitle="第二段字幕", narration="第二段口播文案", asset_id=<真实图id>)]))
job = v.create_video_job(db, req); print("job", job.job_id); db.close()
v.run_video_job(job.job_id, SessionLocal)
db = SessionLocal(); from server.app.modules.video.models import VideoJob
j = db.query(VideoJob).filter_by(job_id=job.job_id).one()
print(j.status, j.error, j.video_key, j.srt_key)
PY
```
Expected: `done`，`video_key` / `srt_key` 非空；下载 `/api/videos/file/{job_id}` 目视：竖屏、图片铺满有缓推、中文字幕烧录正确、有配音。若字幕串位/字体缺字/画质异常，在此调 `ffmpeg_compose.build_shot_command` 的滤镜参数（zoompan/drawtext/字号/位置）后重跑。

- [ ] **Step 4: 推分支 + 开 PR**

```bash
git push -u origin feat/article-to-video-loop
gh pr create --title "feat(video): 文章→配套视频 Claude Code Loop（MVP）" \
  --body "见 docs/superpowers/specs/2026-07-07-article-to-video-loop-design.md 与 plans/2026-07-07-article-to-video-loop.md"
```

---

## 自查（写完计划回看 spec）

**Spec 覆盖：**
- 路线 A 图文轮播+TTS+烧录字幕 → Task 5(TTS)/6(ffmpeg)/7(编排) ✓
- 只产资产不发布 → Task 8 只出 mp4/srt/元数据，无发布驱动 ✓
- 复用图库逐段选图 → Task 7 `_load_image_bytes` 复用 image_library store ✓
- Claude 点名选图（GEO 不调 LLM）→ Task 9 list_stock_images + Task 11 tool + storyboard.asset_id ✓
- storyboard=markdown 分工 → Task 3 schema + Task 11 compose_video tool docstring ✓
- 真零配置 edge-tts 无 key → Task 5 默认引擎 ✓
- SRT + 结构化元数据一等产物 → Task 4 SRT + VideoJob.title/description/tags + status 回传 ✓
- 异步 bg_session_factory 后台线程 → Task 7 spawn_video_job + Task 10 注入 ✓
- 3 个 MCP tool，21→24 → Task 11 + Task 10 ✓
- loop 配方 + skill 镜像 /goal → Task 12 ✓
- ffmpeg 只在容器（app 镜像）→ Task 1 Dockerfile.app + Task 13 容器验证 ✓
- MCP token 鉴权 + mcp_exception_response → Task 8 ✓

**占位符扫描：** Task 7 Step 3 明确标注删除 `font_path = fc.__dict__` 占位行（实现用 `cjk_font_path()`）；其余步骤均给出完整代码。测试里 `build_test_app_fixture` / `build_test_client_fixture` / `ctx.seed_*` 按 conftest 现有约定接入——实现者须先读 `server/tests/conftest.py` 对齐 fixture 形态（这是接入点说明，非空占位）。

**类型/命名一致性：** `VideoJob`(job_id/status/progress/storyboard/video_key/srt_key/title/description/tags/error) 跨 Task 2/7/8 一致；`ComposeVideoRequest`/`Storyboard`/`Shot` 跨 3/7/8/11 一致；`get_engine`/`synthesize`(→bytes) 跨 5/7 一致；`build_shot_command`/`build_concat_command`/`probe_duration`/`run` 跨 6/7 一致；`create_video_job`/`run_video_job`/`spawn_video_job`/`bg_session_factory` 跨 7/8/10 一致；`MCP_TOOLS_COUNT=24` 跨 10/11/12 一致。
