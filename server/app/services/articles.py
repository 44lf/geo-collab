from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session, selectinload

from server.app.core.time import utcnow
from server.app.models import Article, ArticleBodyAsset, ArticleGroupItem, Asset, PublishRecord, TaskLog
from server.app.schemas.article import ArticleBodyAssetRead, ArticleCreate, ArticleRead, ArticleUpdate

VALID_ARTICLE_STATUSES = {"draft", "ready", "archived"}


# 正文图片节点信息
@dataclass(frozen=True)
class ImageNode:
    asset_id: str
    editor_node_id: str | None = None


# 递归遍历 Tiptap JSON 树的所有节点
def _iter_nodes(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        content = node.get("content")
        if isinstance(content, list):
            for child in content:
                yield from _iter_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from _iter_nodes(child)


# 从 Tiptap 图片节点中提取 asset_id（支持多种字段名）
def _asset_id_from_image_node(node: dict[str, Any]) -> str | None:
    attrs = node.get("attrs")
    if not isinstance(attrs, dict):
        return None

    for key in ("assetId", "asset_id", "dataAssetId"):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return value

    src = attrs.get("src")
    if isinstance(src, str) and "/api/assets/" in src:
        return src.rstrip("/").split("/api/assets/")[-1].split("?")[0]

    return None


# 从 Tiptap JSON 中提取所有图片节点
def extract_body_image_nodes(content_json: dict[str, Any]) -> list[ImageNode]:
    images: list[ImageNode] = []
    for node in _iter_nodes(content_json):
        if node.get("type") != "image":
            continue
        asset_id = _asset_id_from_image_node(node)
        if not asset_id:
            continue
        attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
        editor_node_id = attrs.get("id") or attrs.get("nodeId")
        images.append(ImageNode(asset_id=asset_id, editor_node_id=editor_node_id))
    return images


# 序列化 content_json 为紧凑 JSON 字符串
def dumps_content_json(content_json: dict[str, Any]) -> str:
    return json.dumps(content_json, ensure_ascii=False, separators=(",", ":"))


# 反序列化 content_json 字符串为字典
def loads_content_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


# 校验文章状态值是否合法
def validate_article_status(status: str) -> None:
    if status not in VALID_ARTICLE_STATUSES:
        raise ValueError(f"Invalid article status: {status}")


# 确认资源文件在数据库中存在
def ensure_asset_exists(db: Session, asset_id: str | None) -> None:
    if asset_id is None:
        return
    if db.get(Asset, asset_id) is None:
        raise ValueError(f"Asset not found: {asset_id}")


# 同步文章正文中的图片关联信息（全量替换）
def sync_article_body_assets(db: Session, article: Article, content_json: dict[str, Any]) -> None:
    image_nodes = extract_body_image_nodes(content_json)
    for image_node in image_nodes:
        ensure_asset_exists(db, image_node.asset_id)

    article.body_assets.clear()
    for position, image_node in enumerate(image_nodes):
        article.body_assets.append(
            ArticleBodyAsset(
                asset_id=image_node.asset_id,
                position=position,
                editor_node_id=image_node.editor_node_id,
            )
        )


# 获取单篇文章（含正文图片关联）
def get_article(db: Session, article_id: int) -> Article | None:
    stmt = (
        select(Article)
        .where(Article.id == article_id)
        .options(selectinload(Article.body_assets).selectinload(ArticleBodyAsset.asset))
    )
    return db.execute(stmt).scalar_one_or_none()


# 列出文章，支持按标题/作者模糊搜索
def list_articles(db: Session, query: str | None = None) -> list[Article]:
    stmt = select(Article).options(selectinload(Article.body_assets)).order_by(Article.updated_at.desc())
    if query:
        like = f"%{query}%"
        stmt = stmt.where((Article.title.like(like)) | (Article.author.like(like)))
    return list(db.execute(stmt).scalars().all())


# 创建文章
def create_article(db: Session, payload: ArticleCreate) -> Article:
    validate_article_status(payload.status)
    ensure_asset_exists(db, payload.cover_asset_id)
    article = Article(
        title=payload.title,
        author=payload.author,
        cover_asset_id=payload.cover_asset_id,
        content_json=dumps_content_json(payload.content_json),
        content_html=payload.content_html,
        plain_text=payload.plain_text,
        word_count=payload.word_count,
        status=payload.status,
    )
    sync_article_body_assets(db, article, payload.content_json)
    db.add(article)
    db.commit()
    return get_article(db, article.id) or article


# 更新文章（部分更新，只修改提供的字段）
def update_article(db: Session, article: Article, payload: ArticleUpdate) -> Article:
    update_data = payload.model_dump(exclude_unset=True)
    if "status" in update_data and update_data["status"] is not None:
        validate_article_status(update_data["status"])
    if "cover_asset_id" in update_data:
        ensure_asset_exists(db, update_data["cover_asset_id"])

    content_json = loads_content_json(article.content_json)
    if "content_json" in update_data and update_data["content_json"] is not None:
        content_json = update_data["content_json"]

    for field in ("title", "author", "cover_asset_id", "content_html", "plain_text", "word_count", "status"):
        if field in update_data:
            setattr(article, field, update_data[field])

    if "content_json" in update_data:
        article.content_json = dumps_content_json(content_json)
        sync_article_body_assets(db, article, content_json)

    article.updated_at = utcnow()
    db.commit()
    return get_article(db, article.id) or article


# 仅更新文章封面图
def set_article_cover(db: Session, article: Article, cover_asset_id: str | None) -> Article:
    ensure_asset_exists(db, cover_asset_id)
    article.cover_asset_id = cover_asset_id
    article.updated_at = utcnow()
    db.commit()
    return get_article(db, article.id) or article


# 删除文章（先清除分组关联和发布记录，避免 NOT NULL FK 约束阻塞）
def delete_article(db: Session, article: Article) -> None:
    article_id = article.id
    db.execute(sa_delete(ArticleGroupItem).where(ArticleGroupItem.article_id == article_id))
    record_ids = list(
        db.execute(select(PublishRecord.id).where(PublishRecord.article_id == article_id)).scalars()
    )
    if record_ids:
        db.execute(sa_delete(TaskLog).where(TaskLog.record_id.in_(record_ids)))
        db.execute(sa_delete(PublishRecord).where(PublishRecord.id.in_(record_ids)))
    db.delete(article)
    db.commit()


# 将 ORM Article 转为响应体
def to_article_read(article: Article) -> ArticleRead:
    body_assets = sorted(article.body_assets, key=lambda item: item.position)
    return ArticleRead(
        id=article.id,
        title=article.title,
        author=article.author,
        cover_asset_id=article.cover_asset_id,
        content_json=loads_content_json(article.content_json),
        content_html=article.content_html,
        plain_text=article.plain_text,
        word_count=article.word_count,
        status=article.status,
        body_assets=[
            ArticleBodyAssetRead(
                asset_id=item.asset_id,
                position=item.position,
                editor_node_id=item.editor_node_id,
            )
            for item in body_assets
        ],
        created_at=article.created_at,
        updated_at=article.updated_at,
    )

