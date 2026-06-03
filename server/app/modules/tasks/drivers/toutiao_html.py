from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

from server.app.modules.articles.parser import BodySegment


class ToutiaoBodyError(Exception):
    """Raised when the article body cannot be serialized for Toutiao."""


@dataclass(frozen=True)
class ImageRef:
    """Ordered reference to a body image emitted as a placeholder token.

    The actual upload + token→`<img>` substitution happens later (in JS); the
    serializer only records what each placeholder stands for, in document order.
    """

    token: str
    image_path: Path | None
    image_asset_id: str | None = None
    stock_image_id: int | None = None


def _run_html(text: str, bold: bool) -> str:
    inner = escape(text, quote=False)
    return f"<strong>{inner}</strong>" if bold else inner


def body_segments_to_toutiao_html(
    segments: list[BodySegment],
) -> tuple[str, list[ImageRef]]:
    """Serialize parsed body segments into Toutiao `<p data-track="N">` HTML.

    Returns ``(html, image_order)`` where ``image_order`` lists, in document
    order, an :class:`ImageRef` per body image. Each image becomes its own
    placeholder paragraph ``<p data-track="N">__GEO_IMG_k__</p>`` (k = 0-based
    image index); the real upload + token substitution happens later in JS.

    Paragraph break = a text segment whose text is exactly "\\n".
    Headings render as a bold paragraph (no dedicated heading tag).
    ``data-track`` stays monotonic 1-based across ALL paragraphs, text and
    placeholder alike.
    """
    paragraphs: list[str] = []
    image_order: list[ImageRef] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            joined = "".join(current)
            if joined.strip():
                paragraphs.append(joined)
        current.clear()

    for seg in segments:
        if seg.kind == "image":
            flush()
            k = len(image_order)
            token = f"__GEO_IMG_{k}__"
            paragraphs.append(token)
            image_order.append(
                ImageRef(
                    token=token,
                    image_path=seg.image_path,
                    image_asset_id=seg.image_asset_id,
                    stock_image_id=seg.stock_image_id,
                )
            )
            continue
        if seg.text == "\n":
            flush()
            continue
        if not seg.text:
            continue
        bold = seg.bold or seg.heading_level is not None
        current.append(_run_html(seg.text, bold))
    flush()

    if not paragraphs:
        raise ToutiaoBodyError("正文为空")
    html = "".join(f'<p data-track="{i + 1}">{p}</p>' for i, p in enumerate(paragraphs))
    return html, image_order
