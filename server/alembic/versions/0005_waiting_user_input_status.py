"""add waiting_user_input publish record status

Revision ID: 0005_waiting_user_input
Revises: 0004_idempotency_versions
Create Date: 2026-05-11 16:05:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0005_waiting_user_input"
down_revision: Union[str, None] = "0004_idempotency_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_STATUS_CHECK = (
    "status in ('pending', 'running', 'waiting_manual_publish', "
    "'waiting_user_input', 'succeeded', 'failed', 'cancelled')"
)
OLD_STATUS_CHECK = "status in ('pending', 'running', 'waiting_manual_publish', 'succeeded', 'failed', 'cancelled')"


def upgrade() -> None:
    with op.batch_alter_table("publish_records", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_publish_records_status", type_="check")
        batch_op.create_check_constraint("ck_publish_records_status", NEW_STATUS_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("publish_records", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_publish_records_status", type_="check")
        batch_op.create_check_constraint("ck_publish_records_status", OLD_STATUS_CHECK)
