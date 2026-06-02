import json

from server.app.modules.tasks.drivers.toutiao_inpage import build_publish_form


def test_build_publish_form_minimal_draft():
    form = build_publish_form(title="今天是周二", content_html='<p data-track="1">正文</p>')
    assert form["title"] == "今天是周二"
    assert form["content"] == '<p data-track="1">正文</p>'
    assert form["save"] == "0"
    assert form["source"] == "29"
    assert form["pgc_feed_covers"] == "[]"
    # extra is valid JSON carrying the word count
    extra = json.loads(form["extra"])
    assert extra["content_word_cnt"] == len("正文")
    assert "pgc_id" not in form  # omitted on first draft


def test_build_publish_form_reuses_pgc_id():
    form = build_publish_form(title="t", content_html="<p>x</p>", pgc_id="7646670891934089737")
    assert form["pgc_id"] == "7646670891934089737"
