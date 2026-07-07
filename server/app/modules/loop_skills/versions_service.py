"""loop_skills 版本管理 —— DB 读写(与纯文件逻辑的 service.py 分离)。"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.loop_skills.models import LoopSkillBundleVersion
from server.app.modules.loop_skills.service import (
    SkillBundle,
    SkillFile,
    build_bundle,
    build_bundle_from_file_map,  # noqa: F401  (Task 4 用)
)
from server.app.shared.errors import ConflictError, ValidationError

# 上传 zip 压缩后体积上限:几个 md 几十 KB 足够,2MB 防滥用
LOOP_SKILL_MAX_ZIP_BYTES = 2 * 1024 * 1024
# 单条目解压后上限(照搬 accounts import 范式)
LOOP_SKILL_MAX_ENTRY_BYTES = 2 * 1024 * 1024
# 解压后累计体积上限(防高压缩比 zip bomb:2MB 压缩包可膨胀到 GB 级)
LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED = 4 * 1024 * 1024
# 条目数上限(白名单不限数量,靠它挡"海量小文件"膨胀)
LOOP_SKILL_MAX_ENTRIES = 50
# version_label / notes 长度上限(且拒控制字符,防 header 注入 + DB 膨胀)
LOOP_SKILL_MAX_LABEL_LEN = 200
LOOP_SKILL_MAX_NOTES_LEN = 500
# zip 路径白名单
_ALLOWED_TOP = ("README.md",)
_ALLOWED_PREFIXES = ("commands/", "skills/")
_REQUIRED = ("commands/goal.md", "skills/geo-goal-orchestrator/SKILL.md")
# 启用/删除共用的应用级锁名
_ENABLE_LOCK = "geo_loop_skill_enable"


@dataclass(frozen=True)
class VersionMeta:
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_size: int
    is_enabled: bool
    is_deleted: bool
    uploaded_by_user_id: int | None
    notes: str | None
    created_at: datetime


def _to_meta(row: LoopSkillBundleVersion) -> VersionMeta:
    # 只读非 deferred 列 —— 不触发 files 加载
    return VersionMeta(
        id=row.id,
        version_label=row.version_label,
        bundle_sha256=row.bundle_sha256,
        file_count=row.file_count,
        total_size=row.total_size,
        is_enabled=row.is_enabled,
        is_deleted=row.is_deleted,
        uploaded_by_user_id=row.uploaded_by_user_id,
        notes=row.notes,
        created_at=row.created_at,
    )


def _row_to_bundle(row: LoopSkillBundleVersion) -> SkillBundle:
    files = [SkillFile(**f) for f in row.files]  # 此处才触发 deferred files 加载
    return SkillBundle(version=row.version_label, bundle_sha256=row.bundle_sha256, files=files)


def get_active_bundle(session: Session) -> SkillBundle:
    # order_by(id.desc()).first() —— 不用 .one():容忍并发窗口内瞬时双启用,取最新一条
    row = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.is_enabled.is_(True))
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .first()
    )
    return _row_to_bundle(row) if row is not None else build_bundle()


def get_bundle_by_id(session: Session, version_id: int) -> SkillBundle:
    row = session.get(LoopSkillBundleVersion, version_id)
    if row is None or row.is_deleted:
        raise ValidationError(f"skill 包版本不存在或已删除: {version_id}")
    return _row_to_bundle(row)


def resolve_bundle_for_install(session: Session, version: str | None) -> SkillBundle:
    """install/download 的版本解析:空→启用版;纯数字→id;否则按 label 取最新未删。"""
    if not version:
        return get_active_bundle(session)
    if version.isdigit():
        return get_bundle_by_id(session, int(version))
    row = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.version_label == version)
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ValidationError(f"找不到 skill 包版本: {version}")
    return _row_to_bundle(row)


def list_versions(session: Session) -> list[VersionMeta]:
    rows = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .all()
    )
    return [_to_meta(r) for r in rows]


def _clean_text(value: str, *, field: str, max_len: int) -> str:
    """strip + 长度上限 + 拒控制字符(换行/回车/制表 / DEL)。

    version_label 会被下游放进 HTTP 响应头(Content-Disposition / X-Bundle-Version);
    含 \\r\\n 会造成 header 注入,含中文会让 latin-1 编码崩(500)。这里先把控制字符挡掉、
    限长;非 ASCII(中文)本身允许,由 router 侧 percent-encode / RFC5987 兜底(见 Task 6)。
    """
    v = value.strip()
    if len(v) > max_len:
        raise ValidationError(f"{field} 过长(上限 {max_len} 字符)")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in v):
        raise ValidationError(f"{field} 含非法控制字符")
    return v


def upload_version(
    session: Session,
    zip_bytes: bytes,
    *,
    version_label: str | None,
    notes: str | None,
    uploaded_by_user_id: int | None,
) -> VersionMeta:
    if len(zip_bytes) > LOOP_SKILL_MAX_ZIP_BYTES:
        raise ValidationError(f"zip 超过压缩体积上限 {LOOP_SKILL_MAX_ZIP_BYTES} 字节")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValidationError("不是合法的 zip 文件") from exc

    # C:条目数上限(白名单不限数量,先挡"海量小文件")
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > LOOP_SKILL_MAX_ENTRIES:
        raise ValidationError(f"zip 条目过多: {len(infos)}(上限 {LOOP_SKILL_MAX_ENTRIES})")

    raw: dict[str, bytes] = {}
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        # zip-slip / 绝对路径防护
        if name.startswith("/") or ".." in name.split("/"):
            raise ValidationError(f"非法路径(zip-slip): {info.filename}")
        # 白名单前缀(startswith 接受 tuple)
        if not (name in _ALLOWED_TOP or name.startswith(_ALLOWED_PREFIXES)):
            raise ValidationError(f"不允许的文件路径: {name}(仅 README.md / commands/ / skills/)")
        # C:重名路径静默覆盖会让 sha 与实际内容脱节(zip 允许同名条目)→ 直接拒
        if name in raw:
            raise ValidationError(f"zip 含重复路径: {name}")
        # C:单条目解压上限
        if info.file_size > LOOP_SKILL_MAX_ENTRY_BYTES:
            raise ValidationError(f"解压后单文件过大: {name}")
        data = zf.read(info)
        # C:累计解压体积上限(防高压缩比 zip bomb)
        total += len(data)
        if total > LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED:
            raise ValidationError(f"解压后累计体积超上限 {LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED} 字节")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"非 UTF-8 文本文件: {name}") from exc
        raw[name] = data

    missing = [r for r in _REQUIRED if r not in raw]
    if missing:
        raise ValidationError(f"缺少必需文件: {', '.join(missing)}")

    # B:label / notes 清洗(strip + 限长 + 拒控制字符);label strip 后空则回落时间戳
    label = (
        _clean_text(version_label, field="version_label", max_len=LOOP_SKILL_MAX_LABEL_LEN)
        if version_label
        else ""
    )
    if not label:
        label = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    clean_notes = (
        _clean_text(notes, field="notes", max_len=LOOP_SKILL_MAX_NOTES_LEN) if notes else None
    )
    bundle = build_bundle_from_file_map(raw, version=label)

    row = LoopSkillBundleVersion(
        version_label=label,
        bundle_sha256=bundle.bundle_sha256,
        files=[
            {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
            for f in bundle.files
        ],
        file_count=len(bundle.files),
        total_size=sum(f.size for f in bundle.files),
        is_enabled=False,
        is_deleted=False,
        uploaded_by_user_id=uploaded_by_user_id,
        notes=clean_notes,
    )
    session.add(row)
    session.flush()  # 拿到 row.id;commit 交给 get_db / add_audit_entry
    return _to_meta(row)


def enable_version(session: Session, version_id: int) -> None:
    """启用某版本(=回退)。单例不变式靠 MySQL GET_LOCK 应用级锁保证。

    ⚠️ 连接亲和(A,评审必修):GET_LOCK 是**连接级**锁,而 `Session.commit()` 会把连接
    归还池、下一条语句可能换连接 → 若在 session 上 commit 再 RELEASE_LOCK,释放会落到
    **别的连接**、原锁泄漏在池里(后续任何 enable 都 10s→409)。因此显式独占一条 `Connection`,
    GET_LOCK → 存在性检查 → 两条 UPDATE → commit → RELEASE_LOCK **全在同一 conn**;
    `Connection.commit()` 不归还物理连接(与 `Session.commit()` 的关键区别),故安全。
    传入的 `session` 本函数不用于写,只供 router 事后 add_audit_entry。
    """
    with session.get_bind().connect() as conn:  # type: ignore[union-attr]
        got = conn.execute(text("SELECT GET_LOCK(:k, 10)"), {"k": _ENABLE_LOCK}).scalar()
        if got != 1:
            raise ConflictError("启用操作繁忙,请稍后重试")
        try:
            row = conn.execute(
                select(LoopSkillBundleVersion.id, LoopSkillBundleVersion.is_deleted).where(
                    LoopSkillBundleVersion.id == version_id
                )
            ).first()
            if row is None or row.is_deleted:
                raise ValidationError(f"版本不存在或已删除: {version_id}")
            # 从零启用态并发也安全:两者都在锁内串行,后者 UPDATE 前能看到前者已提交的启用行
            conn.execute(
                update(LoopSkillBundleVersion)
                .where(LoopSkillBundleVersion.is_enabled.is_(True))
                .values(is_enabled=False)
            )
            conn.execute(
                update(LoopSkillBundleVersion)
                .where(LoopSkillBundleVersion.id == version_id)
                .values(is_enabled=True)
            )
            conn.commit()  # 同一 conn 上提交,锁仍持有;下一个 GET_LOCK 持有者看到最新状态
        finally:
            conn.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": _ENABLE_LOCK})
    # with 退出 → conn.close() 兜底释放该连接上的所有命名锁(双保险)


def soft_delete_version(session: Session, version_id: int) -> None:
    """逻辑删除。拒删当前启用版(与 enable 同锁,消 check-then-act 竞态)。

    同 enable:GET_LOCK/updates/commit/RELEASE 全钉在独占的一条 Connection 上(见上)。
    """
    with session.get_bind().connect() as conn:  # type: ignore[union-attr]
        got = conn.execute(text("SELECT GET_LOCK(:k, 10)"), {"k": _ENABLE_LOCK}).scalar()
        if got != 1:
            raise ConflictError("操作繁忙,请稍后重试")
        try:
            row = conn.execute(
                select(
                    LoopSkillBundleVersion.id,
                    LoopSkillBundleVersion.is_deleted,
                    LoopSkillBundleVersion.is_enabled,
                ).where(LoopSkillBundleVersion.id == version_id)
            ).first()
            if row is None or row.is_deleted:
                raise ValidationError(f"版本不存在或已删除: {version_id}")
            if row.is_enabled:
                raise ConflictError("不能删除当前启用版,请先启用别的版本再删")
            conn.execute(
                update(LoopSkillBundleVersion)
                .where(LoopSkillBundleVersion.id == version_id)
                .values(is_deleted=True)
            )
            conn.commit()
        finally:
            conn.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": _ENABLE_LOCK})
