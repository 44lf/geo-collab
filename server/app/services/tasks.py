"""
任务执行引擎 — Geo Collab 的核心。

架构概览：

  Route handler (POST /api/tasks/{id}/execute)
    → threading.Thread (后台异步执行)
      → execute_task()
        → _run_pending_records()
          并发循环：
            ThreadPoolExecutor (max 5 workers)
            → _start_runnable_records → _publish_record (后台线程)
              → ToutiaoPublisher.publish_article()
            → _finish_record_future() 处理结果
            → 继续处理下一条 pending record

  并发控制：
    - 每个任务一把 threading.Lock → 防止同一任务被同时执行
    - 每个账号一把 threading.Lock → 同一账号的 records 串行处理
    - 全局上限 MAX_CONCURRENT_RECORDS = 5

  状态流转：
    pending → _claim_record() → running → _finish_record_future()
      → succeeded / failed / waiting_manual_publish / waiting_user_input
      → _aggregate_task_status() 聚合为任务级状态

  人工介入（waiting_user_input）：
    ToutiaoUserInputRequired → 浏览器不关闭 → session 关联到 record
    → 用户处理完后 POST /api/publish-records/{id}/resolve-user-input
      → resolve_user_input_record() → stop 浏览器 → record 回到 pending
      → 后台自动继续执行

  崩溃恢复：
    recover_stuck_records() 在启动时运行
    → 将 lease_until < now 的 running 记录重置为 pending
"""
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError, wait
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete as sa_delete, select, update as sa_update
from sqlalchemy.orm import Session, selectinload

from server.app.core.config import get_settings
from server.app.core.time import utcnow
from server.app.models import (
    Account,
    Article,
    ArticleBodyAsset,
    ArticleGroup,
    ArticleGroupItem,
    Platform,
    PublishRecord,
    PublishTask,
    PublishTaskAccount,
    TaskLog,
)
from server.app.services.assets import store_bytes
from server.app.services.articles import article_has_publishable_body
from server.app.services.browser_sessions import (
    associate_record_with_session,
    disassociate_record,
    get_session_for_record,
    stop_remote_browser_session,
)
from server.app.services.toutiao_publisher import ToutiaoPublisher, ToutiaoPublishError, ToutiaoUserInputRequired
from server.app.schemas.task import (
    PublishRecordRead,
    TaskAccountInput,
    TaskAccountRead,
    TaskAssignmentPreviewItemRead,
    TaskAssignmentPreviewRead,
    TaskCreate,
    TaskLogRead,
    TaskRead,
)

# 常量定义
VALID_TASK_TYPES = {"single", "group_round_robin"}
TERMINAL_TASK_STATUSES = {"succeeded", "partial_failed", "failed", "cancelled"}
PAUSED_RECORD_STATUSES = {"waiting_manual_publish", "waiting_user_input"}
ACTIVE_RECORD_STATUSES = {"running", *PAUSED_RECORD_STATUSES}
CAN_RETRY_TASK_STATUSES = {"failed", "partial_failed", "succeeded", "cancelled"}
MAX_CONCURRENT_RECORDS = 5

# 任务级互斥锁，防止同一任务被并发执行
# 锁对象永久保留（不 pop），避免 race condition
_task_locks: dict[int, threading.Lock] = {}
_account_locks: dict[int, threading.Lock] = {}
_account_locks_lock = threading.Lock()
_task_locks_lock = threading.Lock()  # 仅在测试中用于批量 clean

# 任务硬取消标志
_task_cancel: dict[int, threading.Event] = {}
RECORD_TIMEOUT_SECONDS = 300


def _max_concurrent_records() -> int:
    return max(1, min(int(get_settings().publish_max_concurrent_records), MAX_CONCURRENT_RECORDS))


@dataclass(frozen=True)
class RunningRecord:
    record_id: int
    account_id: int
    started_monotonic: float


# 验证通过后的任务输入
@dataclass(frozen=True)
class TaskInputs:
    platform: Platform
    accounts: list[tuple[int, Account]]
    article_ids: list[int]


# 文章-账号分配项
@dataclass(frozen=True)
class AssignmentItem:
    position: int
    article_id: int
    account_sort_order: int
    account: Account


