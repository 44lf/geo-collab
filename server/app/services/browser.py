from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from playwright.sync_api import BrowserContext, Playwright, sync_playwright

from server.app.services.accounts import launch_options, profile_dir_for_key


@contextmanager
def managed_browser_context(
    account_key: str,
    channel: str = "chrome",
    executable_path: str | None = None,
    close_on_exit: bool = True,
) -> Iterator[tuple[Playwright, BrowserContext, Any]]:
    """启动 Playwright 持久化浏览器上下文，退出时自动清理资源。"""
    playwright: Playwright | None = None
    context: BrowserContext | None = None
    try:
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir_for_key(account_key)),
            **launch_options(channel, executable_path),
        )
        context.set_default_navigation_timeout(30000)
        from server.app.services.toutiao_publisher import _register_context_for_cleanup
        _register_context_for_cleanup(context)
        yield playwright, context, context.new_page()
    finally:
        if close_on_exit:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
                from server.app.services.toutiao_publisher import _launched_contexts
                if context in _launched_contexts:
                    _launched_contexts.remove(context)
            if playwright:
                try:
                    playwright.stop()
                except Exception:
                    pass
