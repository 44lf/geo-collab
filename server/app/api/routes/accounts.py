from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.models import Account as AccountModel, User
from server.app.schemas.account import AccountCheckRequest, AccountExportRequest, AccountRead, AccountRenameRequest, ToutiaoLoginRequest
from server.app.services.accounts import (
    check_account,
    delete_account,
    export_accounts_auth_package,
    get_account,
    import_accounts_auth_package,
    list_accounts,
    login_toutiao,
    rename_account,
    relogin_account,
)
from server.app.services.serializers import to_account_read

router = APIRouter()


def _verify_account_ownership(account: AccountModel | None, current_user: User) -> AccountModel:
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if current_user.role != "admin" and account.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


# 获取所有账号列表
@router.get("", response_model=list[AccountRead])
def read_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountRead]:
    accounts = list_accounts(db)
    if current_user.role != "admin":
        accounts = [a for a in accounts if a.user_id == current_user.id]
    return [to_account_read(account) for account in accounts]


# 添加头条号账号（可选择打开浏览器交互登录或复用已保存状态）
@router.post("/toutiao/login", response_model=AccountRead)
def login_toutiao_account(
    payload: ToutiaoLoginRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRead:
    return to_account_read(login_toutiao(db, current_user.id, payload))


# 导出账号授权包（含 Playwright storage_state 的 ZIP）
@router.post("/export")
def export_accounts(
    payload: AccountExportRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    try:
        export_path = export_accounts_auth_package(db, payload or AccountExportRequest())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        export_path,
        media_type="application/zip",
        filename=export_path.name,
    )


# 导入账号授权包
@router.post("/import")
async def import_accounts(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    import io
    import re
    import zipfile

    from server.app.core.config import MAX_ZIP_BYTES

    zip_bytes = await file.read()
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"ZIP file exceeds {MAX_ZIP_BYTES // (1024 * 1024)}MB limit",
        )

    ZIP_ENTRY_RE = re.compile(r"^(?:manifest\.json|accounts/[a-zA-Z0-9_]+-\d+/(?:account|storage_state)\.json)$")
    MAX_ENTRIES = 50
    MAX_ENTRY_BYTES = 2 * 1024 * 1024

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            entries = archive.namelist()
            if len(entries) > MAX_ENTRIES:
                raise HTTPException(status_code=400, detail=f"ZIP contains {len(entries)} entries, max {MAX_ENTRIES} allowed")
            for entry_name in entries:
                info = archive.getinfo(entry_name)
                if not ZIP_ENTRY_RE.match(entry_name):
                    raise HTTPException(status_code=400, detail=f"Invalid ZIP entry path: {entry_name}")
                if info.file_size > MAX_ENTRY_BYTES:
                    raise HTTPException(status_code=400, detail=f"ZIP entry too large: {entry_name} ({info.file_size} bytes)")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file")

    try:
        return import_accounts_auth_package(db, current_user.id, zip_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# 校验指定账号的登录状态
@router.post("/{account_id}/check", response_model=AccountRead)
def check_existing_account(
    account_id: int,
    payload: AccountCheckRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRead:
    account = _verify_account_ownership(get_account(db, account_id), current_user)
    return to_account_read(check_account(db, account, payload or AccountCheckRequest()))


# 重新登录指定账号（重新打开浏览器）
@router.post("/{account_id}/relogin", response_model=AccountRead)
def relogin_existing_account(
    account_id: int,
    payload: AccountCheckRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRead:
    account = _verify_account_ownership(get_account(db, account_id), current_user)
    return to_account_read(relogin_account(db, account, payload or AccountCheckRequest()))


# 重命名账号显示名称
@router.patch("/{account_id}", response_model=AccountRead)
def rename_existing_account(
    account_id: int,
    payload: AccountRenameRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRead:
    account = _verify_account_ownership(get_account(db, account_id), current_user)
    return to_account_read(rename_account(db, account, payload.display_name))


# 删除指定账号
@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    account = _verify_account_ownership(get_account(db, account_id), current_user)
    delete_account(db, account)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
