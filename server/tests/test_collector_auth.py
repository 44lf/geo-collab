from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from server.app.modules.collector.auth import (
    CollectorAuthenticationError,
    CollectorAuthorizationError,
    authenticate_collector,
    hash_collector_secret,
    parse_bearer_credential,
)
from server.app.modules.collector.models import CollectorCredential, CollectorNode

NOW = datetime(2026, 7, 29, 5, 0)
SECRET = "collector-secret-value"


def _credential(**changes) -> CollectorCredential:
    values = {
        "collector_id": "collector-local-1",
        "credential_id": "credential-1",
        "credential_hash": hash_collector_secret(SECRET),
        "hash_algorithm": "bcrypt",
        "status": "active",
    }
    values.update(changes)
    return CollectorCredential(**values)


def _node(**changes) -> CollectorNode:
    values = {
        "collector_id": "collector-local-1",
        "display_name": "Local collector",
        "destination": "geo-production",
        "status": "enabled",
        "enabled_sources": ["baidu", "ninegame", "yingyongbao", "taptap"],
    }
    values.update(changes)
    return CollectorNode(**values)


def test_valid_credential_authenticates_scoped_principal():
    db = MagicMock()
    credential = _credential()
    db.scalar.side_effect = [credential, _node()]

    principal = authenticate_collector(
        db,
        authorization=f"Bearer credential-1:{SECRET}",
        now=NOW,
    )

    assert principal.collector_id == "collector-local-1"
    assert principal.credential_id == "credential-1"
    assert principal.destination == "geo-production"
    assert principal.enabled_sources == ("baidu", "ninegame", "yingyongbao", "taptap")
    assert credential.last_used_at == NOW
    db.flush.assert_called_once()


@pytest.mark.parametrize(
    ("credential", "node"),
    [
        (None, None),
        (_credential(status="revoked"), _node()),
        (_credential(expires_at=NOW - timedelta(seconds=1)), _node()),
        (_credential(), _node(status="disabled")),
        (_credential(), _node(destination="")),
    ],
)
def test_invalid_revoked_expired_or_disabled_auth_is_indistinguishable(credential, node):
    db = MagicMock()
    db.scalar.side_effect = [credential, node]

    with pytest.raises(CollectorAuthenticationError) as exc:
        authenticate_collector(
            db,
            authorization=f"Bearer credential-1:{SECRET}",
            now=NOW,
        )

    assert str(exc.value) == "invalid collector credential"
    assert SECRET not in str(exc.value)
    db.flush.assert_not_called()


def test_wrong_secret_and_malformed_bearer_do_not_leak_secret():
    db = MagicMock()
    db.scalar.return_value = _credential()
    with pytest.raises(CollectorAuthenticationError) as exc:
        authenticate_collector(
            db,
            authorization="Bearer credential-1:wrong-secret",
            now=NOW,
        )
    assert str(exc.value) == "invalid collector credential"
    assert "wrong-secret" not in str(exc.value)

    for value in (None, "", "Basic abc", "Bearer no-separator", "Bearer bad id:value"):
        with pytest.raises(CollectorAuthenticationError, match="invalid collector credential"):
            parse_bearer_credential(value)


def test_principal_rejects_cross_node_and_cross_destination_scope():
    db = MagicMock()
    db.scalar.side_effect = [_credential(), _node()]
    principal = authenticate_collector(
        db,
        authorization=f"Bearer credential-1:{SECRET}",
        now=NOW,
    )

    principal.require_scope(
        collector_id="collector-local-1",
        destination="geo-production",
    )
    with pytest.raises(CollectorAuthorizationError, match="outside collector scope"):
        principal.require_scope(collector_id="other", destination="geo-production")
    with pytest.raises(CollectorAuthorizationError, match="outside collector scope"):
        principal.require_scope(collector_id="collector-local-1", destination="geo-dev")
