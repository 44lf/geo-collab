from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

from server.app.core.paths import get_data_dir
from server.app.models import Account, Article
from server.app.services.accounts import account_key_from_state_path, launch_options, profile_dir_for_key
from server.app.services.assets import resolve_asset_path

# 头条号发布页面 URL
TOUTIAO_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
LOGIN_HINTS = ("login", "passport", "sso", "验证码", "扫码", "登录")
PUBLISH_HINTS = ("发布", "标题", "正文", "图文", "文章")


# 发布填充结果
@dataclass(frozen=True)
class PublishFillResult:
    url: str
    title: str
    message: str


# 头条号发布异常，可附带失败截图
class ToutiaoPublishError(Exception):
    def __init__(self, message: str, screenshot: bytes | None = None):
        super().__init__(message)
        self.screenshot = screenshot


# 头条号 Playwright 自动发布器
class ToutiaoPublisher:
    def __init__(
        self,
        channel: str = "chrome",
        executable_path: str | None = None,
        wait_ms: int = 8000,
    ):
        self.channel = channel
        self.executable_path = executable_path
        self.wait_ms = wait_ms

    def publish_article(self, article: Article, account: Account, stop_before_publish: bool = False) -> PublishFillResult:
        """填充文章表单并点击发布，完成后关闭浏览器。"""
        from playwright.sync_api import sync_playwright

        account_key = account_key_from_state_path(account.state_path)
        state_path = (get_data_dir() / account.state_path).resolve()
        if not state_path.exists():
            raise ToutiaoPublishError(f"Account storage state not found: {account.state_path}")

        # 启动 Playwright 浏览器
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir_for_key(account_key)),
            **launch_options(self.channel, self.executable_path),
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            # 导航到发布页面，依次执行各步骤
            page.goto(TOUTIAO_PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
            # 等标题输入框出现（替代固定等待，通常 2–4s 即可就绪）
            try:
                page.get_by_role("textbox", name="请输入文章标题").wait_for(state="visible", timeout=self.wait_ms)
            except Exception:
                pass
            self._ensure_publish_page(page)
            self._close_ai_drawer(page)
            self._fill_title(page, article.title)
            self._fill_body(page, article.plain_text or article.content_html)
            self._handle_cover(page, article)
            page.wait_for_timeout(1000)
            publish_url = self._click_publish_and_wait(page, stop_before_publish)
            # 更新 storage_state 以保持登录状态
            context.storage_state(path=str(state_path))
            context.close()
            playwright.stop()
            return PublishFillResult(
                url=publish_url,
                title=article.title,
                message=f"发布成功: {publish_url}",
            )
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

    def _close_ai_drawer(self, page: Any) -> None:
        """关闭头条号 AI 创作助手抽屉，避免遮挡正文编辑区。"""
        try:
            btn = page.locator(".close-btn").first
            if btn.count() and btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(500)
        except Exception:
            logger.warning("Failed to close AI drawer", exc_info=True)

    def _fill_title(self, page: Any, title: str) -> None:
        """填充文章标题（最多 30 字）。"""
        field = page.get_by_role("textbox", name="请输入文章标题")
        try:
            field.wait_for(state="visible", timeout=5000)
            field.click()
            field.fill(title[:30])
            return
        except Exception:
            logger.warning("Title field not found via textbox role, raising error", exc_info=True)
        raise ToutiaoPublishError("Toutiao title field not found")

    def _fill_body(self, page: Any, body: str) -> None:
        """填充文章正文（先剥离 HTML 标签再输入）。"""
        clean_body = re.sub(r'<[^>]+>', '', body) if body else " "
        try:
            para = page.get_by_role("paragraph").first
            para.scroll_into_view_if_needed()
            para.click()
            page.evaluate("(text) => document.execCommand('insertText', false, text)", clean_body)
            return
        except Exception:
            logger.warning("Body fill via paragraph role failed, trying contenteditable fallback", exc_info=True)
        # 兜底：找最大的 contenteditable div 输入
        editable = page.locator("[contenteditable='true']")
        for i in range(editable.count()):
            field = editable.nth(i)
            try:
                box = field.bounding_box()
                if not box or box["height"] < 80:
                    continue
                field.scroll_into_view_if_needed()
                field.click()
                page.evaluate("(text) => document.execCommand('insertText', false, text)", clean_body)
                return
            except Exception:
                logger.warning("Failed to fill body via contenteditable fallback", exc_info=True)
                continue
        raise ToutiaoPublishError("Toutiao body editor not found")

    def _handle_cover(self, page: Any, article: Article) -> None:
        """上传封面图片。封面图是必填项。"""
        if article.cover_asset is None:
            raise ToutiaoPublishError("封面图片是必填项，article 没有关联 cover_asset")

        cover_path = resolve_asset_path(article.cover_asset)
        if not cover_path.exists():
            raise ToutiaoPublishError(f"Cover asset file not found: {article.cover_asset_id}")

        try:
            add_btn = page.locator(".add-icon").first
            add_btn.scroll_into_view_if_needed()
            add_btn.click()
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击封面上传按钮: {exc}") from exc

        try:
            page.get_by_role("button", name="本地上传").wait_for(state="visible", timeout=5000)
        except Exception as exc:
            raise ToutiaoPublishError(f"封面上传对话框未出现: {exc}") from exc

        try:
            with page.expect_file_chooser(timeout=5000) as fc_info:
                page.get_by_role("button", name="本地上传").click()
            fc_info.value.set_files(str(cover_path))
        except Exception as exc:
            raise ToutiaoPublishError(f"封面文件选择失败: {exc}") from exc

        try:
            page.get_by_text("已上传 1 张图片").wait_for(timeout=60000)
        except Exception as exc:
            raise ToutiaoPublishError(f"封面上传超时（60s）: {exc}") from exc

        try:
            page.get_by_role("button", name="确定").click()
            page.wait_for_timeout(800)
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击封面确认按钮: {exc}") from exc

    def _click_publish_and_wait(self, page: Any, stop_before_publish: bool = False) -> str:
        """两步发布：先点"预览并发布"，再点"确认发布"。"""
        before_url = page.url

        # 第一步：点击"预览并发布"
        try:
            page.get_by_role("button", name="预览并发布").click()
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击「预览并发布」按钮: {exc}") from exc

        page.wait_for_timeout(1500)

        # stop_before_publish=True 时停在预览状态，等待手动确认
        if stop_before_publish:
            return page.url

        # 第二步：等待"确认发布"按钮出现并点击
        try:
            confirm_btn = page.get_by_role("button", name="确认发布")
            confirm_btn.wait_for(state="visible", timeout=8000)
            confirm_btn.click()
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击「确认发布」按钮: {exc}") from exc

        page.wait_for_timeout(1500)

        # 第三步：处理可能弹出的"作品同步授权"对话框
        try:
            ok_btn = page.get_by_role("button", name="确定")
            if ok_btn.count() and ok_btn.is_visible(timeout=3000):
                ok_btn.click()
                page.wait_for_timeout(1000)
        except Exception:
            logger.warning("Failed to dismiss post-publish popup", exc_info=True)

        # 等待页面跳转（最长 30 秒）
        try:
            page.wait_for_url(lambda url: url != before_url, timeout=30000)
            return page.url
        except Exception:
            logger.warning("URL change wait failed after publish", exc_info=True)

        # 跳转失败时根据页面文字判断发布结果
        try:
            body_text = page.locator("body").inner_text(timeout=3000)
            if any(h in body_text for h in ("发布失败", "提交失败", "操作失败", "网络错误")):
                raise ToutiaoPublishError(f"发布页面报错: {body_text[:300]}")
            if any(h in body_text for h in ("发布成功", "已发布", "审核中", "投稿成功")):
                return page.url
        except ToutiaoPublishError:
            raise
        except Exception:
            logger.warning("Failed to read body text after publish", exc_info=True)

        return page.url

    def _ensure_publish_page(self, page: Any) -> None:
        """确认当前页面是头条号发布页，且已登录。"""
        body = page.locator("body").inner_text(timeout=3000)
        haystack = f"{page.url}\n{page.title()}\n{body}"
        if any(hint in haystack for hint in LOGIN_HINTS):
            raise ToutiaoPublishError("Toutiao account appears logged out")
        if "mp.toutiao.com" not in page.url or not any(hint in haystack for hint in PUBLISH_HINTS):
            raise ToutiaoPublishError("Toutiao publish page not detected")

    def _screenshot(self, page: Any) -> bytes | None:
        """截取当前页面全屏截图（用于失败诊断）。"""
        try:
            return page.screenshot(full_page=True)
        except Exception:
            logger.warning("Failed to capture screenshot", exc_info=True)
            return None
