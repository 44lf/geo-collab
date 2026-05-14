"""Tests for server.app.services.publish_runner."""
from __future__ import annotations

import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from server.app.services.drivers.toutiao import (
    PublishFillResult,
    ToutiaoUserInputRequired,
)


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_stub_article(tmp_path: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        title="Test Article",
        cover_asset=object(),  # non-None → passes the cover check
    )


def _make_stub_account() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        state_path="browser_states/testplat/k1/storage_state.json"
    )


def _make_stub_session() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id="sess1",
        display=":99",
        novnc_url="http://localhost:6080",
    )


def _make_stub_pw_context_page():
    """Return (pw, context, page) stubs."""
    page = types.SimpleNamespace()
    context = types.SimpleNamespace(
        set_default_navigation_timeout=lambda ms: None,
        new_page=lambda: page,
        close=lambda: None,
    )
    chromium = types.SimpleNamespace(
        launch_persistent_context=lambda user_data_dir, **kw: context
    )
    pw = types.SimpleNamespace(
        chromium=chromium,
        stop=lambda: None,
    )
    # sync_playwright() returns a context manager, so we simulate .start()
    pw_cm = types.SimpleNamespace(start=lambda: pw)
    return pw_cm, context, page


# ---------------------------------------------------------------------------
# Shared monkeypatching helper
# ---------------------------------------------------------------------------

def _patch_common(monkeypatch, tmp_path: Path, stub_session, pw_cm, context, page):
    """Apply all patches common to both tests."""
    # Create the state file so the existence check passes
    state_rel = "browser_states/testplat/k1/storage_state.json"
    state_file = tmp_path / state_rel
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{}")

    # get_data_dir → tmp_path
    monkeypatch.setattr(
        "server.app.services.publish_runner.get_data_dir",
        lambda: tmp_path,
    )

    # account_key_from_state_path → ("testplat", "k1")
    monkeypatch.setattr(
        "server.app.services.publish_runner.account_key_from_state_path",
        lambda state_path: ("testplat", "k1"),
    )

    # profile_dir_for_key → a path that doesn't need to exist
    monkeypatch.setattr(
        "server.app.services.publish_runner.profile_dir_for_key",
        lambda platform_code, account_key: tmp_path / "profile",
    )

    # managed_remote_browser_session → yields stub_session
    @contextmanager
    def _fake_managed(account_key):
        yield stub_session

    monkeypatch.setattr(
        "server.app.services.publish_runner.managed_remote_browser_session",
        _fake_managed,
    )

    # sync_playwright → pw_cm
    monkeypatch.setattr(
        "server.app.services.publish_runner.sync_playwright",
        lambda: pw_cm,
    )

    # launch_options → minimal dict so options["env"] assignment works
    monkeypatch.setattr(
        "server.app.services.publish_runner.launch_options",
        lambda channel, executable_path: {},
    )

    # attach_browser_handles → no-op
    monkeypatch.setattr(
        "server.app.services.publish_runner.attach_browser_handles",
        lambda *args, **kwargs: None,
    )


# ---------------------------------------------------------------------------
# Test 1
# ---------------------------------------------------------------------------

def test_run_publish_routes_by_platform_code(monkeypatch, tmp_path):
    """run_publish calls the driver matched by the platform code in state_path."""
    from server.app.services import publish_runner

    stub_session = _make_stub_session()
    pw_cm, context, page = _make_stub_pw_context_page()

    _patch_common(monkeypatch, tmp_path, stub_session, pw_cm, context, page)

    # Build a stub driver that records calls
    publish_called = []
    expected_result = PublishFillResult(
        url="https://example.com/article/1",
        title="Test Article",
        message="发布成功",
    )

    class _StubDriver:
        code = "testplat"
        name = "Test Platform"
        home_url = "https://example.com"
        publish_url = "https://example.com/publish"

        def detect_logged_in(self, *, url, title, body):
            return True

        def publish(self, *, page, context, article, account, state_path, stop_before_publish):
            publish_called.append(True)
            return expected_result

    stub_driver = _StubDriver()

    # Patch get_driver to return our stub
    monkeypatch.setattr(
        "server.app.services.publish_runner.get_driver",
        lambda platform_code: stub_driver,
    )

    article = _make_stub_article(tmp_path)
    account = _make_stub_account()

    result = publish_runner.run_publish(article=article, account=account)

    assert publish_called, "driver.publish was not called"
    assert result == expected_result


# ---------------------------------------------------------------------------
# Test 2
# ---------------------------------------------------------------------------

def test_run_publish_keeps_session_on_user_input_required(monkeypatch, tmp_path):
    """When driver.publish raises ToutiaoUserInputRequired, session is kept alive and exception has session_id/novnc_url."""
    from server.app.services import publish_runner

    stub_session = _make_stub_session()
    pw_cm, context, page = _make_stub_pw_context_page()

    _patch_common(monkeypatch, tmp_path, stub_session, pw_cm, context, page)

    # Driver raises ToutiaoUserInputRequired
    class _StubDriver:
        code = "testplat"
        name = "Test Platform"
        home_url = "https://example.com"
        publish_url = "https://example.com/publish"

        def detect_logged_in(self, *, url, title, body):
            return True

        def publish(self, *, page, context, article, account, state_path, stop_before_publish):
            raise ToutiaoUserInputRequired("needs login")

    stub_driver = _StubDriver()

    monkeypatch.setattr(
        "server.app.services.publish_runner.get_driver",
        lambda platform_code: stub_driver,
    )

    # Track keep_session_alive calls
    kept_alive = []

    monkeypatch.setattr(
        "server.app.services.publish_runner.keep_session_alive",
        lambda session_id: kept_alive.append(session_id),
    )

    article = _make_stub_article(tmp_path)
    account = _make_stub_account()

    with pytest.raises(ToutiaoUserInputRequired) as exc_info:
        publish_runner.run_publish(article=article, account=account)

    exc = exc_info.value
    assert kept_alive == [stub_session.id], (
        f"keep_session_alive was not called with '{stub_session.id}'; calls: {kept_alive}"
    )
    assert exc.session_id == stub_session.id, f"Expected session_id={stub_session.id!r}, got {exc.session_id!r}"
    assert exc.novnc_url == stub_session.novnc_url, f"Expected novnc_url={stub_session.novnc_url!r}, got {exc.novnc_url!r}"
