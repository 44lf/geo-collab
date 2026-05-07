from __future__ import annotations

import io
import json
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import zipfile

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session, selectinload

from server.app.core.config import get_settings
from server.app.core.paths import ensure_data_dirs, get_data_dir
from server.app.core.time import utcnow
from server.app.models import Account, Platform, PublishRecord, PublishTaskAccount, TaskLog
from server.app.schemas.account import AccountCheckRequest, AccountExportRequest, AccountRead, ToutiaoLoginRequest

TOUTIAO_HOME = "https://mp.toutiao.com"
LOGIN_HINTS = ("login", "passport", "sso", "验证码", "扫码", "登录")


# 浏览器检查结果
@dataclass(frozen=True)
class BrowserCheckResult:
    logged_in: bool
    url: str
    title: str


# 规范化账号本地标识：只保留字母数字和下划线
def normalize_account_key(account_key: str | None) -> str:
    raw = account_key or uuid.uuid4().hex
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-")
    return value or uuid.uuid4().hex


# 账号的浏览器状态目录（含 profile 和 storage_state.json）
def state_dir_for_key(account_key: str) -> Path:
    return get_data_dir() / "browser_states" / "toutiao" / account_key


# Playwright storage_state.json 的完整路径
def state_path_for_key(account_key: str) -> Path:
    return state_dir_for_key(account_key) / "storage_state.json"


# Playwright 持久化浏览器配置目录
def profile_dir_for_key(account_key: str) -> Path:
    return state_dir_for_key(account_key) / "profile"


# 将绝对路径转为相对 data_dir 的 POSIX 路径（用于数据库存储）
def relative_to_data_dir(path: Path) -> str:
    return path.resolve().relative_to(get_data_dir().resolve()).as_posix()


# 从 storage_state_path 中提取 account_key
def account_key_from_state_path(state_path: str) -> str:
    parts = Path(state_path).parts
    try:
        toutiao_index = parts.index("toutiao")
        return parts[toutiao_index + 1]
    except (ValueError, IndexError):
        raise ValueError("Invalid toutiao state path") from None


# 获取或创建头条号平台记录
def get_or_create_toutiao_platform(db: Session) -> Platform:
    platform = db.execute(select(Platform).where(Platform.code == "toutiao")).scalar_one_or_none()
    if platform is not None:
        return platform

    platform = Platform(code="toutiao", name="头条号", base_url="https://mp.toutiao.com", enabled=True)
    db.add(platform)
    db.commit()
    db.refresh(platform)
    return platform


# Playwright 浏览器启动参数
def launch_options(channel: str, executable_path: str | None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "headless": False,
        "viewport": {"width": 1440, "height": 900},
    }
    if channel:
        options["channel"] = channel
    if executable_path:
        options["executable_path"] = executable_path
    return options


# 根据页面文字判断是否已登录
def detect_login_state_text(url: str, title: str, body: str) -> bool:
    haystack = f"{url}\n{title}\n{body}"
    if any(hint in haystack for hint in LOGIN_HINTS):
        return False
    return "mp.toutiao.com" in url and ("profile_v4" in url or "头条号" in title)


# 使用 Playwright 检查账号登录状态（打开浏览器访问头条号）
def run_toutiao_browser_check(
    account_key: str,
    channel: str,
    executable_path: str | None,
    wait_seconds: int,
) -> BrowserCheckResult:
    from playwright.sync_api import sync_playwright

    ensure_data_dirs()
    state_dir_for_key(account_key).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir_for_key(account_key)),
            **launch_options(channel, executable_path),
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(TOUTIAO_HOME, wait_until="domcontentloaded", timeout=60000)
        # 等 JS 渲染完毕（登录弹窗由 JS 注入，domcontentloaded 时还未出现）
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # 轮询等待页面加载完毕或超时
        deadline = wait_seconds * 1000
        elapsed = 0
        logged_in = False
        url = page.url
        title = ""
        while elapsed <= deadline:
            page.wait_for_timeout(1000)
            elapsed += 1000
            try:
                url = page.url
                title = page.title()
                body = page.locator("body").inner_text(timeout=3000)
            except Exception:
                # 页面正在跳转，执行上下文暂时不可用，等下一轮
                continue
            logged_in = detect_login_state_text(url, title, body)
            if logged_in:
                break

        # 保存登录状态到 storage_state.json
        context.storage_state(path=str(state_path_for_key(account_key)))
        context.close()
        return BrowserCheckResult(logged_in=logged_in, url=url, title=title)


# 获取所有账号列表
def list_accounts(db: Session) -> list[Account]:
    stmt = select(Account).options(selectinload(Account.platform)).order_by(Account.updated_at.desc())
    return list(db.execute(stmt).scalars().all())


# 获取单个账号
def get_account(db: Session, account_id: int) -> Account | None:
    stmt = select(Account).where(Account.id == account_id).options(selectinload(Account.platform))
    return db.execute(stmt).scalar_one_or_none()


