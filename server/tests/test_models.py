from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.db.base import Base
from server.app.models import (
    Account,
    Article,
    ArticleBodyAsset,
    ArticleGroup,
    ArticleGroupItem,
    Asset,
    Platform,
    PublishRecord,
    PublishTask,
    PublishTaskAccount,
    TaskLog,
)


def test_core_model_tables_are_declared():
    expected_tables = {
        "platforms",
        "accounts",
        "assets",
        "articles",
        "article_body_assets",
        "article_groups",
        "article_group_items",
        "publish_tasks",
        "publish_task_accounts",
        "publish_records",
        "task_logs",
    }

    assert expected_tables.issubset(Base.metadata.tables.keys())


def test_core_model_relationships_round_trip_in_sqlite_memory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        platform = Platform(code="toutiao", name="头条号", base_url="https://mp.toutiao.com")
        cover = Asset(
            id="asset-cover",
            filename="cover.png",
            ext=".png",
            mime_type="image/png",
            size=100,
            sha256="a" * 64,
            storage_key="assets/2026/05/cover.png",
        )
        body_image = Asset(
            id="asset-body",
            filename="body.png",
            ext=".png",
            mime_type="image/png",
            size=120,
            sha256="b" * 64,
            storage_key="assets/2026/05/body.png",
        )
        article = Article(
            title="测试文章",
            author="Geo",
            cover_asset=cover,
            content_json="{}",
            content_html="<p>正文</p>",
            plain_text="正文",
            word_count=2,
            status="ready",
            body_assets=[ArticleBodyAsset(asset=body_image, position=0, editor_node_id="node-1")],
        )
        group = ArticleGroup(name="测试分组", items=[ArticleGroupItem(article=article, sort_order=1)])
        account = Account(
            platform=platform,
            display_name="测试账号",
            platform_user_id="toutiao-user",
            status="valid",
            state_path="browser_states/toutiao/1/storage_state.json",
            last_login_at=utcnow(),
        )
        task = PublishTask(
            name="测试任务",
            task_type="single",
            status="pending",
            platform=platform,
            article=article,
            group=None,
            accounts=[PublishTaskAccount(account=account, sort_order=0)],
        )
        record = PublishRecord(task=task, article=article, platform=platform, account=account, status="pending")
        task.records.append(record)
        task.logs.append(TaskLog(record=record, level="info", message="created"))

        session.add(group)
        session.add(task)
        session.commit()

        stored_task = session.query(PublishTask).one()
        assert stored_task.platform.code == "toutiao"
        assert stored_task.accounts[0].account.display_name == "测试账号"
        assert stored_task.records[0].article.body_assets[0].position == 0
        assert stored_task.logs[0].message == "created"


def test_database_constraints_exist_in_metadata():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    account_checks = {constraint["name"] for constraint in inspector.get_check_constraints("accounts")}
    article_checks = {constraint["name"] for constraint in inspector.get_check_constraints("articles")}
    task_checks = {constraint["name"] for constraint in inspector.get_check_constraints("publish_tasks")}
    record_checks = {constraint["name"] for constraint in inspector.get_check_constraints("publish_records")}

    assert "ck_accounts_status" in account_checks
    assert "ck_articles_status" in article_checks
    assert "ck_publish_tasks_task_type" in task_checks
    assert "ck_publish_tasks_status" in task_checks
    assert "ck_publish_records_status" in record_checks
