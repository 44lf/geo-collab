"""fts and composite indexes

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.engine.dialect.name

    if dialect_name == "mysql":
        op.execute(
            "ALTER TABLE articles ADD FULLTEXT INDEX ft_articles (title, author) WITH PARSER ngram"
        )
    else:
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
        op.execute(
            "INSERT INTO articles_fts(rowid, title, author) SELECT id, title, author FROM articles"
        )
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

    op.create_index("ix_accounts_platform_status", "accounts", ["platform_id", "status"])
    op.create_index("ix_publish_records_task_status", "publish_records", ["task_id", "status"])
    op.create_index("ix_publish_records_account_status", "publish_records", ["account_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.engine.dialect.name

    op.drop_index("ix_publish_records_account_status", table_name="publish_records")
    op.drop_index("ix_publish_records_task_status", table_name="publish_records")
    op.drop_index("ix_accounts_platform_status", table_name="accounts")

    if dialect_name == "mysql":
        op.execute("ALTER TABLE articles DROP INDEX ft_articles")
    else:
        op.execute("DROP TRIGGER IF EXISTS after_articles_insert")
        op.execute("DROP TRIGGER IF EXISTS after_articles_delete")
        op.execute("DROP TRIGGER IF EXISTS after_articles_update")
        op.execute("DROP TABLE IF EXISTS articles_fts")
