from server.app.modules.ai_generation.converter import markdown_to_tiptap


def _types(doc):
    return [n["type"] for n in doc["content"]]


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
