from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from server.app.models import Account, Article, ArticleBodyAsset, Asset
from server.app.services.articles import dumps_content_json
from server.app.services.assets import guess_image_size, normalize_ext
from server.app.services.toutiao_publisher import ToutiaoPublisher, ToutiaoPublishError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a standalone Toutiao publish smoke test without going through the task system."
    )
    parser.add_argument("--account-key", default="spike", help="Account browser profile key under browser_states/toutiao/.")
    parser.add_argument("--data-dir", default=None, help="Override GEO_DATA_DIR.")
    parser.add_argument("--title", default=None, help="Article title. Defaults to a timestamped smoke title.")
    parser.add_argument(
        "--body",
        default="这是一篇 Geo 发布链路 smoke 测试文章，用于验证标题、封面、正文和正文图片上传流程。",
        help="Plain body text inserted before the body image.",
    )
    parser.add_argument("--cover-image", default="scripts/test_cover.png", help="Cover image path.")
    parser.add_argument("--body-image", default=None, help="Body image path. Defaults to --cover-image.")
    parser.add_argument("--channel", default="chrome", help="Playwright browser channel, e.g. chrome/msedge/chromium.")
    parser.add_argument("--executable-path", default=None, help="Explicit browser executable path.")
    parser.add_argument(
        "--confirm-publish",
        action="store_true",
        help="Actually click the final confirm publish button. By default the script stops before final publish.",
    )
    return parser.parse_args()


def configure_data_dir(data_dir: str | None):
    if data_dir:
        os.environ["GEO_DATA_DIR"] = str(Path(data_dir).resolve())

    from server.app.core.config import get_settings

    get_settings.cache_clear()

    from server.app.core.paths import ensure_data_dirs, get_data_dir
    from server.app.services.accounts import relative_to_data_dir, state_path_for_key

    ensure_data_dirs()
    return get_data_dir, relative_to_data_dir, state_path_for_key


def create_spike_asset(source: Path, data_dir: Path, label: str) -> Asset:
    if not source.is_file():
        raise FileNotFoundError(f"{label} image not found: {source}")

    data = source.read_bytes()
    content_type = mimetypes.guess_type(source.name)[0] or "image/png"
    ext = normalize_ext(source.name, content_type, data[:32])
    asset_id = f"spike-{label}-{uuid.uuid4().hex[:12]}"
    storage_key = Path("spike_assets") / f"{asset_id}{ext}"
    dest = data_dir / storage_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    width, height = guess_image_size(data)

    return Asset(
        id=asset_id,
        filename=source.name,
        ext=ext,
        mime_type=content_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=storage_key.as_posix(),
        width=width,
        height=height,
    )


def build_article(title: str, body: str, cover_asset: Asset, body_asset: Asset) -> Article:
    content_json = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": body}],
            },
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "image",
                        "attrs": {
                            "assetId": body_asset.id,
                            "src": f"/api/assets/{body_asset.id}",
                        },
                    }
                ],
            },
        ],
    }
    article = Article(
        title=title,
        cover_asset_id=cover_asset.id,
        cover_asset=cover_asset,
        content_json=dumps_content_json(content_json),
        content_html=f"<p>{body}</p>",
        plain_text=body,
        word_count=len(body),
        status="ready",
    )
    article.body_assets.append(
        ArticleBodyAsset(
            asset_id=body_asset.id,
            asset=body_asset,
            position=0,
        )
    )
    return article


def main() -> int:
    args = parse_args()
    get_data_dir, relative_to_data_dir, state_path_for_key = configure_data_dir(args.data_dir)
    data_dir = get_data_dir()
    title = args.title or f"Geo smoke {time.strftime('%Y-%m-%d %H:%M:%S')}"
    cover_path = Path(args.cover_image).resolve()
    body_path = Path(args.body_image or args.cover_image).resolve()

    state_path = state_path_for_key(args.account_key)
    if not state_path.exists():
        print(f"Account storage state not found: {state_path}", file=sys.stderr)
        print("Run login/check flow for this account key first.", file=sys.stderr)
        return 2

    cover_asset = create_spike_asset(cover_path, data_dir, "cover")
    body_asset = create_spike_asset(body_path, data_dir, "body")
    article = build_article(title, args.body, cover_asset, body_asset)
    account = Account(
        display_name=args.account_key,
        status="valid",
        state_path=relative_to_data_dir(state_path),
    )
    publisher = ToutiaoPublisher(channel=args.channel, executable_path=args.executable_path)

    print(f"Data dir: {data_dir}")
    print(f"Account state: {state_path}")
    print(f"Title: {title}")
    print(f"Cover asset: {cover_asset.storage_key}")
    print(f"Body asset: {body_asset.storage_key}")
    print(f"Final confirm publish: {args.confirm_publish}")

    try:
        result = publisher.publish_article(
            article,
            account,
            stop_before_publish=not args.confirm_publish,
        )
    except ToutiaoPublishError as exc:
        print(f"Publish smoke failed: {exc}", file=sys.stderr)
        if exc.screenshot:
            shot_path = data_dir / "logs" / f"toutiao-publish-smoke-failure-{time.strftime('%Y%m%d-%H%M%S')}.png"
            shot_path.parent.mkdir(parents=True, exist_ok=True)
            shot_path.write_bytes(exc.screenshot)
            print(f"Failure screenshot: {shot_path}", file=sys.stderr)
        return 1

    print("Publish smoke finished:")
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    if not args.confirm_publish:
        print("Stopped before final confirm publish. Add --confirm-publish to actually publish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
