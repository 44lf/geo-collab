"""fts add plain_text to articles search index

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.engine.dialect.name

    if dialect_name == "mysql":
        op.execute("ALTER TABLE articles DROP INDEX ft_articles")
        op.execute(
            "ALTER TABLE articles ADD FULLTEXT INDEX ft_articles (title, author, plain_text) WITH PARSER ngram"
        )
    else:
        # Drop old triggers first
        op.execute("DROP TRIGGER IF EXISTS after_articles_insert")
        op.execute("DROP TRIGGER IF EXISTS after_articles_delete")
        op.execute("DROP TRIGGER IF EXISTS after_articles_update")

        # Drop old FTS table
        op.execute("DROP TABLE IF EXISTS articles_fts")

        # Recreate with plain_text included, try trigram tokenizer first
        try:
            op.execute(
                "CREATE VIRTUAL TABLE articles_fts USING fts5("
                "title, author, plain_text, content='articles', content_rowid='id', tokenize='trigram')"
            )
        except Exception:
            op.execute(
                "CREATE VIRTUAL TABLE articles_fts USING fts5("
                "title, author, plain_text, content='articles', content_rowid='id')"
            )

        # Populate from existing data
        op.execute(
            "INSERT INTO articles_fts(rowid, title, author, plain_text) "
            "SELECT id, title, author, plain_text FROM articles"
        )

        # Rebuild triggers with plain_text
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS after_articles_insert "
            "AFTER INSERT ON articles BEGIN "
            "INSERT INTO articles_fts(rowid, title, author, plain_text) "
            "VALUES (new.id, new.title, new.author, new.plain_text); "
            "END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS after_articles_delete "
            "AFTER DELETE ON articles BEGIN "
            "INSERT INTO articles_fts(articles_fts, rowid, title, author, plain_text) "
            "VALUES('delete', old.id, old.title, old.author, old.plain_text); "
            "END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS after_articles_update "
            "AFTER UPDATE ON articles BEGIN "
            "INSERT INTO articles_fts(articles_fts, rowid, title, author, plain_text) "
            "VALUES('delete', old.id, old.title, old.author, old.plain_text); "
            "INSERT INTO articles_fts(rowid, title, author, plain_text) "
            "VALUES (new.id, new.title, new.author, new.plain_text); "
            "END"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.engine.dialect.name

    if dialect_name == "mysql":
        op.execute("ALTER TABLE articles DROP INDEX ft_articles")
        op.execute(
            "ALTER TABLE articles ADD FULLTEXT INDEX ft_articles (title, author) WITH PARSER ngram"
        )
    else:
        # Drop new triggers
        op.execute("DROP TRIGGER IF EXISTS after_articles_insert")
        op.execute("DROP TRIGGER IF EXISTS after_articles_delete")
        op.execute("DROP TRIGGER IF EXISTS after_articles_update")

        # Drop new FTS table
        op.execute("DROP TABLE IF EXISTS articles_fts")

        # Recreate original without plain_text
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
