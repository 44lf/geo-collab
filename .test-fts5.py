"""Test trigram FTS5 with CJK more thoroughly."""
import sqlalchemy as sa

engine = sa.create_engine("sqlite://", connect_args={"check_same_thread": False})
with engine.connect() as conn:
    conn.execute(sa.text("""
        CREATE VIRTUAL TABLE t_fts USING fts5(
            title, author, tokenize='trigram'
        )
    """))
    conn.execute(sa.text("INSERT INTO t_fts(title, author) VALUES ('可删除文章', 'Geo')"))
    conn.execute(sa.text("INSERT INTO t_fts(title, author) VALUES ('hello world', 'Test')"))

    # Test various query types
    queries = ["删除", "可删除", "可删除文", "文章", "可", "hello", "world", "hello world"]
    for q in queries:
        try:
            result = conn.execute(
                sa.text("SELECT rowid FROM t_fts WHERE t_fts MATCH :q"),
                {"q": q},
            ).all()
            print(f"MATCH {q!r}: rowids={[r[0] for r in result]}")
        except Exception as e:
            print(f"MATCH {q!r}: ERROR {e}")
