from server.app.modules.ai_generation.converter import markdown_to_tiptap

_STRUCTURAL_TYPES = {"heading", "bulletList", "orderedList", "listItem"}


def _types(doc):
    return [n["type"] for n in doc["content"]]


def _walk(node):
    """递归遍历 Tiptap 节点树，yield 每一个节点（含自身）。"""
    yield node
    for child in node.get("content") or []:
        yield from _walk(child)


def _assert_no_empty_structural_node(doc):
    for node in _walk(doc):
        if node.get("type") in _STRUCTURAL_TYPES:
            assert node.get("content"), (
                f"发现空结构节点 {node.get('type')!r}（content=[]），"
                "违反 Tiptap/ProseMirror schema，会导致文档渲染空白"
            )


def test_single_image_is_top_level():
    doc = markdown_to_tiptap("![cat](http://x/c.png)")
    assert doc["content"] == [
        {
            "type": "image",
            "attrs": {
                "src": "http://x/c.png",
                "alt": "cat",
                "title": "",
                "width": "30%",
                "assetId": None,
            },
        }
    ]


def test_image_between_paragraphs_keeps_order():
    doc = markdown_to_tiptap("before\n\n![a](u1)\n\nafter")
    assert _types(doc) == ["paragraph", "image", "paragraph"]


def test_inline_image_splits_paragraph_in_order():
    doc = markdown_to_tiptap("hello ![a](u) world")
    assert _types(doc) == ["paragraph", "image", "paragraph"]


def test_multiple_block_images_preserve_src_order():
    doc = markdown_to_tiptap("![a](u1)\n\n![b](u2)")
    assert _types(doc) == ["image", "image"]
    assert [n["attrs"]["src"] for n in doc["content"]] == ["u1", "u2"]


def test_no_stray_empty_paragraph_around_image():
    doc = markdown_to_tiptap("![only](u)")
    assert all(n["type"] != "paragraph" for n in doc["content"])


def test_image_in_tight_list_item_hoists_without_empty_listitem():
    doc = markdown_to_tiptap("- item one\n- ![a](u)\n")
    assert any(n["type"] == "image" for n in doc["content"])
    _assert_no_empty_structural_node(doc)


def test_image_in_heading_hoists_without_empty_heading():
    doc = markdown_to_tiptap("# ![a](u)\n")
    assert any(n["type"] == "image" for n in doc["content"])
    _assert_no_empty_structural_node(doc)


def test_normal_bullet_list_unaffected():
    doc = markdown_to_tiptap("- a\n- b\n")
    bullet_lists = [n for n in doc["content"] if n["type"] == "bulletList"]
    assert len(bullet_lists) == 1
    items = bullet_lists[0]["content"]
    assert len(items) == 2
    for item in items:
        assert item["type"] == "listItem"
        assert item.get("content")
    _assert_no_empty_structural_node(doc)


def test_normal_heading_unaffected():
    doc = markdown_to_tiptap("# Title\n")
    headings = [n for n in doc["content"] if n["type"] == "heading"]
    assert len(headings) == 1
    heading = headings[0]

    def _text(node):
        if node.get("type") == "text":
            return node.get("text", "")
        return "".join(_text(c) for c in node.get("content") or [])

    assert _text(heading) == "Title"
