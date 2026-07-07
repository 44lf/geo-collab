"""loop_skills HTTP 路由.

两组路由：
- router (user JWT)：/info + /download.zip，给 Web Section ⑤ 用
- mcp_router (MCP token)：/install-payload，给 install_loop_skills 工具用（Task 6 加）

两条用户群不同 + 鉴权不同，必须拆 router；service 层 build_bundle 共用。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.security import get_current_user, require_admin
from server.app.db.session import get_db
from server.app.modules.audit.service import add_audit_entry
from server.app.modules.loop_skills import versions_service as vs
from server.app.modules.loop_skills.schemas import BundleVersionList, BundleVersionMeta
from server.app.modules.loop_skills.service import SkillBundle, build_zip
from server.app.modules.system.models import User
from server.app.shared.errors import ValidationError

router = APIRouter()


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


@router.get("/loop-skill-bundle/info", response_model=LoopSkillBundleInfo)
def get_loop_skill_bundle_info(db: Session = Depends(get_db)) -> LoopSkillBundleInfo:
    """[user] /goal Loop skill 包元信息 —— 当前启用版(无则回落种子)。"""
    b = vs.get_active_bundle(db)
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
    """[user] 下载 zip。version 空=启用版;数字=id;否则 label。"""
    b = vs.resolve_bundle_for_install(db, version)  # 找不到 → ValidationError → 全局 400
    return _zip_response(b)


@router.get("/loop-skill-bundle/versions", response_model=BundleVersionList)
def list_bundle_versions(db: Session = Depends(get_db)) -> BundleVersionList:
    """[user] 列出所有未删除版本(元信息,不含正文)。"""
    metas = vs.list_versions(db)
    return BundleVersionList(
        versions=[
            BundleVersionMeta(
                id=m.id,
                version_label=m.version_label,
                bundle_sha256=m.bundle_sha256,
                file_count=m.file_count,
                total_size=m.total_size,
                is_enabled=m.is_enabled,
                uploaded_by_user_id=m.uploaded_by_user_id,
                notes=m.notes,
                created_at=m.created_at,
            )
            for m in metas
        ]
    )


@router.post("/loop-skill-bundle/versions", response_model=BundleVersionMeta)
async def upload_bundle_version(
    file: UploadFile = File(...),
    version_label: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BundleVersionMeta:
    """[user] 上传 zip 建新版本(默认不启用)。"""
    data = await file.read()
    m = vs.upload_version(
        db,
        data,
        version_label=version_label,
        notes=notes,
        uploaded_by_user_id=current_user.id,
    )
    add_audit_entry(
        db,
        user=current_user,
        action="loop_skill_bundle.upload",
        target_type="loop_skill_bundle",
        target_id=str(m.id),
        payload={"version_label": m.version_label, "sha": m.bundle_sha256},
    )
    return BundleVersionMeta(
        id=m.id,
        version_label=m.version_label,
        bundle_sha256=m.bundle_sha256,
        file_count=m.file_count,
        total_size=m.total_size,
        is_enabled=m.is_enabled,
        uploaded_by_user_id=m.uploaded_by_user_id,
        notes=m.notes,
        created_at=m.created_at,
    )


@router.post("/loop-skill-bundle/versions/{version_id}/enable", status_code=204)
def enable_bundle_version(
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),  # 决策1:启用=改所有人本地 skill,收归 admin
) -> Response:
    """[admin] 启用某版本(=回退)。"""
    vs.enable_version(db, version_id)
    add_audit_entry(
        db,
        user=current_user,
        action="loop_skill_bundle.enable",
        target_type="loop_skill_bundle",
        target_id=str(version_id),
    )
    return Response(status_code=204)


@router.delete("/loop-skill-bundle/versions/{version_id}", status_code=204)
def delete_bundle_version(
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),  # 决策1:删除收归 admin
) -> Response:
    """[admin] 逻辑删除某版本(拒删当前启用版)。"""
    vs.soft_delete_version(db, version_id)
    add_audit_entry(
        db,
        user=current_user,
        action="loop_skill_bundle.delete",
        target_type="loop_skill_bundle",
        target_id=str(version_id),
    )
    return Response(status_code=204)


@router.get("/loop-skill-bundle/versions/{version_id}/download.zip")
def download_bundle_version(version_id: int, db: Session = Depends(get_db)) -> Response:
    """[user] 下载指定版本 zip。不存在/已删 → 404(spec §L169)。"""
    try:
        b = vs.get_bundle_by_id(db, version_id)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _zip_response(b)


# MCP token 鉴权 (router-level dependency)
mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])


@mcp_router.get("/loop-skill-bundle/install-payload")
def get_loop_skill_install_payload(
    version: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """[MCP] install_loop_skills 工具入口 —— version 空=启用版,否则点名。

    找不到点名版本 → 返回 {ok:false, data:{available:[...]}}(HTTP 200,非 400):让 MCP 工具
    拿到候选版本列表帮用户挑,兑现 spec §L168、支撑"点名任意版"的可用性。
    """
    try:
        b = vs.resolve_bundle_for_install(db, version)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "data": {
                "available": [
                    {"id": m.id, "version_label": m.version_label, "is_enabled": m.is_enabled}
                    for m in vs.list_versions(db)
                ]
            },
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