# 获取所有任务列表
def list_tasks(db: Session) -> list[PublishTask]:
    stmt = (
        select(PublishTask)
        .options(
            selectinload(PublishTask.platform),
            selectinload(PublishTask.accounts).selectinload(PublishTaskAccount.account),
            selectinload(PublishTask.records),
        )
        .order_by(PublishTask.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


# 获取单个任务
def get_task(db: Session, task_id: int) -> PublishTask | None:
    stmt = (
        select(PublishTask)
        .where(PublishTask.id == task_id)
        .options(
            selectinload(PublishTask.platform),
            selectinload(PublishTask.accounts).selectinload(PublishTaskAccount.account),
            selectinload(PublishTask.records),
        )
    )
    return db.execute(stmt).scalar_one_or_none()


# 获取任务的发布记录列表
def list_task_records(db: Session, task_id: int) -> list[PublishRecord]:
    stmt = select(PublishRecord).where(PublishRecord.task_id == task_id).order_by(PublishRecord.id.asc())
    return list(db.execute(stmt).scalars().all())


# 获取任务的日志列表（支持增量拉取）
def list_task_logs(db: Session, task_id: int, after_id: int = 0, limit: int = 100) -> list[TaskLog]:
    stmt = (
        select(TaskLog)
        .where(TaskLog.task_id == task_id, TaskLog.id > after_id)
        .order_by(TaskLog.created_at.asc(), TaskLog.id.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


# 创建新任务
def delete_all_tasks(db: Session) -> None:
    db.execute(sa_delete(TaskLog))
    db.execute(sa_delete(PublishRecord))
    db.execute(sa_delete(PublishTaskAccount))
    db.execute(sa_delete(PublishTask))
    db.flush()


def create_task(db: Session, payload: TaskCreate) -> PublishTask:
    if payload.client_request_id:
        existing = db.execute(
            select(PublishTask).where(PublishTask.client_request_id == payload.client_request_id)
        ).scalar_one_or_none()
        if existing is not None:
            return get_task(db, existing.id) or existing

    inputs = _validated_task_inputs(db, payload)
    assignments = _build_assignments(inputs.article_ids, inputs.accounts)

    task = PublishTask(
        name=payload.name,
        task_type=payload.task_type,
        status="pending",
        platform_id=inputs.platform.id,
        article_id=payload.article_id if payload.task_type == "single" else None,
        group_id=payload.group_id if payload.task_type == "group_round_robin" else None,
        stop_before_publish=payload.stop_before_publish,
        client_request_id=payload.client_request_id,
    )
    db.add(task)
    db.flush()

    for sort_order, account in inputs.accounts:
        task.accounts.append(PublishTaskAccount(account_id=account.id, sort_order=sort_order))

    for assignment in assignments:
        task.records.append(
            PublishRecord(
                article_id=assignment.article_id,
                platform_id=inputs.platform.id,
                account_id=assignment.account.id,
                status="pending",
            )
        )

    db.flush()
    return get_task(db, task.id) or task


# 预览任务分配结果
def preview_task_assignment(db: Session, payload: TaskCreate) -> TaskAssignmentPreviewRead:
    inputs = _validated_task_inputs(db, payload)
    assignments = _build_assignments(inputs.article_ids, inputs.accounts)
    return TaskAssignmentPreviewRead(
        task_type=payload.task_type,
        platform_code=inputs.platform.code,
        article_count=len(inputs.article_ids),
        account_count=len(inputs.accounts),
        items=[
            TaskAssignmentPreviewItemRead(
                position=assignment.position,
                article_id=assignment.article_id,
                account_id=assignment.account.id,
                account_sort_order=assignment.account_sort_order,
            )
            for assignment in assignments
        ],
    )


# 获取单个发布记录
def get_record(db: Session, record_id: int) -> PublishRecord | None:
    return db.get(PublishRecord, record_id)


# 执行任务：并发处理所有待处理的发布记录
def execute_task(db: Session, task: PublishTask) -> PublishTask:
    """
    任务执行入口。

    同步方法（在后台线程中调用）：
      1. 获取任务锁（threading.Lock），防止并发执行同一任务
      2. 状态 pending → running（通过 UPDATE ... WHERE status='pending' 保证原子性）
      3. 调用 _run_pending_records() 并发处理 records
      4. 执行完后 _aggregate_task_status() 聚合任务级状态
    """
    lock = _task_locks.setdefault(task.id, threading.Lock())
    locked = lock.acquire(blocking=False)
    if not locked:
        raise ValueError(f"Task {task.id} is already being executed")

    cancel_event = threading.Event()
    _task_cancel[task.id] = cancel_event

    try:
        if task.status in TERMINAL_TASK_STATUSES:
            raise ValueError(f"Task is already terminal: {task.status}")

        now = utcnow()
        if task.status == "pending":
            stmt = (
                sa_update(PublishTask)
                .where(PublishTask.id == task.id, PublishTask.status == "pending")
                .values(status="running", started_at=now)
            )
            if db.execute(stmt).rowcount == 0:
                db.flush()
                refreshed = get_task(db, task.id)
                if refreshed is None or refreshed.status in TERMINAL_TASK_STATUSES:
                    return refreshed or task
                task = refreshed
            else:
                task.status = "running"
                task.started_at = now
            _add_log(db, task.id, None, "info", "Task started")

        _run_pending_records(db, task)
        db.flush()
        return get_task(db, task.id) or task
    finally:
        if locked:
            lock.release()
        _task_cancel.pop(task.id, None)


# 内部方法：循环执行待处理的发布记录
def _run_pending_records(db: Session, task: PublishTask) -> None:
    cancel_evt = _task_cancel.get(task.id)
    running: dict[Future, RunningRecord] = {}
    executor = ThreadPoolExecutor(max_workers=_max_concurrent_records())

    try:
        while True:
            if cancel_evt and cancel_evt.is_set():
                _add_log(db, task.id, None, "warn", "Task cancelled during execution")
                break

            if task.stop_before_publish:
                records = list_task_records(db, task.id)
                if any(record.status == "waiting_manual_publish" for record in records):
                    db.commit()
                    return
            else:
                records = list_task_records(db, task.id)

            if any(record.status == "waiting_user_input" for record in records):
                db.commit()
                return

            _start_runnable_records(db, task, executor, running)

            if not running:
                records = list_task_records(db, task.id)
                if not any(record.status == "pending" for record in records):
                    _aggregate_task_status(task, records)
                    db.commit()
                    return
                db.commit()
                time.sleep(0.2)
                continue

            done, _ = wait(running.keys(), timeout=1, return_when=FIRST_COMPLETED)
            timed_out = [
                future
                for future, running_record in running.items()
                if time.monotonic() - running_record.started_monotonic > RECORD_TIMEOUT_SECONDS
            ]
            for future in set(done) | set(timed_out):
                running_record = running.pop(future)
                if future in timed_out and not future.done():
                    _mark_record_failed(db, task.id, running_record.record_id, "Timeout: record execution exceeded 300s")
                    _release_account_lock(running_record.account_id)
                    db.commit()
                    continue
                _finish_record_future(db, task, running_record.record_id, future)
                _release_account_lock(running_record.account_id)
                db.commit()
    finally:
        for running_record in running.values():
            _release_account_lock(running_record.account_id)
        executor.shutdown(wait=False, cancel_futures=True)


def _start_runnable_records(
    db: Session,
    task: PublishTask,
    executor: ThreadPoolExecutor,
    running: dict[Future, RunningRecord],
) -> None:
    running_accounts = {item.account_id for item in running.values()}
    blocked_accounts: set[int] = set()
    slots = _max_concurrent_records() - len(running)
    if task.stop_before_publish:
        slots = min(slots, 1)
    if slots <= 0:
        return

    while slots > 0:
        db.flush()
        records = list_task_records(db, task.id)
        next_record = next(
            (
                record
                for record in records
                if record.status == "pending"
                and record.account_id not in running_accounts
                and record.account_id not in blocked_accounts
            ),
            None,
        )
        if next_record is None:
            return

        if not _try_acquire_account_lock(next_record.account_id):
            blocked_accounts.add(next_record.account_id)
            continue

        try:
            if not _claim_record(db, task.id, next_record):
                _release_account_lock(next_record.account_id)
                continue

            article = _load_article_for_publish(db, next_record.article_id)
            account = db.get(Account, next_record.account_id)
            validation_error = _validate_record_inputs(article, account)
            if validation_error:
                _mark_record_failed(db, task.id, next_record.id, validation_error)
                _release_account_lock(next_record.account_id)
                db.commit()
                continue

            _detach_record_inputs(db, next_record, article, account)
            future = executor.submit(_publish_record, next_record, article, account, task.stop_before_publish)
            running[future] = RunningRecord(next_record.id, next_record.account_id, time.monotonic())
            running_accounts.add(next_record.account_id)
            slots -= 1
            db.commit()
        except Exception:
            _release_account_lock(next_record.account_id)
            raise


def _try_acquire_account_lock(account_id: int) -> bool:
    with _account_locks_lock:
        lock = _account_locks.setdefault(account_id, threading.Lock())
    return lock.acquire(blocking=False)


def _release_account_lock(account_id: int) -> None:
    lock = _account_locks.get(account_id)
    if lock is not None and lock.locked():
        try:
            lock.release()
        except RuntimeError:
            pass


def _claim_record(db: Session, task_id: int, record: PublishRecord) -> bool:
    now = utcnow()
    lease_until = now + timedelta(seconds=RECORD_TIMEOUT_SECONDS + 60)
    stmt = (
        sa_update(PublishRecord)
        .where(PublishRecord.id == record.id, PublishRecord.status == "pending")
        .values(status="running", started_at=now, lease_until=lease_until)
    )
    if db.execute(stmt).rowcount == 0:
        db.commit()
        return False
    record.status = "running"
    record.started_at = now
    record.lease_until = lease_until
    _add_log(db, task_id, record.id, "info", f"Record {record.id} started")
    return True


def _load_article_for_publish(db: Session, article_id: int) -> Article | None:
    return db.execute(
        select(Article)
        .where(Article.id == article_id)
        .options(
            selectinload(Article.cover_asset),
            selectinload(Article.body_assets).selectinload(ArticleBodyAsset.asset),
        )
    ).scalar_one_or_none()


def _validate_record_inputs(article: Article | None, account: Account | None) -> str | None:
    if article is None or account is None:
        return "Record article or account not found"
    if not article.title or not article.title.strip():
        return "文章标题不能为空"
    if not article_has_publishable_body(article):
        return "文章正文不能为空"
    if article.cover_asset_id is None:
        return "文章封面不能为空"
    if account.status != "valid":
        return f"Account is not valid: {account.id}"
    return None


def _detach_record_inputs(db: Session, record: PublishRecord, article: Article, account: Account) -> None:
    objects: list[object] = [record, article, account]
    if article.cover_asset is not None:
        objects.append(article.cover_asset)
    for link in article.body_assets:
        objects.append(link)
        if link.asset is not None:
            objects.append(link.asset)
    for obj in objects:
        if obj in db:
            db.expunge(obj)


def _publish_record(record: PublishRecord, article: Article, account: Account, stop_before_publish: bool):
    publisher = build_publisher_for_record(record)
    return publisher.publish_article(article, account, stop_before_publish=stop_before_publish)


def _finish_record_future(db: Session, task: PublishTask, record_id: int, future: Future) -> None:
    """
    处理单条 record 的发布结果。

    ThreadPoolExecutor 的 future 完成后调用此方法：
      - 正常结束 → 标记 succeeded / waiting_manual_publish
      - ToutiaoUserInputRequired → 标记 waiting_user_input + 关联 remote session
      - 其他异常 → 标记 failed（附失败截图 asset_id）
    """
    try:
        result = future.result()
    except FutureTimeoutError:
        _mark_record_failed(db, task.id, record_id, "Timeout: record execution exceeded 300s")
    except ToutiaoUserInputRequired as exc:
        # 人工介入：保持浏览器打开，将 session 关联到 record
        # 前端会读取 record.novnc_url 展示"打开远程浏览器"按钮
        screenshot_asset_id = _store_failure_screenshot(db, task.id, record_id, exc.screenshot)
        _mark_record_waiting_user_input(db, task.id, record_id, str(exc), screenshot_asset_id=screenshot_asset_id)
        if exc.session_id:
            associate_record_with_session(record_id, exc.session_id)
    except ToutiaoPublishError as exc:
        screenshot_asset_id = _store_failure_screenshot(db, task.id, record_id, exc.screenshot)
        _mark_record_failed(db, task.id, record_id, str(exc), screenshot_asset_id=screenshot_asset_id)
    except ValueError as exc:
        _mark_record_failed(db, task.id, record_id, str(exc))
    except Exception as exc:
        _mark_record_failed(db, task.id, record_id, f"Unexpected error: {exc}")
    else:
        if task.stop_before_publish:
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == record_id, PublishRecord.status == "running")
                .values(status="waiting_manual_publish", finished_at=utcnow(), lease_until=None)
            )
            message = "等待手动确认发布"
        else:
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == record_id, PublishRecord.status == "running")
                .values(status="succeeded", publish_url=result.url or None, finished_at=utcnow(), lease_until=None)
            )
            message = result.message
        if db.execute(stmt).rowcount > 0:
            _add_log(db, task.id, record_id, "info", message)


