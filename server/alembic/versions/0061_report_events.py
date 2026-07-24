"""report_events：跨模块通用打点上报事件表。

记录报错异常、事件触发，用于回溯业务流程（AI 生文、pipeline 执行、任务发布、账号
登录等任意模块均可写入，不局限于某一条业务线）。所有登录用户可查询（非 admin-only
的审计日志），只提供查询接口，写入只走后端内部 service.record_event()。

Revision ID: 0061_report_events
Revises: 0060_auto_review_pass_line
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061_report_events"
down_revision: str | None = "0060_auto_review_pass_line"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_module", sa.String(50), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
    )
    op.create_index(op.f("ix_report_events_created_at"), "report_events", ["created_at"])
    op.create_index(op.f("ix_report_events_source_module"), "report_events", ["source_module"])
    op.create_index(op.f("ix_report_events_source_id"), "report_events", ["source_id"])
    op.create_index(op.f("ix_report_events_event_type"), "report_events", ["event_type"])
    op.create_index(
        "ix_report_events_source_created",
        "report_events",
        ["source_module", "created_at"],
    )
    op.create_index(
        "ix_report_events_event_type_created",
        "report_events",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_report_events_source_type_id",
        "report_events",
        ["source_type", "source_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_events_source_type_id", table_name="report_events")
    op.drop_index("ix_report_events_event_type_created", table_name="report_events")
    op.drop_index("ix_report_events_source_created", table_name="report_events")
    op.drop_index(op.f("ix_report_events_event_type"), table_name="report_events")
    op.drop_index(op.f("ix_report_events_source_id"), table_name="report_events")
    op.drop_index(op.f("ix_report_events_source_module"), table_name="report_events")
    op.drop_index(op.f("ix_report_events_created_at"), table_name="report_events")
    op.drop_table("report_events")
