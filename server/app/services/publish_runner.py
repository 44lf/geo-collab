from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from server.app.core.paths import get_data_dir
from server.app.models import Account, Article
from server.app.services.accounts import account_key_from_state_path, launch_options, profile_dir_for_key
from server.app.services.browser_sessions import (
    attach_browser_handles,
    keep_session_alive,
    managed_remote_browser_session,
)
from server.app.services.drivers import get_driver
from server.app.services.drivers.toutiao import (
    PublishFillResult,
    ToutiaoPublishError,
    ToutiaoUserInputRequired,
)
from server.app.services.publish_diagnostics import publish_step, record_publish_diagnostic


def _short_url(url: str, limit: int = 180) -> str:
    return url if len(url) <= limit else f"{url[:limit]}..."


def _attach_page_network_diagnostics(page: Any) -> None:
    counters = {"failed": 0, "bad_response": 0}

    def on_request_failed(request: Any) -> None:
        if counters["failed"] >= 20:
            return
        counters["failed"] += 1
        try:
            failure = getattr(request, "failure", None)
            if callable(failure):
                failure = failure()
            error_text = failure or "unknown"
            record_publish_diagnostic(
                f"network request failed: {request.method} {_short_url(request.url)}; error={error_text}",
                level="warn",
            )
        except Exception:
            record_publish_diagnostic("network request failed: unable to read request details", level="warn")

    def on_response(response: Any) -> None:
        try:
            status = int(response.status)
        except Exception:
            return
        if status < 400 or counters["bad_response"] >= 20:
            return
        counters["bad_response"] += 1
        try:
            record_publish_diagnostic(
                f"network response status={status}: {response.request.method} {_short_url(response.url)}",
                level="warn",
            )
        except Exception:
            record_publish_diagnostic(f"network response status={status}: unable to read response details", level="warn")

    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)


def run_publish(
    *,
    article: Article,
    account: Account,
    channel: str = "chromium",
    executable_path: str | None = None,
    stop_before_publish: bool = False,
) -> PublishFillResult:
    """Generic publish entry point. Looks up driver by account platform, starts remote session, runs driver.publish."""
    if not article.title or not article.title.strip():
        raise ToutiaoPublishError("标题不能为空")
    if article.cover_asset is None:
        raise ToutiaoPublishError("封面图片是必填项")

    platform_code, account_key = account_key_from_state_path(account.state_path)
    state_path = (get_data_dir() / account.state_path).resolve()
    if not state_path.exists():
        raise ToutiaoPublishError(f"Account storage state not found: {account.state_path}")

    driver = get_driver(platform_code)

    with publish_step("remote browser session"):
        session_cm = managed_remote_browser_session(account_key)
        session = session_cm.__enter__()
    try:
        pw = None
        context = None
        _keep_browser = False
        try:
            with publish_step("start Playwright"):
                pw = sync_playwright().start()
            with publish_step("launch Chromium"):
                options = launch_options(channel, executable_path)
                options["env"] = {**os.environ, "DISPLAY": session.display}
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir_for_key(platform_code, account_key)),
                    **options,
                )
            context.set_default_navigation_timeout(30000)
            page = context.new_page()
            _attach_page_network_diagnostics(page)
            attach_browser_handles(session.id, pw, context, page)
            with publish_step("driver publish flow", page=page):
                return driver.publish(
                    page=page,
                    context=context,
                    article=article,
                    account=account,
                    state_path=state_path,
                    stop_before_publish=stop_before_publish,
                )
        except ToutiaoUserInputRequired as exc:
            _keep_browser = True
            keep_session_alive(session.id)
            exc.session_id = session.id
            exc.novnc_url = session.novnc_url
            raise
        finally:
            if not _keep_browser:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                if pw is not None:
                    try:
                        pw.stop()
                    except Exception:
                        pass
                attach_browser_handles(session.id, None, None, None)
    finally:
        session_cm.__exit__(None, None, None)