def _mark_record_failed(
    db: Session,
    task_id: int,
    record_id: int,
    error_message: str,
    screenshot_asset_id: str | None = None,
) -> None:
    stmt = (
        sa_update(PublishRecord)
        .where(PublishRecord.id == record_id)
        .values(status="failed", error_message=error_message, finished_at=utcnow(), lease_until=None)
    )
    db.execute(stmt)
    _add_log(db, task_id, record_id, "error", error_message, screenshot_asset_id=screenshot_asset_id)


def _mark_record_waiting_user_input(
    db: Session,
    task_id: int,
    record_id: int,
    message: str,
    screenshot_asset_id: str | None = None,
) -> None:
    stmt = (
        sa_update(PublishRecord)
        .where(PublishRecord.id == record_id, PublishRecord.status == "running")
        .values(status="waiting_user_input", error_message=message, finished_at=None, lease_until=None)
    )
    db.execute(stmt)
    _add_log(db, task_id, record_id, "warn", message, screenshot_asset_id=screenshot_asset_id)


def _run_next_pending_record(db: Session, task: PublishTask, records: list[PublishRecord] | None = None) -> None:
    if records is None:
        db.flush()
        records = list_task_records(db, task.id)

    cancel_evt = _task_cancel.get(task.id)

    while True:
        if cancel_evt and cancel_evt.is_set():
            _add_log(db, task.id, None, "warn", "Task cancelled during execution")
            break

        next_record = next((r for r in records if r.status == "pending"), None)
        if next_record is None:
            _aggregate_task_status(task, records)
            return

        now = utcnow()
        record_id = next_record.id
        article_id = next_record.article_id
        account_id = next_record.account_id
        lease_until = utcnow() + timedelta(minutes=10)
        stmt = (
            sa_update(PublishRecord)
            .where(PublishRecord.id == record_id, PublishRecord.status == "pending")
            .values(status="running", started_at=now, lease_until=lease_until)
        )
        if db.execute(stmt).rowcount == 0:
            db.commit()
            records = list_task_records(db, task.id)
            continue
        next_record.status = "running"
        next_record.started_at = now
        next_record.lease_until = lease_until
        _add_log(db, task.id, next_record.id, "info", f"Record {next_record.id} started")
        # Phase 1: Commit running state (short transaction)
        db.commit()

        article = db.execute(
            select(Article)
            .where(Article.id == article_id)
            .options(
                selectinload(Article.cover_asset),
                selectinload(Article.body_assets).selectinload(ArticleBodyAsset.asset),
            )
        ).scalar_one_or_none()
        account = db.get(Account, account_id)
        if not (article is not None and account is not None):
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == next_record.id)
                .values(status="failed", error_message="Record article or account not found", finished_at=utcnow(), lease_until=None)
            )
            db.execute(stmt)
            _add_log(db, task.id, next_record.id, "error", "Record article or account not found")
            db.commit()
            continue

        # Phase 2: Playwright execution (no DB transaction wrapping)
        executor = None
        try:
            if not article.title or not article.title.strip():
                raise ValueError("文章标题不能为空")
            if not article_has_publishable_body(article):
                raise ValueError("文章正文不能为空")
            if article.cover_asset_id is None:
                raise ValueError("文章封面不能为空")
            if account.status != "valid":
                raise ValueError(f"Account is not valid: {account.id}")

            publisher = build_publisher_for_record(next_record)
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(
                publisher.publish_article,
                article, account,
                stop_before_publish=task.stop_before_publish,
            )
            result = future.result(timeout=RECORD_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == next_record.id)
                .values(status="failed", error_message="Timeout: record execution exceeded 300s", finished_at=utcnow(), lease_until=None)
            )
            db.execute(stmt)
            _add_log(db, task.id, next_record.id, "error", "Timeout: record execution exceeded 300s")
            db.commit()
            continue
        except ToutiaoUserInputRequired as exc:
            screenshot_asset_id = _store_failure_screenshot(db, task.id, next_record.id, exc.screenshot)
            _mark_record_waiting_user_input(db, task.id, next_record.id, str(exc), screenshot_asset_id=screenshot_asset_id)
            if exc.session_id:
                associate_record_with_session(next_record.id, exc.session_id)
            db.commit()
            continue
        except ToutiaoPublishError as exc:
            screenshot_asset_id = _store_failure_screenshot(db, task.id, next_record.id, exc.screenshot)
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == next_record.id)
                .values(status="failed", error_message=str(exc), finished_at=utcnow(), lease_until=None)
            )
            db.execute(stmt)
            _add_log(db, task.id, next_record.id, "error", str(exc), screenshot_asset_id=screenshot_asset_id)
            db.commit()
            continue
        except ValueError as exc:
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == next_record.id)
                .values(status="failed", error_message=str(exc), finished_at=utcnow(), lease_until=None)
            )
            db.execute(stmt)
            _add_log(db, task.id, next_record.id, "error", str(exc))
            db.commit()
            continue
        except Exception as exc:
            _add_log(db, task.id, next_record.id, "error", f"Unexpected error: {exc}")
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == next_record.id)
                .values(status="failed", error_message=str(exc), finished_at=utcnow(), lease_until=None)
            )
            db.execute(stmt)
            db.commit()
            continue
        finally:
            if executor:
                executor.shutdown(wait=False)

        # Phase 3: Conditional update — only if still 'running'
        if task.stop_before_publish:
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == next_record.id, PublishRecord.status == "running")
                .values(status="waiting_manual_publish", finished_at=utcnow(), lease_until=None)
            )
        else:
            stmt = (
                sa_update(PublishRecord)
                .where(PublishRecord.id == next_record.id, PublishRecord.status == "running")
                .values(status="succeeded", publish_url=result.url or None, finished_at=utcnow(), lease_until=None)
            )
        if db.execute(stmt).rowcount > 0:
            _add_log(db, task.id, next_record.id, "info", result.message if not task.stop_before_publish else "等待手动确认发布")
        db.commit()
        if task.stop_before_publish:
            return


