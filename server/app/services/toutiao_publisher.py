from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.app.core.paths import get_data_dir
from server.app.models import Account, Article
from server.app.services.accounts import account_key_from_state_path, launch_options, profile_dir_for_key
from server.app.services.assets import resolve_asset_path

TOUTIAO_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
LOGIN_HINTS = ("login", "passport", "sso", "验证码", "扫码", "登录")
PUBLISH_HINTS = ("发布", "标题", "正文", "图文", "文章")
ACTIVE_PUBLISH_SESSIONS: list[tuple[Any, Any]] = []


@dataclass(frozen=True)
class PublishFillResult:
    url: str
    title: str
    message: str


class ToutiaoPublishError(Exception):
    def __init__(self, message: str, screenshot: bytes | None = None):
        super().__init__(message)
        self.screenshot = screenshot


class ToutiaoPublisher:
    def __init__(
        self,
        channel: str = "chrome",
        executable_path: str | None = None,
        wait_ms: int = 8000,
        close_after_fill: bool = False,
    ):
        self.channel = channel
        self.executable_path = executable_path
        self.wait_ms = wait_ms
        self.close_after_fill = close_after_fill

    def fill_article(self, article: Article, account: Account) -> PublishFillResult:
        from playwright.sync_api import sync_playwright

        account_key = account_key_from_state_path(account.state_path)
        state_path = (get_data_dir() / account.state_path).resolve()
        if not state_path.exists():
            raise ToutiaoPublishError(f"Account storage state not found: {account.state_path}")

        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir_for_key(account_key)),
            **launch_options(self.channel, self.executable_path),
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(TOUTIAO_PUBLISH_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(self.wait_ms)
            self._ensure_publish_page(page)
            self._fill_title(page, article.title)
            self._fill_body(page, article.plain_text or article.content_html)
            self._upload_cover(page, article)
            context.storage_state(path=str(state_path))
            result = PublishFillResult(
                url=page.url,
                title=page.title(),
                message="Article filled and waiting for manual publish",
            )
            if self.close_after_fill:
                context.close()
                playwright.stop()
            else:
                ACTIVE_PUBLISH_SESSIONS.append((playwright, context))
            return result
        except ToutiaoPublishError as exc:
            screenshot = exc.screenshot or self._screenshot(page)
            context.close()
            playwright.stop()
            raise ToutiaoPublishError(str(exc), screenshot) from exc
        except Exception as exc:
            screenshot = self._screenshot(page)
            context.close()
            playwright.stop()
            raise ToutiaoPublishError(str(exc), screenshot) from exc

    def _ensure_publish_page(self, page: Any) -> None:
        body = page.locator("body").inner_text(timeout=3000)
        haystack = f"{page.url}\n{page.title()}\n{body}"
        if any(hint in haystack for hint in LOGIN_HINTS):
            raise ToutiaoPublishError("Toutiao account appears logged out")
        if "mp.toutiao.com" not in page.url or not any(hint in haystack for hint in PUBLISH_HINTS):
            raise ToutiaoPublishError("Toutiao publish page not detected")

    def _fill_title(self, page: Any, title: str) -> None:
        selectors = [
            "textarea[placeholder*='标题']",
            "textarea",
            "input[placeholder*='标题']",
            "[contenteditable='true'][data-placeholder*='标题']",
        ]
        for selector in selectors:
            field = page.locator(selector).first
            try:
                if field.count() and field.is_visible():
                    field.click()
                    field.fill(title[:30])
                    return
            except Exception:
                continue
        raise ToutiaoPublishError("Toutiao title field not found")

    def _fill_body(self, page: Any, body: str) -> None:
        editable = page.locator("[contenteditable='true']")
        for index in range(editable.count()):
            field = editable.nth(index)
            try:
                box = field.bounding_box()
                if not field.is_visible() or not box or box["height"] < 80:
                    continue
                field.click()
                page.keyboard.type(body or " ")
                return
            except Exception:
                continue
        raise ToutiaoPublishError("Toutiao body editor not found")

    def _upload_cover(self, page: Any, article: Article) -> None:
        if article.cover_asset is None:
            return

        cover_path = resolve_asset_path(article.cover_asset)
        if not cover_path.exists():
            raise ToutiaoPublishError(f"Cover asset file not found: {article.cover_asset_id}")

        page.locator(".article-cover").first.click(timeout=5000)
        file_input = page.locator("input[type='file']").first
        if not file_input.count():
            raise ToutiaoPublishError("Toutiao cover file input not found")
        file_input.set_input_files(str(cover_path))
        page.wait_for_timeout(1500)

    def _screenshot(self, page: Any) -> bytes | None:
        try:
            return page.screenshot(full_page=True)
        except Exception:
            return None
