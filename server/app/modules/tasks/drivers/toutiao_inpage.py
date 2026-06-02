from __future__ import annotations

import json
import re

_EXTRA_BASE = {
    "content_source": 100000000402,
    "is_multi_title": 0,
    "sub_titles": [],
    "gd_ext": {
        "entrance": "",
        "from_page": "publisher_mp",
        "enter_from": "PC",
        "device_platform": "mp",
        "is_message": 0,
    },
    "tuwen_wtt_transfer_switch": "1",
}


def _word_count(content_html: str) -> int:
    return len(re.sub(r"<[^>]+>", "", content_html))


def build_publish_form(
    *,
    title: str,
    content_html: str,
    save: int = 0,
    pgc_id: str | None = None,
) -> dict[str, str]:
    """Build the application/x-www-form-urlencoded fields for the publish call.

    Constants mirror the real editor request captured 2026-06-02 (see design doc
    §6 "Spike 结论 · phase 2"). Milestone 1 sends save=0 (draft) with no cover.
    """
    extra = dict(_EXTRA_BASE)
    extra["content_word_cnt"] = _word_count(content_html)

    form: dict[str, str] = {
        "source": "29",
        "extra": json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
        "content": content_html,
        "title": title,
        "search_creation_info": json.dumps(
            {"searchTopOne": 0, "abstract": "", "clue_id": ""}, separators=(",", ":")
        ),
        "mp_editor_stat": "{}",
        "is_refute_rumor": "0",
        "save": str(save),
        "entrance": "main" if save == 1 else "",
        "draft_form_data": json.dumps({"coverType": 2}, separators=(",", ":")),
        "pgc_feed_covers": "[]",
        "article_ad_type": "3",
        "is_fans_article": "0",
        "govern_forward": "0",
        "praise": "0",
        "disable_praise": "0",
        "tree_plan_article": "0",
        "claim_exclusive": "0",
        "timer_status": "0",
    }
    if pgc_id:
        form["pgc_id"] = pgc_id
    return form