# 手动确认发布结果（仅对 waiting_manual_publish 状态的记录有效）
def manual_confirm_record(
    db: Session,
    record: PublishRecord,
    outcome: str,
    publish_url: str | None,
    error_message: str | None,
) -> PublishRecord:
    if record.status != "waiting_manual_publish":
        raise ValueError(f"Record is not waiting for manual confirm: {record.status}")
    if outcome not in {"succeeded", "failed"}:
        raise ValueError(f"Invalid outcome: {outcome}")

    record.status = outcome
    record.finished_at = utcnow()
    if outcome == "succeeded":
        record.publish_url = str(publish_url) if publish_url else None
        _add_log(db, record.task_id, record.id, "info", "Record manually confirmed as succeeded")
    else:
        record.error_message = error_message or "Manually marked as failed"
        _add_log(db, record.task_id, record.id, "warn", "Record manually confirmed as failed")

    task = get_task(db, record.task_id)
    if task is not None:
        records = list_task_records(db, task.id)
        _aggregate_task_status(task, records)

    db.flush()
    return record


# 重试失败的发布记录
def resolve_user_input_record(db: Session, record: PublishRecord) -> PublishRecord:
    if record.status != "waiting_user_input":
        raise ValueError(f"Record is not waiting for user input: {record.status}")

    session = get_session_for_record(record.id)
    if session:
        stop_remote_browser_session(session.id)
    disassociate_record(record.id)

    record.status = "pending"
    record.error_message = None
    record.started_at = None
    record.finished_at = None
    record.lease_until = None
    _add_log(db, record.task_id, record.id, "info", "User input resolved; record requeued")

    task = get_task(db, record.task_id)
    if task is not None and task.status not in TERMINAL_TASK_STATUSES:
        task.status = "running"
        task.finished_at = None

    db.flush()
    return record


