"""add fts5 and composite indexes

Revision ID: 0003_fts5_indexes
Revises: 6b14e9d054c6
Create Date: 2026-05-08 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_fts5_indexes"
down_revision: Union[str, None] = "6b14e9d054c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Composite indexes
    op.create_index("ix_task_logs_task_id_id", "task_logs", ["task_id", "id"])
    op.create_index("ix_publish_records_task_status_id", "publish_records", ["task_id", "status", "id"])
    op.create_index("ix_publish_records_retry_of", "publish_records", ["retry_of_record_id"])
    op.create_index("ix_accounts_state_path", "accounts", ["state_path"])

    # FTS5 virtual table (external content — reads text from articles table)
    # Uses trigram tokenizer for CJK/Chinese full-text search support
    try:
        op.execute(
            "CREATE VIRTUAL TABLE articles_fts USING fts5("
            "title, author, content='articles', content_rowid='id', tokenize='trigram')"
        )
    except Exception:
        op.execute(
            "CREATE VIRTUAL TABLE articles_fts USING fts5("
            "title, author, content='articles', content_rowid='id')"
        )
    op.execute("INSERT INTO articles_fts(rowid, title, author) SELECT id, title, author FROM articles")

    # Keep FTS index in sync with articles DML
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS after_articles_insert "
        "AFTER INSERT ON articles BEGIN "
        "INSERT INTO articles_fts(rowid, title, author) VALUES (new.id, new.title, new.author); "
        "END"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS after_articles_delete "
        "AFTER DELETE ON articles BEGIN "
        "INSERT INTO articles_fts(articles_fts, rowid, title, author) "
        "VALUES('delete', old.id, old.title, old.author); "
        "END"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS after_articles_update "
        "AFTER UPDATE ON articles BEGIN "
        "INSERT INTO articles_fts(articles_fts, rowid, title, author) "
        "VALUES('delete', old.id, old.title, old.author); "
        "INSERT INTO articles_fts(rowid, title, author) VALUES (new.id, new.title, new.author); "
        "END"
    )


def downgrade() -> None:
    op.drop_index("ix_task_logs_task_id_id", table_name="task_logs")
    op.drop_index("ix_publish_records_task_status_id", table_name="publish_records")
    op.drop_index("ix_publish_records_retry_of", table_name="publish_records")
    op.drop_index("ix_accounts_state_path", table_name="accounts")
    op.execute("DROP TRIGGER IF EXISTS after_articles_insert")
    op.execute("DROP TRIGGER IF EXISTS after_articles_delete")
    op.execute("DROP TRIGGER IF EXISTS after_articles_update")
    op.execute("DROP TABLE IF EXISTS articles_fts")
