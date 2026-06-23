"""平台驱动的共享数据契约：传给驱动的 PublishPayload、返回的 PublishResult，
以及驱动级异常 PublishError / UserInputRequired。

驱动只拿这里的纯数据结构，所有 asset 路径在进浏览器前已从 DB 预解析，
驱动内不碰 ORM（见 CLAUDE.md「PlatformDriver」约束）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server.app.modules.articles.parser import BodySegment  # noqa: F401


@dataclass(frozen=True)
class PublishPayload:
    """传给平台驱动的已完整解析文章数据。

    所有 asset 路径都会在启动浏览器前从 DB 预解析，因此驱动不需要访问 ORM
    关系，也不需要调用 resolve_asset_path。
    """

    title: str
    cover_asset_path: Path
    body_segments: list[BodySegment]
    account_key: str
    state_path: Path
    display_name: str
    platform_code: str
    temp_files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ApiPublishPayload:
    """API 型平台驱动的发布载荷：纯数据，不含 secret。

    与 PublishPayload 的区别：无 state_path/account_key；cover_path 可空（驱动内回落正文首图）。

    两种鉴权形态，由驱动 ``auth`` 决定、runner_api 注入对应字段（驱动只读纯数据，不碰 ORM）：
      - ``auth='token'``（默认，公众号）：``access_token`` 由 DB 缓存解析后注入。
      - ``auth='cookie'``（TapTap）：``state`` = 解密后的 storage_state（含 cookie 罐）；
        ``forum`` = ``api_credentials``（如 ``{app_id, group_id, x_ua}``）；``content_json`` =
        Tiptap 文档（全保真转换用）；``image_paths`` = 图片节点 key→本地文件路径（含图库临时文件）。
    """

    title: str
    body_segments: list[BodySegment]
    cover_path: Path | None
    display_name: str
    platform_code: str
    access_token: str = ""
    temp_files: tuple[Path, ...] = ()
    # ── cookie-session 形态（TapTap）专用，token 形态留空 ──────────────────
    state: dict | None = None
    forum: dict | None = None
    content_json: dict | None = None
    image_paths: dict[str, Path] | None = None


@dataclass(frozen=True)
class PublishResult:
    url: str | None
    title: str
    message: str


class PublishError(Exception):
    """平台无关的发布失败异常，可附带诊断截图。"""

    def __init__(self, message: str, screenshot: bytes | None = None):
        super().__init__(message)
        self.screenshot = screenshot


class UserInputRequired(PublishError):
    """需要 noVNC 人工接管（登录失效 / 验证码等）时抛出。

    携带 session_id / novnc_url 供前端接管；error_type 区分接管原因。
    注意：stop_before_publish=True 的正常停顿不抛此异常（见 CLAUDE.md）。
    """

    def __init__(
        self,
        message: str,
        screenshot: bytes | None = None,
        session_id: str | None = None,
        novnc_url: str | None = None,
        error_type: str = "login_required",
    ):
        super().__init__(message, screenshot)
        self.session_id = session_id
        self.novnc_url = novnc_url
        self.error_type = error_type