def retry_record(db: Session, record: PublishRecord) -> PublishRecord:
    if record.status != "failed":
        raise ValueError(f"Only failed records can be retried: {record.status}")
    if record.retry_of_record_id is not None:
        raise ValueError("Retry records cannot be retried again; create a new task after checking the platform result")

    existing_retry = db.execute(
        select(PublishRecord).where(PublishRecord.retry_of_record_id == record.id)
    ).scalar_one_or_none()
    if existing_retry is not None:
        raise ValueError(f"Record {record.id} already has retry record {existing_retry.id}")

    conflicting_record = db.execute(
        select(PublishRecord)
        .where(
            PublishRecord.task_id == record.task_id,
            PublishRecord.article_id == record.article_id,
            PublishRecord.account_id == record.account_id,
            PublishRecord.id != record.id,
            PublishRecord.status.in_(["pending", "running", "waiting_manual_publish", "waiting_user_input", "succeeded"]),
        )
        .order_by(PublishRecord.id.asc())
    ).scalar_one_or_none()
    if conflicting_record is not None:
        raise ValueError(
            f"Article/account already has record {conflicting_record.id} in status {conflicting_record.status}"
        )

    new_record = PublishRecord(
        task_id=record.task_id,
        article_id=record.article_id,
        platform_id=record.platform_id,
        account_id=record.account_id,
        status="pending",
        retry_of_record_id=record.id,
    )
    db.add(new_record)

    task = get_task(db, record.task_id)
    if task is not None and task.status in CAN_RETRY_TASK_STATUSES:
        task.status = "running"
        task.finished_at = None
        _add_log(db, task.id, None, "info", f"Task reopened for retry of record {record.id}")

    db.flush()
    return new_record


