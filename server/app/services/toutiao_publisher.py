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
    ):
        self.channel = channel
        self.executable_path = executable_path
        self.wait_ms = wait_ms

    def publish_article(self, article: Article, account: Account) -> PublishFillResult:
        """Fill the article form and click publish. Closes the browser when done."""
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
            self._close_ai_drawer(page)
            self._fill_title(page, article.title)
            self._fill_body(page, article.plain_text or article.content_html)
            self._handle_cover(page, article)
            page.wait_for_timeout(1000)
            publish_url = self._click_publish_and_wait(page)
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
        # 头条号 AI 创作助手抽屉会遮挡正文编辑区，先关掉
        try:
            btn = page.locator(".close-btn").first
            if btn.count() and btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(500)
        except Exception:
            pass

    def _fill_title(self, page: Any, title: str) -> None:
        # playwright-cli 实测: textbox role，placeholder "请输入文章标题（2～30个字）"
        field = page.get_by_role("textbox", name="请输入文章标题")
        try:
            field.wait_for(state="visible", timeout=5000)
            field.click()
            field.fill(title[:30])
            return
        except Exception:
            pass
        raise ToutiaoPublishError("Toutiao title field not found")

    def _fill_body(self, page: Any, body: str) -> None:
        # playwright-cli 实测: paragraph role（contenteditable div 的占位段落）
        try:
            para = page.get_by_role("paragraph").first
            para.scroll_into_view_if_needed()
            para.click()
            page.keyboard.type(body or " ")
            return
        except Exception:
            pass
        # 兜底：找最大的 contenteditable div
        editable = page.locator("[contenteditable='true']")
        for i in range(editable.count()):
            field = editable.nth(i)
            try:
                box = field.bounding_box()
                if not box or box["height"] < 80:
                    continue
                field.scroll_into_view_if_needed()
                field.click()
                page.keyboard.type(body or " ")
                return
            except Exception:
                continue
        raise ToutiaoPublishError("Toutiao body editor not found")

    def _handle_cover(self, page: Any, article: Article) -> None:
        # playwright-cli 实测流程：
        # 1. 点封面区的加号(.add-icon) → 弹出上传对话框
        # 2. 点"本地上传" → 触发 file chooser
        # 3. setFiles() → 上传，进度条出现
        # 4. 等待"已上传 1 张图片"文字（网络完成标志）
        # 5. 点"确定"关闭对话框
        if article.cover_asset is None:
            raise ToutiaoPublishError("封面图片是必填项，article 没有关联 cover_asset")

        cover_path = resolve_asset_path(article.cover_asset)
        if not cover_path.exists():
            raise ToutiaoPublishError(f"Cover asset file not found: {article.cover_asset_id}")

        # 点击封面加号
        try:
            add_btn = page.locator(".add-icon").first
            add_btn.scroll_into_view_if_needed()
            add_btn.click()
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击封面上传按钮: {exc}") from exc

        # 等待上传对话框出现
        try:
            page.get_by_role("button", name="本地上传").wait_for(state="visible", timeout=5000)
        except Exception as exc:
            raise ToutiaoPublishError(f"封面上传对话框未出现: {exc}") from exc

        # 点"本地上传"触发 file chooser，然后 set_files
        try:
            with page.expect_file_chooser(timeout=5000) as fc_info:
                page.get_by_role("button", name="本地上传").click()
            fc_info.value.set_files(str(cover_path))
        except Exception as exc:
            raise ToutiaoPublishError(f"封面文件选择失败: {exc}") from exc

        # 等待上传完成（"已上传 1 张图片"文字出现），最多 60 秒
        try:
            page.get_by_text("已上传 1 张图片").wait_for(timeout=60000)
        except Exception as exc:
            raise ToutiaoPublishError(f"封面上传超时（60s）: {exc}") from exc

        # 点"确定"确认封面
        try:
            page.get_by_role("button", name="确定").click()
            page.wait_for_timeout(800)
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击封面确认按钮: {exc}") from exc

    def _click_publish_and_wait(self, page: Any) -> str:
        before_url = page.url

        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # 第一步：点"预览并发布"→ 页面滚到发布设置，按钮变成"确认发布"
        # playwright-cli 实测生成的代码：page.getByRole('button', { name: '预览并发布' }).click()
        try:
            page.get_by_role("button", name="预览并发布").click()
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击「预览并发布」按钮: {exc}") from exc

        page.wait_for_timeout(1500)

        # 第二步：等待"确认发布"按钮出现并点击
        try:
            confirm_btn = page.get_by_role("button", name="确认发布")
            confirm_btn.wait_for(state="visible", timeout=8000)
            confirm_btn.click()
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击「确认发布」按钮: {exc}") from exc

        page.wait_for_timeout(1500)

        # 第三步：处理可能弹出的"作品同步授权"对话框（首次发布会出现）
        try:
            ok_btn = page.get_by_role("button", name="确定")
            if ok_btn.count() and ok_btn.is_visible(timeout=3000):
                ok_btn.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        # 等待页面跳转（最多 30 秒）
        try:
            page.wait_for_url(lambda url: url != before_url, timeout=30000)
            return page.url
        except Exception:
            pass

        # 跳转失败时检查页面文字判断成功/失败
        try:
            body_text = page.locator("body").inner_text(timeout=3000)
            if any(h in body_text for h in ("发布失败", "提交失败", "操作失败", "网络错误")):
                raise ToutiaoPublishError(f"发布页面报错: {body_text[:300]}")
            if any(h in body_text for h in ("发布成功", "已发布", "审核中", "投稿成功")):
                return page.url
        except ToutiaoPublishError:
            raise
        except Exception:
            pass

        return page.url

    def _ensure_publish_page(self, page: Any) -> None:
        body = page.locator("body").inner_text(timeout=3000)
        haystack = f"{page.url}\n{page.title()}\n{body}"
        if any(hint in haystack for hint in LOGIN_HINTS):
            raise ToutiaoPublishError("Toutiao account appears logged out")
        if "mp.toutiao.com" not in page.url or not any(hint in haystack for hint in PUBLISH_HINTS):
            raise ToutiaoPublishError("Toutiao publish page not detected")

    def _screenshot(self, page: Any) -> bytes | None:
        try:
            return page.screenshot(full_page=True)
        except Exception:
            return None
