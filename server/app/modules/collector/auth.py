"""Revocable, Collector-scoped bearer authentication for the Gateway."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import bcrypt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.db.session import get_db
from server.app.modules.collector.models import CollectorCredential, CollectorNode

_CREDENTIAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_AUTH_ERROR = "invalid collector credential"
_SCOPE_ERROR = "resource is outside collector scope"


class CollectorAuthenticationError(RuntimeError):
    """The caller did not present one active Collector identity."""


class CollectorAuthorizationError(RuntimeError):
    """The authenticated Collector attempted to cross its resource scope."""


@dataclass(frozen=True, slots=True)
class CollectorPrincipal:
    collector_id: str
    credential_id: str
    destination: str
    enabled_sources: tuple[str, ...]

    def require_scope(self, *, collector_id: str, destination: str) -> None:
        if collector_id != self.collector_id or destination != self.destination:
            raise CollectorAuthorizationError(_SCOPE_ERROR)


def hash_collector_secret(secret: str) -> str:
    """Hash a provisioned secret; plaintext is never stored server-side."""

    if not 16 <= len(secret) <= 256:
        raise ValueError("collector secret length must be between 16 and 256 characters")
    return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def parse_bearer_credential(authorization: str | None) -> tuple[str, str]:
    """Parse ``Bearer <credential_id>:<secret>`` without echoing invalid input."""

    try:
        scheme, token = (authorization or "").split(" ", 1)
        credential_id, secret = token.split(":", 1)
    except ValueError as exc:
        raise CollectorAuthenticationError(_AUTH_ERROR) from exc
    if (
        scheme.lower() != "bearer"
        or _CREDENTIAL_ID.fullmatch(credential_id) is None
        or not 16 <= len(secret) <= 256
    ):
        raise CollectorAuthenticationError(_AUTH_ERROR)
    return credential_id, secret


def _verify_secret(credential: CollectorCredential, secret: str) -> bool:
    if credential.hash_algorithm != "bcrypt":
        return False
    try:
        return bcrypt.checkpw(
            secret.encode("utf-8"),
            credential.credential_hash.encode("ascii"),
        )
    except (ValueError, UnicodeError):
        return False


def authenticate_collector(
    db: Session,
    *,
    authorization: str | None,
    now: datetime | None = None,
) -> CollectorPrincipal:
    """Authenticate one credential and return its immutable node scope."""

    credential_id, secret = parse_bearer_credential(authorization)
    credential = cast(
        CollectorCredential | None,
        db.scalar(
            select(CollectorCredential).where(CollectorCredential.credential_id == credential_id)
        ),
    )
    current_time = now or utcnow()
    if (
        credential is None
        or credential.status != "active"
        or (credential.expires_at is not None and credential.expires_at <= current_time)
        or not _verify_secret(credential, secret)
    ):
        raise CollectorAuthenticationError(_AUTH_ERROR)

    node = cast(
        CollectorNode | None,
        db.scalar(
            select(CollectorNode).where(CollectorNode.collector_id == credential.collector_id)
        ),
    )
    if (
        node is None
        or node.status != "enabled"
        or not node.destination
        or node.destination != node.destination.strip()
    ):
        raise CollectorAuthenticationError(_AUTH_ERROR)

    credential.last_used_at = current_time
    db.flush()
    return CollectorPrincipal(
        collector_id=node.collector_id,
        credential_id=credential.credential_id,
        destination=node.destination,
        enabled_sources=tuple(str(source) for source in node.enabled_sources),
    )


def require_collector_principal(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> CollectorPrincipal:
    try:
        return authenticate_collector(db, authorization=authorization)
    except CollectorAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_AUTH_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