# 为记录构建发布器实例（由子类或 mock 重写）
def build_publisher_for_record(record: PublishRecord) -> ToutiaoPublisher:
    settings = get_settings()
    return ToutiaoPublisher(
        channel=settings.publish_browser_channel,
        executable_path=settings.publish_browser_executable_path,
    )


# 存储失败截图并返回资源 ID
def _store_failure_screenshot(
    db: Session,
    task_id: int,
    record_id: int,
    screenshot: bytes | None,
) -> str | None:
    if not screenshot:
        return None
    stored = store_bytes(
        db,
        screenshot,
        filename=f"task-{task_id}-record-{record_id}-failure.png",
        content_type="image/png",
    )
    return stored.asset.id


# 取消任务
def cancel_task(db: Session, task: PublishTask) -> PublishTask:
    """
    取消任务。

    对于 waiting_user_input 状态的 record：
      1. stop 关联的远程浏览器 session（关闭 Xvfb + noVNC）
      2. 解除 record↔session 关联
    然后再标记 cancelled。
    """
    if task.status in TERMINAL_TASK_STATUSES:
        return task

    # 信号硬取消（正在执行的 record 会收到中断）
    evt = _task_cancel.get(task.id)
    if evt:
        evt.set()

    now = utcnow()
    records = list_task_records(db, task.id)
    task.status = "cancelled"
    task.finished_at = now
    for record in records:
        was_waiting_user_input = record.status == "waiting_user_input"
        if record.status in {"pending", "running", "waiting_user_input"}:
            record.status = "cancelled"
            record.finished_at = now
        if was_waiting_user_input:
            session = get_session_for_record(record.id)
            if session:
                stop_remote_browser_session(session.id)
            disassociate_record(record.id)
    _add_log(db, task.id, None, "warn", "Task cancelled")
    db.flush()
    return get_task(db, task.id) or task


