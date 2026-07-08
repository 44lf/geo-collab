"""多 skill 库 HTTP 路由：/api/mcp/skills/*（user JWT）+ install-payload（MCP token）。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.core.security import get_current_user, require_admin
from server.app.db.session import get_db
from server.app.modules.audit.service import add_audit_entry
from server.app.modules.loop_skills import skill_service as svc
from server.app.modules.loop_skills.schemas import (
    SetCurrentBody,
    SkillList,
    SkillMeta,
    SkillVersionList,
    SkillVersionMeta,
    UploadResult,
)
from server.app.modules.loop_skills.service import SkillBundle, build_zip
from server.app.modules.system.models import User
from server.app.shared.errors import ClientError, ConflictError, ValidationError

skills_user_router = APIRouter()


def _zip_response(b: SkillBundle) -> Response:
    ascii_name = f"geo-skill-{b.bundle_sha256[:12]}.zip"
    utf8_name = quote(f"geo-skill-{b.version}.zip")
    return Response(
        content=build_zip(b),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
            ),
        },
    )


@skills_user_router.get("/skills", response_model=SkillList)
def list_skills(db: Session = Depends(get_db)) -> SkillList:
    return SkillList(skills=[SkillMeta(**it.__dict__) for it in svc.list_skills(db)])


@skills_user_router.post("/skills/upload", response_model=UploadResult)
async def upload_skill(
    files: list[UploadFile] = File(...),
    name: str = Form(...),
    category: str = Form("general"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResult:
    entries = [(f.filename or "file", await f.read()) for f in files]
    try:
        skill, version = svc.create_version(
            db,
            entries=entries,
            name=name,
            uploaded_by=current_user.id,
            is_admin=(current_user.role == "admin"),
            category=category,
        )
    except (ConflictError, ValidationError):
        # 冲突(并发撞版本号→409) / 不存在类(→400) 走全局兜底,不在此处改写
        raise
    except ClientError as exc:
        # 剩下的才是权限类 ClientError(官方包非 admin 追加版本)→ 403
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    add_audit_entry(
        db,
        user=current_user,
        action="skill.upload",
        target_type="skill",
        target_id=str(skill.id),
        payload={"version": version.version_label},
    )
    return UploadResult(skill_id=skill.id, slug=skill.slug, version_label=version.version_label)


@skills_user_router.get("/skills/{skill_id}/versions", response_model=SkillVersionList)
def list_versions(skill_id: int, db: Session = Depends(get_db)) -> SkillVersionList:
    return SkillVersionList(
        versions=[SkillVersionMeta(**v.__dict__) for v in svc.list_versions(db, skill_id)]
    )


@skills_user_router.post("/skills/{skill_id}/set-current", status_code=204)
def set_current(
    skill_id: int,
    body: SetCurrentBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    try:
        svc.set_current(db, skill_id, body.version_id, is_admin=(current_user.role == "admin"))
    except (ConflictError, ValidationError):
        # 不存在类(→400) 走全局兜底,不在此处改写
        raise
    except ClientError as exc:
        # 剩下的才是权限类 ClientError(官方包非 admin 回滚)→ 403
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    add_audit_entry(
        db,
        user=current_user,
        action="skill.set_current",
        target_type="skill",
        target_id=str(skill_id),
        payload={"version_id": body.version_id},
    )
    return Response(status_code=204)


@skills_user_router.delete("/skills/{skill_id}/versions/{version_id}", status_code=204)
def delete_version(
    skill_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    try:
        svc.delete_version(
            db,
            skill_id,
            version_id,
            user_id=current_user.id,
            is_admin=(current_user.role == "admin"),
        )
    except (ConflictError, ValidationError):
        # 冲突(删当前版本→409) / 不存在(→400) 走全局兜底,不在此处改写
        raise
    except ClientError as exc:
        # 剩下的才是权限类 ClientError(官方包非 admin / 非属主删版本)→ 403
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    add_audit_entry(
        db,
        user=current_user,
        action="skill.delete_version",
        target_type="skill_version",
        target_id=str(version_id),
    )
    return Response(status_code=204)


@skills_user_router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Response:
    svc.delete_skill(db, skill_id)
    add_audit_entry(
        db,
        user=current_user,
        action="skill.delete",
        target_type="skill",
        target_id=str(skill_id),
    )
    return Response(status_code=204)


@skills_user_router.get("/skills/{skill_id}/download.zip")
def download_skill(skill_id: int, db: Session = Depends(get_db)) -> Response:
    return _zip_response(svc.get_skill_bundle_by_id(db, skill_id))


skills_mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])


@skills_mcp_router.get("/skills/catalog")
def mcp_list_skills(category: str | None = None, db: Session = Depends(get_db)) -> dict:
    try:
        items = svc.list_skills(db)
        if category:
            items = [it for it in items if it.category == category]
        skills = [
            {
                "id": it.id,
                "slug": it.slug,
                "name": it.name,
                "category": it.category,
                "is_official": it.is_official,
                "current_version_label": it.current_version_label,
                "file_count": it.file_count,
                "total_bytes": it.total_bytes,
                "units": svc.unit_names_for_skill(db, it.id, it.slug),
            }
            for it in items
        ]
        return {"skills": skills}
    except Exception as exc:
        raise mcp_exception_response(exc, context=f"mcp_list_skills category={category}") from exc


@skills_mcp_router.get("/skills/{slug}/install-payload")
def install_payload(slug: str, db: Session = Depends(get_db)) -> dict:
    try:
        b = svc.get_current_bundle(db, slug)
    except (ValidationError, ClientError) as exc:
        # 只有"skill 不存在/无当前版本"这类才回 ok:false + available 列表；
        # 基础设施错误(DB/MinIO 等)让它自然抛，交给下面的 mcp_exception_response。
        return {
            "ok": False,
            "error": str(exc),
            "data": {"available": [{"slug": s.slug, "name": s.name} for s in svc.list_skills(db)]},
        }
    except Exception as exc:
        raise mcp_exception_response(exc, context=f"install_payload slug={slug}") from exc
    return {
        "ok": True,
        "data": {
            "version": b.version,
            "bundle_sha256": b.bundle_sha256,
            "install_hint": (
                "Write each file to the user's .claude/ directory, preserving the relative "
                "path. Prefer project-level <repo>/.claude/ over ~/.claude/ inside a git repo. "
                "If a file exists, show diff and ask before overwriting."
            ),
            "files": [
                {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
                for f in b.files
            ],
        },
        "error": None,
    }