# 添加头条号账号
def login_toutiao(db: Session, payload: ToutiaoLoginRequest) -> Account:
    platform = get_or_create_toutiao_platform(db)
    account_key = normalize_account_key(payload.account_key)
    state_path = state_path_for_key(account_key)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    status = "unknown"
    if payload.use_browser:
        # 打开浏览器让用户交互登录
        result = run_toutiao_browser_check(
            account_key=account_key,
            channel=payload.channel,
            executable_path=payload.executable_path,
            wait_seconds=payload.wait_seconds,
        )
        status = "valid" if result.logged_in else "unknown"
    elif state_path.exists():
        # 复用已保存的 storage_state
        status = "valid"
    else:
        raise ValueError(f"Storage state not found: {state_path}")

    relative_state_path = relative_to_data_dir(state_path)
    account = db.execute(
        select(Account).where(Account.platform_id == platform.id, Account.state_path == relative_state_path)
    ).scalar_one_or_none()
    now = utcnow()
    if account is None:
        account = Account(
            platform=platform,
            display_name=payload.display_name,
            platform_user_id=None,
            status=status,
            state_path=relative_state_path,
            note=payload.note,
            last_login_at=now if status == "valid" else None,
            last_checked_at=now,
        )
        db.add(account)
    else:
        account.display_name = payload.display_name
        account.status = status
        account.note = payload.note
        account.last_checked_at = now
        if status == "valid":
            account.last_login_at = now
        account.updated_at = now

    db.commit()
    return get_account(db, account.id) or account


# 检查账号登录状态
def check_account(db: Session, account: Account, payload: AccountCheckRequest) -> Account:
    now = utcnow()
    account_key = account_key_from_state_path(account.state_path)
    abs_state_path = get_data_dir() / account.state_path
    if payload.use_browser:
        result = run_toutiao_browser_check(
            account_key=account_key,
            channel=payload.channel,
            executable_path=payload.executable_path,
            wait_seconds=payload.wait_seconds,
        )
        account.status = "valid" if result.logged_in else "expired"
    else:
        account.status = "valid" if abs_state_path.exists() else "expired"

    account.last_checked_at = now
    account.updated_at = now
    db.commit()
    return get_account(db, account.id) or account


# 重新登录账号（本质是重新调用 login_toutiao）
def relogin_account(db: Session, account: Account, payload: AccountCheckRequest) -> Account:
    account_key = account_key_from_state_path(account.state_path)
    request = ToutiaoLoginRequest(
        display_name=account.display_name,
        account_key=account_key,
        channel=payload.channel,
        executable_path=payload.executable_path,
        wait_seconds=payload.wait_seconds,
        use_browser=payload.use_browser,
        note=account.note,
    )
    return login_toutiao(db, request)


# 重命名账号显示名称
def rename_account(db: Session, account: Account, display_name: str) -> Account:
    account.display_name = display_name.strip()
    account.updated_at = utcnow()
    db.commit()
    return get_account(db, account.id) or account


# 删除账号（先清除关联记录，避免 NOT NULL FK 约束阻塞）
def delete_account(db: Session, account: Account) -> None:
    account_id = account.id
    db.execute(sa_delete(PublishTaskAccount).where(PublishTaskAccount.account_id == account_id))
    record_ids = list(
        db.execute(select(PublishRecord.id).where(PublishRecord.account_id == account_id)).scalars()
    )
    if record_ids:
        db.execute(sa_delete(TaskLog).where(TaskLog.record_id.in_(record_ids)))
        db.execute(sa_delete(PublishRecord).where(PublishRecord.id.in_(record_ids)))
    db.delete(account)
    db.commit()


# 导出账号授权包（ZIP 格式，含 storage_state.json）
def export_accounts_auth_package(db: Session, payload: AccountExportRequest) -> Path:
    ensure_data_dirs()
    accounts = _accounts_for_export(db, payload.account_ids)
    if not accounts:
        raise ValueError("No accounts to export")

    now = utcnow()
    export_path = _new_export_path(now)
    manifest = {
        "schema_version": 1,
        "app_version": get_settings().app_version,
        "exported_at": now.isoformat(),
        "excluded_scopes": ["articles", "assets", "publish_tasks", "task_logs", "database"],
        "accounts": [],
    }

    with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for account in accounts:
            account_dir = f"accounts/{account.platform.code}-{account.id}"
            account_payload = _account_export_payload(account)
            exported_files: list[str] = []
            archive.writestr(
                f"{account_dir}/account.json",
                json.dumps(account_payload, ensure_ascii=False, indent=2),
            )
            exported_files.append(f"{account_dir}/account.json")

            state_file = _resolve_data_file(account.state_path)
            state_archive_path = f"{account_dir}/storage_state.json"
            archive.write(state_file, state_archive_path)
            exported_files.append(state_archive_path)

            manifest["accounts"].append({**account_payload, "exported_files": exported_files})

        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return export_path