# 根据所有记录的状态聚合计算任务级状态
def _aggregate_task_status(task: PublishTask, records: list[PublishRecord]) -> None:
    now = utcnow()
    if not records:
        task.status = "failed"
        task.finished_at = now
        return
    if any(r.status in {"pending", "running", "waiting_manual_publish", "waiting_user_input"} for r in records):
        return  # 任务尚未结束，保持当前状态
    if all(r.status == "succeeded" for r in records):
        task.status = "succeeded"
        task.finished_at = now
    elif any(r.status == "failed" for r in records):
        task.status = "partial_failed" if any(r.status == "succeeded" for r in records) else "failed"
        task.finished_at = now


# 添加任务日志
def _add_log(
    db: Session,
    task_id: int,
    record_id: int | None,
    level: str,
    message: str,
    screenshot_asset_id: str | None = None,
) -> None:
    db.add(
        TaskLog(
            task_id=task_id,
            record_id=record_id,
            level=level,
            message=message,
            screenshot_asset_id=screenshot_asset_id,
        )
    )


# 校验任务输入参数
def _validated_task_inputs(db: Session, payload: TaskCreate) -> TaskInputs:
    if payload.task_type not in VALID_TASK_TYPES:
        raise ValueError(f"Invalid task_type: {payload.task_type}")

    platform = db.execute(select(Platform).where(Platform.code == payload.platform_code)).scalar_one_or_none()
    if platform is None:
        raise ValueError(f"Platform not found: {payload.platform_code}")

    ordered_accounts = _validated_accounts(db, platform.id, payload.accounts)
    article_ids = _article_ids_for_task(db, payload)
    _validate_unique_articles(article_ids)

    if payload.task_type == "single" and len(ordered_accounts) != 1:
        raise ValueError("Single task requires exactly one account")

    return TaskInputs(platform=platform, accounts=ordered_accounts, article_ids=article_ids)


# 构建文章-账号分配列表（轮询算法）
def _build_assignments(article_ids: list[int], accounts: list[tuple[int, Account]]) -> list[AssignmentItem]:
    return [
        AssignmentItem(
            position=index,
            article_id=article_id,
            account_sort_order=accounts[index % len(accounts)][0],
            account=accounts[index % len(accounts)][1],
        )
        for index, article_id in enumerate(article_ids)
    ]


