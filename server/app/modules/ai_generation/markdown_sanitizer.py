"""Markdown content normalization for generated articles."""

from __future__ import annotations

import re

# 行首 1-6 个 # 后「紧跟非空白且非 #」= 无空格 hashtag（本就不是合法 ATX 标题）。
# 合法标题 `## 标题`（# 后有空格）不匹配，不受影响。
_HASHTAG_HEADING_RE = re.compile(r"(?m)^(\s*)(#{1,6})(?=[^\s#])")


def escape_hashtag_headings(markdown: str) -> str:
    """转义「#标签」这类行首 hashtag，避免被 markdown 当成 ATX 标题（吞掉首 #、渲成 H1 超大字号）。

    小红书图文文案的 #话题标签用它保号（`#打工人` 不再变「打工人」）+ 降级为正文段落。
    仅动无空格 hashtag 行；`## 小标题`（# 后空格）等合法标题原样保留。
    """
    if not markdown or "#" not in markdown:
        return markdown
    return _HASHTAG_HEADING_RE.sub(r"\1\\\2", markdown)


def normalize_markdown_content(markdown: str) -> str:
    """Repair the known writer handoff artifact: JSON-escaped ASCII quotes.

    Some loop writers compose tool arguments as if the markdown body were JSON source,
    so natural straight quotes arrive as the literal two characters ``\"``.  Keep this
    deliberately narrow: do not run generic unicode/string unescaping here.
    """
    if not markdown or '\\"' not in markdown:
        return markdown
    return markdown.replace('\\"', '"')
