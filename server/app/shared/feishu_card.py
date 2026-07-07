"""飞书审核卡片（交互卡 2.0）构造 + 发送。

与 webhook-only 的 feishu.py 分开：这里走自建应用 bot（im/v1/messages），
用于「一篇一卡」的待审通知。build_* 为纯函数可单测；send_* 吞异常返回 None。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# lark_md 需转义的字符
_LARK_MD_SPECIAL = ("\\", "*", "_", "`", "~", "[", "]")


def _esc(text: str, limit: int = 120) -> str:
    s = text or ""
    for ch in _LARK_MD_SPECIAL:
        s = s.replace(ch, "\\" + ch)
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def build_review_card(
    *,
    article_id: int,
    title: str,
    question: str,
    score: int | None,
    decision: str | None,
    review_url: str,
) -> dict:
    """构造待审交互卡（卡片 2.0）。纯函数、无 I/O。本期不含「审核通过」按钮。"""
    score_line = "自评：—"
    if decision is not None or score is not None:
        score_line = f"自评：{decision or '—'}" + (f" · {score} 分" if score is not None else "")
    lines = [f"**{_esc(title, 80)}**", f"选题：{_esc(question, 80)}", score_line]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"📝 文章待审 #{article_id}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看文章"},
                        "type": "primary",
                        "url": review_url,
                    }
                ],
            },
        ],
    }
