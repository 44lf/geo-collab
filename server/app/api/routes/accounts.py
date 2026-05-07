from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.schemas.account import AccountCheckRequest, AccountExportRequest, AccountRead, ToutiaoLoginRequest
from server.app.services.accounts import (
    check_account,
    delete_account,
    export_accounts_auth_package,
    get_account,
    list_accounts,
    login_toutiao,
    relogin_account,
    to_account_read,
)

router = APIRouter()


@router.get("", response_model=list[AccountRead])
def read_accounts(db: Session = Depends(get_db)) -> list[AccountRead]:
    return [to_account_read(account) for account in list_accounts(db)]


@router.post("/toutiao/login", response_model=AccountRead)
def login_toutiao_account(payload: ToutiaoLoginRequest, db: Session = Depends(get_db)) -> AccountRead:
    try:
        account = login_
        toutiao(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_account_read(account)


@router.post("/export")
def export_accounts(payload: AccountExportRequest | None = None, db: Session = Depends(get_db)) -> FileResponse:
    try:
        export_path = export_accounts_auth_package(db, payload or AccountExportRequest())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(export_path, media_type="application/zip", filename=export_path.name)


@router.post("/{account_id}/check", response_model=AccountRead)
def check_existing_account(
    account_id: int,
    payload: AccountCheckRequest | None = None,
    db: Session = Depends(get_db),
) -> AccountRead:
    account = get_account(db, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        checked = check_account(db, account, payload or AccountCheckRequest())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_account_read(checked)


@router.post("/{account_id}/relogin", response_model=AccountRead)
def relogin_existing_account(
    account_id: int,
    payload: AccountCheckRequest | None = None,
    db: Session = Depends(get_db),
) -> AccountRead:
    account = get_account(db, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        relogged = relogin_account(db, account, payload or AccountCheckRequest())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_account_read(relogged)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_account(account_id: int, db: Session = Depends(get_db)) -> Response:
    account = get_account(db, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    delete_account(db, account)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
