"""验证 random_fill_missed 从 illustrate_one 透传到 _maybe_insert_images；默认路径不开。"""

import inspect

from server.app.modules.articles import ai_format, ai_illustrate_svc


def test_all_wrappers_accept_random_fill_missed():
    for fn in (
        ai_format._maybe_insert_images,
        ai_format._ai_format_write_back,
        ai_format._web_fallback_collect_and_write_back,
        ai_format.run_ai_format,
        ai_format._run_ai_format_web_fallback,
        ai_format.run_ai_format_from_game_list,
    ):
        params = inspect.signature(fn).parameters
        assert "random_fill_missed" in params, fn.__name__
        assert params["random_fill_missed"].default is False, fn.__name__


def test_illustrate_one_enables_random_fill_and_no_apply_image_fallback():
    src = inspect.getsource(ai_illustrate_svc.illustrate_one)
    assert "random_fill_missed=True" in src
    assert "apply_image_fallback" not in src


def test_run_ai_format_forwards_random_fill_missed(monkeypatch):
    captured = {}

    def fake_write_back(article_id, **kw):
        captured.update(kw)
        return 0

    monkeypatch.setattr(ai_format, "_ai_format_write_back", fake_write_back)

    class _Prep:
        content_json = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x"}]}],
        }
        valid_indices = {0}
        system_prompt = "p"
        available_categories = []
        model = "m"
        api_key = "k"
        base_url = None
        timeout_seconds = 1
        image_search_query = None

    monkeypatch.setattr(ai_format, "_ai_format_prepare", lambda *a, **k: _Prep())
    monkeypatch.setattr(
        ai_format,
        "_call_litellm_completion",
        lambda **k: type(
            "R",
            (),
            {
                "choices": [
                    type(
                        "C",
                        (),
                        {"message": type("M", (), {"content": '{"heading_indices": []}'})()},
                    )()
                ]
            },
        )(),
    )
    ai_format.run_ai_format(1, include_images=True, random_fill_missed=True)
    assert captured.get("random_fill_missed") is True
