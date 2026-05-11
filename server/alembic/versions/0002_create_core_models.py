"""create core models

Revision ID: 0002_create_core_models
Revises: 0001_create_platforms
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_create_core_models"
down_revision: Union[str, None] = "0001_create_platforms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("ext", sa.String(length=30), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=1000), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_assets_storage_key"),
    )
    op.create_index(op.f("ix_assets_mime_type"), "assets", ["mime_type"], unique=False)
    op.create_index(op.f("ix_assets_sha256"), "assets", ["sha256"], unique=False)

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("platform_user_id", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("state_path", sa.String(length=1000), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status in ('valid', 'expired', 'unknown')", name="ck_accounts_status"),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_id", "platform_user_id", name="uq_accounts_platform_user"),
    )
    op.create_index(op.f("ix_accounts_platform_id"), "accounts", ["platform_id"], unique=False)
    op.create_index(op.f("ix_accounts_status"), "accounts", ["status"], unique=False)

    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("cover_asset_id", sa.String(length=64), nullable=True),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("plain_text", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status in ('draft', 'ready', 'archived')", name="ck_articles_status"),
        sa.ForeignKeyConstraint(["cover_asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_articles_status"), "articles", ["status"], unique=False)
    op.create_index(op.f("ix_articles_title"), "articles", ["title"], unique=False)

    op.create_table(
        "article_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_article_groups_name"), "article_groups", ["name"], unique=True)

    op.create_table(
        "publish_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("platform_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("stop_before_publish", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("task_type in ('single', 'group_round_robin')", name="ck_publish_tasks_task_type"),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'partial_failed', 'failed', 'cancelled')",
            name="ck_publish_tasks_status",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["group_id"], ["article_groups.id"]),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_publish_tasks_platform_id"), "publish_tasks", ["platform_id"], unique=False)
    op.create_index(op.f("ix_publish_tasks_status"), "publish_tasks", ["status"], unique=False)
    op.create_index(op.f("ix_publish_tasks_task_type"), "publish_tasks", ["task_type"], unique=False)

    op.create_table(
        "article_body_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("editor_node_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_article_body_assets_article_id"), "article_body_assets", ["article_id"], unique=False)
    op.create_index(op.f("ix_article_body_assets_asset_id"), "article_body_assets", ["asset_id"], unique=False)

    op.create_table(
        "article_group_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["group_id"], ["article_groups.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "article_id", name="uq_article_group_items_group_article"),
    )
    op.create_index(op.f("ix_article_group_items_article_id"), "article_group_items", ["article_id"], unique=False)
    op.create_index(op.f("ix_article_group_items_group_id"), "article_group_items", ["group_id"], unique=False)

    op.create_table(
        "publish_task_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["publish_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "account_id", name="uq_publish_task_accounts_task_account"),
    )
    op.create_index(op.f("ix_publish_task_accounts_account_id"), "publish_task_accounts", ["account_id"], unique=False)
    op.create_index(op.f("ix_publish_task_accounts_task_id"), "publish_task_accounts", ["task_id"], unique=False)

    op.create_table(
        "publish_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("platform_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("publish_url", sa.String(length=1000), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_of_record_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'waiting_manual_publish', 'waiting_user_input', 'succeeded', 'failed', 'cancelled')",
            name="ck_publish_records_status",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.ForeignKeyConstraint(["retry_of_record_id"], ["publish_records.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["publish_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_publish_records_account_id"), "publish_records", ["account_id"], unique=False)
    op.create_index(op.f("ix_publish_records_article_id"), "publish_records", ["article_id"], unique=False)
    op.create_index(op.f("ix_publish_records_platform_id"), "publish_records", ["platform_id"], unique=False)
    op.create_index(op.f("ix_publish_records_status"), "publish_records", ["status"], unique=False)
    op.create_index(op.f("ix_publish_records_task_id"), "publish_records", ["task_id"], unique=False)

    op.create_table(
        "task_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=True),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("screenshot_asset_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("level in ('info', 'warn', 'error')", name="ck_task_logs_level"),
        sa.ForeignKeyConstraint(["record_id"], ["publish_records.id"]),
        sa.ForeignKeyConstraint(["screenshot_asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["publish_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_logs_level"), "task_logs", ["level"], unique=False)
    op.create_index(op.f("ix_task_logs_task_id"), "task_logs", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_task_logs_task_id"), table_name="task_logs")
    op.drop_index(op.f("ix_task_logs_level"), table_name="task_logs")
    op.drop_table("task_logs")
    op.drop_index(op.f("ix_publish_records_task_id"), table_name="publish_records")
    op.drop_index(op.f("ix_publish_records_status"), table_name="publish_records")
    op.drop_index(op.f("ix_publish_records_platform_id"), table_name="publish_records")
    op.drop_index(op.f("ix_publish_records_article_id"), table_name="publish_records")
    op.drop_index(op.f("ix_publish_records_account_id"), table_name="publish_records")
    op.drop_table("publish_records")
    op.drop_index(op.f("ix_publish_task_accounts_task_id"), table_name="publish_task_accounts")
    op.drop_index(op.f("ix_publish_task_accounts_account_id"), table_name="publish_task_accounts")
    op.drop_table("publish_task_accounts")
    op.drop_index(op.f("ix_article_group_items_group_id"), table_name="article_group_items")
    op.drop_index(op.f("ix_article_group_items_article_id"), table_name="article_group_items")
    op.drop_table("article_group_items")
    op.drop_index(op.f("ix_article_body_assets_asset_id"), table_name="article_body_assets")
    op.drop_index(op.f("ix_article_body_assets_article_id"), table_name="article_body_assets")
    op.drop_table("article_body_assets")
    op.drop_index(op.f("ix_publish_tasks_task_type"), table_name="publish_tasks")
    op.drop_index(op.f("ix_publish_tasks_status"), table_name="publish_tasks")
    op.drop_index(op.f("ix_publish_tasks_platform_id"), table_name="publish_tasks")
    op.drop_table("publish_tasks")
    op.drop_index(op.f("ix_article_groups_name"), table_name="article_groups")
    op.drop_table("article_groups")
    op.drop_index(op.f("ix_articles_title"), table_name="articles")
    op.drop_index(op.f("ix_articles_status"), table_name="articles")
    op.drop_table("articles")
    op.drop_index(op.f("ix_accounts_status"), table_name="accounts")
    op.drop_index(op.f("ix_accounts_platform_id"), table_name="accounts")
    op.drop_table("accounts")
    op.drop_index(op.f("ix_assets_sha256"), table_name="assets")
    op.drop_index(op.f("ix_assets_mime_type"), table_name="assets")
    op.drop_table("assets")
