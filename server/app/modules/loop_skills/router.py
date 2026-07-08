"""loop_skills HTTP 路由.

两组路由：
- router (user JWT)：/info + /download.zip + /versions（只读列表），给 Web Section ⑤ 用
- mcp_router (MCP token)：/install-payload，给 install_loop_skills 工具用

Task 7 起，这三个只读端点（info / download.zip / install-payload）不再读旧的
`LoopSkillBundleVersion` 版本表（`versions_service.py`），而是转读官方 skill
（slug="goal"）的当前版本 —— 见 `skill_service.get_current_bundle()`。旧的上传
/ 启用 / 删除写端点已下线（前端已切到 `/api/mcp/skills/*` 新路由，见
`skill_router.py`）。这里只剩「旧路径 → 新数据源」的兼容别名，保证存量调用方
（Web Section ⑤、`install_loop_skills` MCP 工具旧调用）不断。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.db.session import get_db
from server.app.modules.loop_skills import skill_service as _svc
from server.app.modules.loop_skills.schemas import BundleVersionList, BundleVersionMeta
from server.app.modules.loop_skills.service import SkillBundle, build_zip
from server.app.shared.errors import ValidationError

router = APIRouter()

_OFFICIAL_SLUG = "goal"


class LoopSkillFileMeta(BaseModel):
    path: str
    size: int
    sha256: str


class LoopSkillBundleInfo(BaseModel):
    version: str
    bundle_sha256: str
    files: list[LoopSkillFileMeta]
    install_hint: str


def _zip_response(b: SkillBundle) -> Response:
    """把 bundle 打成 zip Response。version_label 可含中文/特殊字符 → 头部一律做 ASCII 安全化:
    - filename 用 ASCII slug(sha 短串);中文原名走 RFC 5987 filename*
    - X-Bundle-Version 用 percent-encode(否则 Starlette latin-1 编码非 ASCII 会 500 / header 注入)
    """
    ascii_name = f"geo-loop-skills-{b.bundle_sha256[:12]}.zip"
    utf8_name = quote(f"geo-loop-skills-{b.version}.zip")
    return Response(
        content=build_zip(b),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
            ),
            "X-Bundle-Version": quote(b.version),
            "X-Bundle-Sha256": b.bundle_sha256,
        },
    )


def _find_official_skill(db: Session) -> _svc.SkillListItem | None:
    """按 slug 找官方 skill。`skill_service` 只公开 `list_skills` 做枚举，没有
    按 slug 查单条的公开函数——用列表过滤，避免碰它的私有 `_active_skill_by_slug`。
    """
    return next((s for s in _svc.list_skills(db) if s.slug == _OFFICIAL_SLUG), None)


@router.get("/loop-skill-bundle/info", response_model=LoopSkillBundleInfo)
def get_loop_skill_bundle_info(db: Session = Depends(get_db)) -> LoopSkillBundleInfo:
    """[user] /goal Loop skill 包元信息 —— 别名读官方 skill(slug=goal)当前版本。

    官方包不存在 / 无当前版本 → `ValidationError` 冒泡给全局 handler → 400。
    """
    b = _svc.get_current_bundle(db, _OFFICIAL_SLUG)
    return LoopSkillBundleInfo(
        version=b.version,
        bundle_sha256=b.bundle_sha256,
        files=[LoopSkillFileMeta(path=f.path, size=f.size, sha256=f.sha256) for f in b.files],
        install_hint=(
            "解压到本机 ~/.claude/（全局，所有 Claude Code 会话可见）"
            " 或项目根 <repo>/.claude/（仅该项目可见）。"
        ),
    )


@router.get("/loop-skill-bundle/download.zip")
def download_loop_skill_bundle_zip(
    version: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Response:
    """[user] 下载官方 skill(goal)当前版本 zip。

    `version` 查询参数为旧调用方兼容保留；官方 skill 只有"当前版本"概念，
    这里直接忽略，不再支持按 id / label 点名历史版本（要点名历史版本请走
    新的 `/api/mcp/skills/{skill_id}/download.zip`）。
    """
    b = _svc.get_current_bundle(db, _OFFICIAL_SLUG)
    return _zip_response(b)


@router.get("/loop-skill-bundle/versions", response_model=BundleVersionList)
def list_bundle_versions(db: Session = Depends(get_db)) -> BundleVersionList:
    """[user] 列出官方 skill(goal)的版本历史(元信息,不含正文)。官方包不存在 → 空列表。"""
    official = _find_official_skill(db)
    if official is None:
        return BundleVersionList(versions=[])
    items = _svc.list_versions(db, official.id)
    return BundleVersionList(
        versions=[
            BundleVersionMeta(
                id=m.id,
                version_label=m.version_label,
                bundle_sha256=m.bundle_sha256,
                file_count=m.file_count,
                total_size=m.total_bytes,
                is_enabled=m.is_current,
                uploaded_by_user_id=m.uploaded_by,
                notes=None,
                created_at=m.uploaded_at,
            )
            for m in items
        ]
    )


# MCP token 鉴权 (router-level dependency)
mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])


@mcp_router.get("/loop-skill-bundle/install-payload")
def get_loop_skill_install_payload(
    version: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """[MCP] install_loop_skills 工具旧路径别名 —— 转读官方 skill(slug=goal)当前版本。

    `version` 查询参数为旧调用方兼容保留，目前忽略(官方 skill 无历史点名概念)。
    找不到官方包 → 返回 {ok:false, data:{available:[...]}}(HTTP 200,非 400):让
    MCP 工具拿到现有 skill 列表帮用户排查，而不是直接报错。
    """
    try:
        b = _svc.get_current_bundle(db, _OFFICIAL_SLUG)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "data": {"available": [{"slug": s.slug, "name": s.name} for s in _svc.list_skills(db)]},
        }
    return {
        "ok": True,
        "data": {
            "version": b.version,
            "bundle_sha256": b.bundle_sha256,
            "install_hint": (
                "Write each file to the user's .claude/ directory, preserving "
                "the relative path. Prefer project-level <repo>/.claude/ over "
                "~/.claude/ when the user is currently inside a git repo. "
                "If a file already exists, show diff and ask user before overwriting."
            ),
            "files": [
                {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
                for f in b.files
            ],
        },
        "error": None,
    }
