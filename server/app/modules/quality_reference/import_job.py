"""qref 外部参考异步导入：job 生命周期 + 后台 worker（镜像 video/service.py）。

建 job 秒回（不受 MCP 30s 约束）→ daemon 线程自开 session 跑：抠图 URL → 逐图
下载(SSRF+超时)+去重入 MinIO → 改写 markdown 为内链 → import_external 落库 → 写
image_link → done。事务顺序见 plan Global Constraints，避开 _insert_idempotent 回滚。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.modules.quality_reference import image_store, service
from server.app.modules.quality_reference.fetch import ImageFetchError, download_image
from server.app.modules.quality_reference.models import (
    QualityReferenceImageLink,
    QualityReferenceImportJob,
)
from server.app.modules.quality_reference.schemas import ImportExternalReferenceRequest
from server.app.shared.errors import ClientError, ValidationError

logger = logging.getLogger(__name__)

# 由 create_app() 注入（与 scheme_router / pipelines.router / video.service 同款）
bg_session_factory: Callable[[], Any] | None = None

_OPERATOR_USER_ID = 1  # Loop 身份（admin），added_by_user_id 可空、admin 兜底

# 并发闸（护连接池）：同时至多 N 个导入 job 真跑，其余 daemon 线程 park 排队、不占 DB
# 连接。仿 pipelines 的 GEO_PIPELINE_MAX_CONCURRENT_RUNS；非互斥锁，只限吞吐不串行化。
_MAX_CONCURRENT = int(os.environ.get("GEO_QREF_IMPORT_MAX_CONCURRENT", "3"))
_IMPORT_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT)

# markdown 图片：![alt](url) 或 ![alt](url "title")；取 url token（到空白/右括号止）
_IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")


def extract_image_urls(markdown: str) -> list[str]:
    """按出现顺序去重返回 markdown 内的图片 URL（仅 inline ![](url) 语法）。"""
    seen: dict[str, None] = {}
    for m in _IMG_RE.finditer(markdown or ""):
        seen.setdefault(m.group(1), None)
    return list(seen.keys())


def rewrite_image_urls(markdown: str, mapping: dict[str, str]) -> str:
    """把 markdown 里命中 mapping 的图片 URL 替换为内链；未命中原样保留。"""

    def _sub(m: re.Match) -> str:
        url = m.group(1)
        new = mapping.get(url)
        return m.group(0).replace(url, new) if new else m.group(0)

    return _IMG_RE.sub(_sub, markdown or "")


def create_import_job(
    db: Session, req: ImportExternalReferenceRequest
) -> QualityReferenceImportJob:
    """校验 + 插 pending + commit，秒回。"""
    if not req.source_url.strip():
        raise ValidationError("source_url 必填")
    job = QualityReferenceImportJob(
        job_id=uuid.uuid4().hex,
        status="pending",
        progress=0.0,
        title=req.title,
        markdown=req.markdown,
        platform=req.platform,
        source_url=req.source_url,
        category=req.category,
        question_texts=req.question_texts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_import_job(db: Session, job_id: str) -> QualityReferenceImportJob | None:
    return db.query(QualityReferenceImportJob).filter_by(job_id=job_id).first()


def _link_image(db: Session, reference_id: int, image_id: int) -> None:
    """幂等建 reference↔image 关联（UNIQUE(reference_id,image_id) 兜底）。"""
    exists_row = (
        db.query(QualityReferenceImageLink)
        .filter_by(reference_id=reference_id, image_id=image_id)
        .first()
    )
    if exists_row is not None:
        return
    db.add(QualityReferenceImageLink(reference_id=reference_id, image_id=image_id))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()  # 并发已建同关联 → 忽略


def run_import_job(job_id: str, session_factory) -> None:
    """后台线程入口：先抢并发闸 → 自开 session 跑整条 pipeline。异常兜底写 failed。

    抢闸在开 session 之前：排队线程只被 park、不占 DB 连接（护连接池）。job 行此时仍是
    pending，轮询看到 pending→running→done，语义正确。释放挪到外层 finally：即使
    session_factory() 或内层 db.close() 抛异常，许可也保证释放（BoundedSemaphore 不
    自愈，漏放会永久收窄并发闸）。
    """
    _IMPORT_SEMAPHORE.acquire()
    try:
        db = session_factory()
        try:
            job = db.query(QualityReferenceImportJob).filter_by(job_id=job_id).one()
            job.status = "running"
            db.commit()

            urls = extract_image_urls(job.markdown)
            job.images_total = len(urls)
            db.commit()

            # ── 阶段 A：逐图下载+去重入库，各自 commit（先落地，避免后续回滚冲掉）──
            mapping: dict[str, str] = {}
            image_ids: list[int] = []
            rehosted = skipped = 0
            for i, url in enumerate(urls):
                try:
                    data, mime = download_image(url)
                    img = image_store.ingest_image(db, data, mime)
                    db.commit()
                    mapping[url] = image_store.internal_url(img.id)
                    image_ids.append(img.id)
                    rehosted += 1
                except ImageFetchError as exc:
                    db.rollback()
                    skipped += 1
                    logger.warning("qref 图片跳过 job=%s url=%s: %s", job_id, url, exc)
                job.images_rehosted = rehosted
                job.images_skipped = skipped
                job.progress = round((i + 1) / (len(urls) + 1), 3)
                db.commit()

            # ── 阶段 B：改写 markdown → import_external 落库 + commit ──
            rewritten = rewrite_image_urls(job.markdown, mapping)
            ref, _similar = service.import_external(
                db,
                user_id=_OPERATOR_USER_ID,
                title=job.title,
                category=job.category,
                source_url=job.source_url,
                platform=job.platform,
                question_texts=job.question_texts,
                markdown=rewritten,
            )
            db.commit()  # import_external 只 flush，这里提交（dup 复活也返回有效 ref）

            # ── 阶段 C：写 image_link（含 dup 复活场景，关联挂到已有 ref）+ commit ──
            for image_id in image_ids:
                _link_image(db, ref.id, image_id)
            job.reference_id = ref.id
            job.progress = 1.0
            job.status = "done"
            db.commit()
        except Exception as exc:  # noqa: BLE001 — 后台线程兜底
            logger.exception("qref 外部参考导入失败: job_id=%s", job_id)
            db.rollback()
            try:
                job = db.query(QualityReferenceImportJob).filter_by(job_id=job_id).one()
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
                db.commit()
            except Exception:
                logger.exception("写 failed 状态也失败: job_id=%s", job_id)
        finally:
            db.close()
    finally:
        _IMPORT_SEMAPHORE.release()


def spawn_import_job(job_id: str) -> None:
    if bg_session_factory is None:
        raise ClientError("bg_session_factory 未注入（create_app 未执行？）")
    factory = bg_session_factory
    threading.Thread(target=run_import_job, args=(job_id, factory), daemon=True).start()
