from __future__ import annotations

import os
from pathlib import Path

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

    with managed_remote_browser_session(account_key) as session:
        pw = None
        context = None
        _keep_browser = False
        try:
            pw = sync_playwright().start()
            options = launch_options(channel, executable_path)
            options["env"] = {**os.environ, "DISPLAY": session.display}
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir_for_key(platform_code, account_key)),
                **options,
            )
            context.set_default_navigation_timeout(30000)
            page = context.new_page()
            attach_browser_handles(session.id, pw, context, page)
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