# 校验并排序账号列表
def _validated_accounts(
    db: Session,
    platform_id: int,
    account_inputs: list[TaskAccountInput],
) -> list[tuple[int, Account]]:
    if not account_inputs:
        raise ValueError("At least one account is required")

    seen: set[int] = set()
    ordered_inputs: list[tuple[int, int]] = []
    for index, item in enumerate(account_inputs):
        if item.account_id in seen:
            raise ValueError(f"Duplicate account_id: {item.account_id}")
        seen.add(item.account_id)
        ordered_inputs.append((item.sort_order if item.sort_order is not None else index, item.account_id))
    ordered_inputs.sort(key=lambda item: item[0])

    account_ids = [account_id for _, account_id in ordered_inputs]
    accounts = {
        account.id: account
        for account in db.execute(select(Account).where(Account.id.in_(account_ids))).scalars().all()
    }
    ordered_accounts: list[tuple[int, Account]] = []
    for sort_order, account_id in ordered_inputs:
        account = accounts.get(account_id)
        if account is None:
            raise ValueError(f"Account not found: {account_id}")
        if account.platform_id != platform_id:
            raise ValueError(f"Account platform mismatch: {account_id}")
        if account.status != "valid":
            raise ValueError(f"Account is not valid: {account_id}")
        ordered_accounts.append((sort_order, account))
    return ordered_accounts


# 校验文章 ID 不重复
def _validate_unique_articles(article_ids: list[int]) -> None:
    if len(article_ids) != len(set(article_ids)):
        raise ValueError("Duplicate article_id in task assignment")


# 根据任务类型获取文章 ID 列表
def _article_ids_for_task(db: Session, payload: TaskCreate) -> list[int]:
    if payload.task_type == "single":
        if payload.article_id is None:
            raise ValueError("article_id is required for single task")
        if db.get(Article, payload.article_id) is None:
            raise ValueError(f"Article not found: {payload.article_id}")
        return [payload.article_id]

    if payload.group_id is None:
        raise ValueError("group_id is required for group_round_robin task")
    group = db.get(ArticleGroup, payload.group_id)
    if group is None:
        raise ValueError(f"Article group not found: {payload.group_id}")
    items = list(
        db.execute(
            select(ArticleGroupItem)
            .where(ArticleGroupItem.group_id == payload.group_id)
            .order_by(ArticleGroupItem.sort_order.asc())
        )
        .scalars()
        .all()
    )
    if not items:
        raise ValueError("Article group has no articles")
    return [item.article_id for item in items]


# 将 ORM PublishTask 转为响应体
def to_task_read(task: PublishTask) -> TaskRead:
    accounts = sorted(task.accounts, key=lambda item: item.sort_order)
    return TaskRead(
        id=task.id,
        name=task.name,
        task_type=task.task_type,
        status=task.status,
        platform_id=task.platform_id,
        platform_code=task.platform.code,
        article_id=task.article_id,
        group_id=task.group_id,
        stop_before_publish=task.stop_before_publish,
        accounts=[
            TaskAccountRead(
                account_id=item.account_id,
                sort_order=item.sort_order,
                display_name=item.account.display_name,
                status=item.account.status,
            )
            for item in accounts
        ],
        record_count=len(task.records),
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


# 将 ORM PublishRecord 转为响应体
def to_record_read(record: PublishRecord) -> PublishRecordRead:
    """
    序列化 PublishRecord → PublishRecordRead（API 响应）。

    额外填充 remote_browser_session_id 和 novnc_url：
      从 _record_to_session 映射中查找 record 关联的远程浏览器 session，
      如果有则暴露 novnc_url 给前端显示"打开远程浏览器"按钮。
    """
    session = get_session_for_record(record.id)
    return PublishRecordRead(
        id=record.id,
        task_id=record.task_id,
        article_id=record.article_id,
        platform_id=record.platform_id,
        account_id=record.account_id,
        status=record.status,
        publish_url=record.publish_url,
        error_message=record.error_message,
        retry_of_record_id=record.retry_of_record_id,
        started_at=record.started_at,
        finished_at=record.finished_at,
        lease_until=record.lease_until,
        remote_browser_session_id=session.id if session else None,
        novnc_url=session.novnc_url if session else None,
    )


# 将 ORM TaskLog 转为响应体
def to_log_read(log: TaskLog) -> TaskLogRead:
    return TaskLogRead(
        id=log.id,
        task_id=log.task_id,
        record_id=log.record_id,
        level=log.level,
        message=log.message,
        screenshot_asset_id=log.screenshot_asset_id,
        created_at=log.created_at,
    )


def recover_stuck_records(db: Session) -> None:
    """启动时恢复卡住的记录：status='running' 且 lease_until < utcnow()。"""
    now = utcnow()
    records = list(
        db.execute(
            select(PublishRecord).where(
                PublishRecord.status == "running",
                PublishRecord.lease_until < now,
            )
        ).scalars().all()
    )
    for record in records:
        record.status = "pending"
        record.lease_until = None
    if records:
        db.commit()
