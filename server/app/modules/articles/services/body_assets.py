"""正文素材同步与素材存在性校验。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from server.app.modules.articles.models import Article, ArticleBodyAsset, Asset
from server.app.modules.articles.parser import extract_body_image_nodes
from server.app.shared.errors import ClientError


def ensure_asset_exists(db: Session, asset_id: str | None) -> None:
    if asset_id is None:
        return
    if db.get(Asset, asset_id) is None:
        raise ClientError(f"Asset not found: {asset_id}")


def sync_article_body_assets(db: Session, article: Article, content_json: dict) -> None:
    """按正文 JSON 里的图片节点重建 body_assets 关联（先全清再按文档顺序重建，position 即顺序）。"""
    image_nodes = extract_body_image_nodes(content_json)
    for asset_id, _ in image_nodes:
        ensure_asset_exists(db, asset_id)

    article.body_assets.clear()
    for position, (asset_id, editor_node_id) in enumerate(image_nodes):
        article.body_assets.append(
            ArticleBodyAsset(
                asset_id=asset_id,
                position=position,
                editor_node_id=editor_node_id,
            )
        )
