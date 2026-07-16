"""Markdown → HTML / Tiptap JSON 转换工具。

Tiptap 支持的节点类型（本模块覆盖范围）：
  doc, paragraph, heading(level 1-6), bulletList, orderedList, listItem, image
  文本标记：bold, italic, code

image 是块级节点，遇到 heading/list 内嵌图片时会被提升到 doc 顶层（近似保序，
不保证与周围文本严格同序），提升后若原 heading/listItem/bulletList/orderedList
因此变空，会被整体丢弃，避免产出违反 ProseMirror 默认 schema 的空结构节点。
"""

from html.parser import HTMLParser
from typing import Any


def markdown_to_html(md: str) -> str:
    import markdown

    return markdown.markdown(md, extensions=["extra"])


class _TiptapBuilder(HTMLParser):
    """将 HTML 流式解析为 Tiptap JSON 节点树。"""

    def __init__(self) -> None:
        super().__init__()
        self._stack: list[dict[str, Any]] = []
        self._root: list[dict[str, Any]] = []
        self._marks: list[dict[str, Any]] = []

    # ── 内部辅助 ──────────────────────────────────────────────────────────

    def _current(self) -> dict[str, Any] | None:
        return self._stack[-1] if self._stack else None

    def _commit(self, node: dict[str, Any]) -> None:
        """把节点挂到父节点或根列表。"""
        if self._stack:
            self._stack[-1].setdefault("content", []).append(node)
        else:
            self._root.append(node)

    # ── HTMLParser 回调函数 ───────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._stack.append({"type": "heading", "attrs": {"level": int(tag[1])}, "content": []})
        elif tag == "p":
            self._stack.append({"type": "paragraph", "content": []})
        elif tag == "ul":
            self._stack.append({"type": "bulletList", "content": []})
        elif tag == "ol":
            self._stack.append({"type": "orderedList", "content": []})
        elif tag == "li":
            self._stack.append({"type": "listItem", "content": []})
        elif tag == "strong":
            self._marks.append({"type": "bold"})
        elif tag == "em":
            self._marks.append({"type": "italic"})
        elif tag in ("code", "tt"):
            self._marks.append({"type": "code"})
        elif tag == "img":
            a = dict(attrs)
            img_node = {
                "type": "image",
                "attrs": {
                    "src": a.get("src") or "",
                    "alt": a.get("alt") or "",
                    "title": "",
                    "width": "30%",  # 编辑器 CustomImage 默认显示宽度
                    "assetId": None,  # 内链已在 src 里，非站内 Asset
                },
            }
            # image 是块级节点，不能嵌在 paragraph/list 里。若正处于段落中：
            # 先把已累积的段落文本收尾（保序），再把 image 落到顶层，最后重开一个
            # 空段落承接图片后面的行内文本。python-markdown 把 ![](url) 包成 <p><img/></p>，
            # 独占一行时该段落为空、会在 </p> 处被丢弃。
            reopen = False
            if self._stack and self._stack[-1].get("type") == "paragraph":
                para = self._stack.pop()
                if para.get("content"):
                    self._commit(para)
                reopen = True
            self._root.append(img_node)
            if reopen:
                self._stack.append({"type": "paragraph", "content": []})

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            # 只弹 paragraph；空段落（如仅承接过被提升的图片）直接丢弃，不产出空节点。
            if self._stack and self._stack[-1].get("type") == "paragraph":
                node = self._stack.pop()
                if node.get("content"):
                    self._commit(node)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"):
            if self._stack:
                node = self._stack.pop()
                # 空结构节点（如 heading/list 内的图片被提升到顶层后，原节点没剩其它
                # 内容）直接丢弃，不产出空节点——ProseMirror 默认 schema 要求
                # heading/bulletList/orderedList/listItem 的 content 非空，产出空节点
                # 会导致 Tiptap 文档整体解析失败、正文渲染空白。
                if node.get("content"):
                    self._commit(node)
        elif tag == "li":
            if not self._stack:
                return
            item = self._stack.pop()
            # Markdown 紧凑列表 <li>text</li> 不含 <p>，需手动包一层 paragraph
            has_block = any(
                c.get("type") in ("paragraph", "bulletList", "orderedList")
                for c in item.get("content", [])
            )
            if not has_block and item.get("content"):
                item["content"] = [{"type": "paragraph", "content": item["content"]}]
            # listItem 内的图片被提升到顶层后可能留下空壳（content=[]）：同样丢弃，
            # 使其所属 bulletList/orderedList 少一个 item 而不是携带一个非法空 item；
            # 若该 list 的所有 item 都被丢弃，list 自身也会在上面的 ul/ol 分支被丢弃。
            if item.get("content"):
                self._commit(item)
        elif tag == "strong":
            self._marks = [m for m in self._marks if m["type"] != "bold"]
        elif tag == "em":
            self._marks = [m for m in self._marks if m["type"] != "italic"]
        elif tag in ("code", "tt"):
            self._marks = [m for m in self._marks if m["type"] != "code"]

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        node: dict[str, Any] = {"type": "text", "text": text}
        if self._marks:
            node["marks"] = list(self._marks)
        cur = self._current()
        if cur is not None:
            cur.setdefault("content", []).append(node)

    # ── 结果 ──────────────────────────────────────────────────────────────

    def result(self) -> dict[str, Any]:
        return {"type": "doc", "content": self._root}


def markdown_to_tiptap(md: str) -> dict[str, Any]:
    """Markdown → Tiptap doc JSON：先转 HTML，再流式解析成节点树（article_writer 落库前调用，与 markdown_to_html 一起生成三份并行正文之一）。"""
    html = markdown_to_html(md)
    builder = _TiptapBuilder()
    builder.feed(html)
    return builder.result()
