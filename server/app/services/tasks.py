import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update as sa_update
from sqlalchemy.orm import Session, selectinload

from server.app.core.time import utcnow
from server.app.models import (
    Account,
    Article,
    ArticleGroup,
    ArticleGroupItem,
    Platform,
    PublishRecord,
    PublishTask,
    PublishTaskAccount,
    TaskLog,
)
from server.app.services.assets import store_bytes
from server.app.services.toutiao_publisher import ToutiaoPublisher, ToutiaoPublishError
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
ACTIVE_RECORD_STATUSES = {"running"}
CAN_RETRY_TASK_STATUSES = {"failed", "partial_failed", "succeeded", "cancelled"}

# 任务级互斥锁，防止同一任务被并发执行
_task_locks: dict[int, threading.Lock] = {}
_task_locks_lock = threading.Lock()

# 任务硬取消标志
_task_cancel: dict[int, threading.Event] = {}
RECORD_TIMEOUT_SECONDS = 300


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


# 执行任务：依次处理每条待处理的发布记录
def execute_task(db: Session, task: PublishTask) -> PublishTask:
    # 获取或创建任务级互斥锁
    with _task_locks_lock:
        if task.id not in _task_locks:
            _task_locks[task.id] = threading.Lock()
    lock = _task_locks[task.id]
    if not lock.acquire(blocking=False):
        raise ValueError(f"Task {task.id} is already being executed")

    # 注册取消事件
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

        records = list_task_records(db, task.id)
        active_record = next((record for record in records if record.status in ACTIVE_RECORD_STATUSES), None)
        if active_record is not None:
            _add_log(db, task.id, active_record.id, "info", f"Task is waiting on record {active_record.id}")
            db.flush()
            return get_task(db, task.id) or task

        _run_next_pending_record(db, task, records)
        db.flush()
        return get_task(db, task.id) or task
    finally:
        lock.release()
        with _task_locks_lock:
            _task_locks.pop(task.id, None)
        _task_cancel.pop(task.id, None)


# 内部方法：循环执行待处理的发布记录
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

        article = db.get(Article, article_id)
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

        try:
            # Phase 2: Playwright execution (no DB transaction wrapping)
            publisher = build_publisher_for_record(next_record)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
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
        record.publish_url = publish_url
        _add_log(db, record.task_id, record.id, "info", "Record manually confirmed as succeeded")
    else:
        record.error_message = error_message or "Manually marked as failed"
        _add_log(db, record.task_id, record.id, "warn", "Record manually confirmed as failed")

    task = get_task(db, record.task_id)
    if task is not None and task.status not in TERMINAL_TASK_STATUSES:
        _run_next_pending_record(db, task)

    db.flush()
    return record


# 重试失败的发布记录
def retry_record(db: Session, record: PublishRecord) -> PublishRecord:
    if record.status != "failed":
        raise ValueError(f"Only failed records can be retried: {record.status}")

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
    return ToutiaoPublisher()


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
        if record.status in {"pending", "running"}:
            record.status = "cancelled"
            record.finished_at = now
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
    if any(r.status in {"pending", "running", "waiting_manual_publish"} for r in records):
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