# 导入账号授权包（ZIP 格式），返回新增和跳过的账号名称列表
def import_accounts_auth_package(db: Session, zip_bytes: bytes) -> dict[str, list[str]]:
    ensure_data_dirs()
    imported: list[str] = []
    skipped: list[str] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except Exception as exc:
            raise ValueError("无效的授权包：无法读取 manifest.json") from exc

        for entry in manifest.get("accounts", []):
            state_path_rel: str = entry.get("state_path", "")
            display_name: str = entry.get("display_name", "未知账号")

            if not state_path_rel:
                skipped.append(f"{display_name}（缺少 state_path）")
                continue

            # 去重：state_path 已存在则跳过
            existing = db.execute(
                select(Account).where(Account.state_path == state_path_rel)
            ).scalar_one_or_none()
            if existing is not None:
                skipped.append(display_name)
                continue

            # 写入 storage_state.json
            account_dir_in_zip = f"accounts/{entry.get('platform_code', 'toutiao')}-{entry['id']}"
            archive_state_path = f"{account_dir_in_zip}/storage_state.json"
            if archive_state_path not in archive.namelist():
                skipped.append(f"{display_name}（ZIP 中缺少 storage_state.json）")
                continue

            try:
                account_key = account_key_from_state_path(state_path_rel)
            except ValueError:
                skipped.append(f"{display_name}（state_path 格式无效）")
                continue

            dest = state_path_for_key(account_key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read(archive_state_path))

            platform = get_or_create_toutiao_platform(db)
            now = utcnow()
            last_login_raw = entry.get("last_login_at")
            account = Account(
                platform=platform,
                display_name=display_name,
                platform_user_id=entry.get("platform_user_id"),
                status=entry.get("status", "unknown"),
                state_path=state_path_rel,
                note=entry.get("note"),
                last_login_at=datetime.fromisoformat(last_login_raw) if last_login_raw else None,
                last_checked_at=now,
            )
            db.add(account)
            imported.append(display_name)

    db.commit()
    return {"imported": imported, "skipped": skipped}


# 生成导出文件路径（优先数据目录，兜底系统临时目录）
def _new_export_path(now) -> Path:
    filename = f"geo-auth-export-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.zip"
    export_dir = get_data_dir() / "exports"
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        probe = export_dir / f".write-probe-{uuid.uuid4().hex}.tmp"
        with probe.open("xb"):
            pass
        probe.unlink(missing_ok=True)
        return export_dir / filename
    except OSError:
        fallback_dir = Path(tempfile.gettempdir()) / "geo-collab-exports"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / filename


# 获取要导出的账号列表
def _accounts_for_export(db: Session, account_ids: list[int] | None) -> list[Account]:
    stmt = select(Account).options(selectinload(Account.platform))
    if account_ids:
        unique_ids = sorted(set(account_ids))
        stmt = stmt.where(Account.id.in_(unique_ids))
    else:
        unique_ids = []
    accounts = list(db.execute(stmt.order_by(Account.id.asc())).scalars().all())
    if unique_ids:
        found_ids = {account.id for account in accounts}
        missing_ids = [account_id for account_id in unique_ids if account_id not in found_ids]
        if missing_ids:
            raise ValueError(f"Accounts not found: {', '.join(str(account_id) for account_id in missing_ids)}")
    return accounts


# 校验并解析 data_dir 下的相对路径
def _resolve_data_file(relative_path: str) -> Path:
    data_dir = get_data_dir().resolve()
    path = (data_dir / relative_path).resolve()
    if not path.is_relative_to(data_dir) or not path.is_file():
        raise ValueError(f"Account state file not found: {relative_path}")
    return path


# 构造账号导出 JSON 载荷
def _account_export_payload(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "platform_code": account.platform.code,
        "platform_name": account.platform.name,
        "display_name": account.display_name,
        "platform_user_id": account.platform_user_id,
        "status": account.status,
        "state_path": account.state_path,
        "last_checked_at": account.last_checked_at.isoformat() if account.last_checked_at else None,
        "last_login_at": account.last_login_at.isoformat() if account.last_login_at else None,
        "note": account.note,
        "created_at": account.created_at.isoformat(),
        "updated_at": account.updated_at.isoformat(),
    }


# 将 ORM Account 转为响应体
def to_account_read(account: Account) -> AccountRead:
    return AccountRead(
        id=account.id,
        platform_code=account.platform.code,
        platform_name=account.platform.name,
        display_name=account.display_name,
        platform_user_id=account.platform_user_id,
        status=account.status,
        last_checked_at=account.last_checked_at,
        last_login_at=account.last_login_at,
        state_path=account.state_path,
        note=account.note,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )
