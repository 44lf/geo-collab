from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

from server.app.core.paths import get_data_dir
from server.app.models import Account, Article, Asset
from server.app.services.accounts import account_key_from_state_path, launch_options, profile_dir_for_key
from server.app.services.articles import article_has_publishable_body, loads_content_json
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


@dataclass(frozen=True)
class BodySegment:
    kind: str
    text: str = ""
    asset_id: str | None = None


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
        if not article.title or not article.title.strip():
            raise ValueError("文章标题不能为空")
        if not article_has_publishable_body(article):
            raise ValueError("文章正文不能为空")
        if article.cover_asset is None:
            raise ValueError("文章封面不能为空")

        from server.app.services.browser import managed_browser_context

        account_key = account_key_from_state_path(account.state_path)
        state_path = (get_data_dir() / account.state_path).resolve()
        if not state_path.exists():
            raise ToutiaoPublishError(f"Account storage state not found: {account.state_path}")

        with managed_browser_context(
            account_key=account_key,
            channel=self.channel,
            executable_path=self.executable_path,
        ) as (playwright, context, page):
            try:
                page.goto(TOUTIAO_PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.get_by_role("textbox", name="请输入文章标题").wait_for(state="visible", timeout=20000)
                except Exception:
                    pass
                self._ensure_publish_page(page)
                self._close_ai_drawer(page)
                self._fill_title(page, article.title)
                self._fill_body(page, article)
                self._handle_cover(page, article)
                page.wait_for_timeout(1000)
                publish_url = self._click_publish_and_wait(page, stop_before_publish)
                context.storage_state(path=str(state_path))
                return PublishFillResult(
                    url=publish_url,
                    title=article.title,
                    message=f"发布成功: {publish_url}",
                )
            except ToutiaoPublishError:
                raise
            except Exception as exc:
                screenshot = self._screenshot(page)
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
        # 主选择器：aria role（慢机器给足 20 秒）
        try:
            field = page.get_by_role("textbox", name="请输入文章标题")
            field.wait_for(state="visible", timeout=20000)
            field.click()
            field.fill(title[:30])
            return
        except Exception:
            logger.warning("Title field not found via textbox role, trying CSS fallback", exc_info=True)
        # 兜底：placeholder 包含"标题"的 input
        try:
            field = page.locator("input[placeholder*='标题']").first
            field.wait_for(state="visible", timeout=5000)
            field.click()
            field.fill(title[:30])
            return
        except Exception:
            logger.warning("Title field not found via CSS fallback", exc_info=True)
        raise ToutiaoPublishError("Toutiao title field not found")

    def _focus_body_editor(self, page: Any) -> None:
        """聚焦正文编辑区。"""
        try:
            para = page.get_by_role("paragraph").first
            para.scroll_into_view_if_needed()
            para.click()
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
                return
            except Exception:
                logger.warning("Failed to fill body via contenteditable fallback", exc_info=True)
                continue
        raise ToutiaoPublishError("Toutiao body editor not found")

    def _fill_body(self, page: Any, article: Article) -> None:
        """按 Tiptap JSON 顺序填充正文文本和正文图片。"""
        segments = self._body_segments(article)
        if not segments:
            raise ToutiaoPublishError("文章正文为空")

        self._focus_body_editor(page)
        for segment in segments:
            if segment.kind == "text":
                self._insert_body_text(page, segment.text)
            elif segment.kind == "image":
                asset = self._body_asset_for_segment(article, segment)
                self._paste_body_image(page, asset)
                self._insert_body_text(page, "\n")

    def _insert_body_text(self, page: Any, text: str) -> None:
        if not text:
            return
        page.evaluate("(text) => document.execCommand('insertText', false, text)", text)

    def _body_segments(self, article: Article) -> list[BodySegment]:
        content_json = loads_content_json(article.content_json)
        segments: list[BodySegment] = []
        self._append_tiptap_segments(content_json, segments)
        segments = self._compact_segments(segments)
        if segments:
            return segments

        body = (article.plain_text or re.sub(r"<[^>]+>", "", article.content_html or "")).strip()
        return [BodySegment(kind="text", text=body)] if body else []

    def _append_tiptap_segments(self, node: Any, segments: list[BodySegment]) -> None:
        if isinstance(node, list):
            for child in node:
                self._append_tiptap_segments(child, segments)
            return
        if not isinstance(node, dict):
            return

        node_type = node.get("type")
        if node_type == "text":
            text = node.get("text")
            if isinstance(text, str) and text:
                segments.append(BodySegment(kind="text", text=text))
            return
        if node_type == "hardBreak":
            segments.append(BodySegment(kind="text", text="\n"))
            return
        if node_type == "image":
            asset_id = self._asset_id_from_tiptap_image(node)
            if asset_id:
                segments.append(BodySegment(kind="image", asset_id=asset_id))
            return

        content = node.get("content")
        if isinstance(content, list):
            for index, child in enumerate(content):
                if node_type == "orderedList" and isinstance(child, dict) and child.get("type") == "listItem":
                    segments.append(BodySegment(kind="text", text=f"{index + 1}. "))
                elif node_type == "bulletList" and isinstance(child, dict) and child.get("type") == "listItem":
                    segments.append(BodySegment(kind="text", text="- "))
                self._append_tiptap_segments(child, segments)

        if node_type in {"paragraph", "heading", "blockquote", "listItem"}:
            segments.append(BodySegment(kind="text", text="\n"))

    def _compact_segments(self, segments: list[BodySegment]) -> list[BodySegment]:
        compacted: list[BodySegment] = []
        for segment in segments:
            if segment.kind == "text":
                if not segment.text:
                    continue
                if compacted and compacted[-1].kind == "text":
                    previous = compacted.pop()
                    compacted.append(BodySegment(kind="text", text=previous.text + segment.text))
                else:
                    compacted.append(segment)
            else:
                compacted.append(segment)
        while compacted and compacted[-1].kind == "text" and not compacted[-1].text.strip():
            compacted.pop()
        return compacted

    def _asset_id_from_tiptap_image(self, node: dict[str, Any]) -> str | None:
        attrs = node.get("attrs")
        if not isinstance(attrs, dict):
            return None
        for key in ("assetId", "asset_id", "dataAssetId"):
            value = attrs.get(key)
            if isinstance(value, str) and value:
                return value
        src = attrs.get("src")
        if isinstance(src, str) and "/api/assets/" in src:
            return src.rstrip("/").split("/api/assets/")[-1].split("?")[0]
        return None

    def _body_asset_for_segment(self, article: Article, segment: BodySegment) -> Asset:
        asset_id = segment.asset_id
        for link in sorted(article.body_assets, key=lambda item: item.position):
            if link.asset_id == asset_id and link.asset is not None:
                return link.asset
        raise ToutiaoPublishError(f"正文图片资源不存在或未加载: {asset_id}")

    def _body_image_count(self, page: Any) -> int:
        try:
            return page.locator("[contenteditable='true'] img").count()
        except Exception:
            return 0

    def _paste_body_image(self, page: Any, asset: Asset) -> None:
        image_path = resolve_asset_path(asset)
        if not image_path.exists():
            raise ToutiaoPublishError(f"正文图片文件不存在: {asset.id}")

        before_count = self._body_image_count(page)
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        page.evaluate(
            """
            async ({ data, mimeType, filename }) => {
              const active = document.activeElement;
              const target =
                active?.closest?.("[contenteditable='true']") ||
                document.querySelector("[contenteditable='true']") ||
                active ||
                document.body;
              target.focus?.();

              const binary = atob(data);
              const bytes = new Uint8Array(binary.length);
              for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
              const file = new File([bytes], filename, { type: mimeType });
              const transfer = new DataTransfer();
              transfer.items.add(file);

              const event = new Event("paste", { bubbles: true, cancelable: true });
              Object.defineProperty(event, "clipboardData", { value: transfer });
              target.dispatchEvent(event);
            }
            """,
            {
                "data": encoded,
                "mimeType": asset.mime_type or "image/png",
                "filename": asset.filename or image_path.name,
            },
        )
        try:
            page.wait_for_function(
                "count => document.querySelectorAll(\"[contenteditable='true'] img\").length > count",
                before_count,
                timeout=45000,
            )
            page.wait_for_timeout(1000)
        except Exception as exc:
            raise ToutiaoPublishError(f"正文图片未能插入编辑器: {asset.id}") from exc

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
            confirm_btn.wait_for(state="visible", timeout=max(self.wait_ms, 30000))
            confirm_btn.click()
        except Exception as exc:
            body_hint = self._body_text_hint(page)
            screenshot = self._screenshot(page)
            raise ToutiaoPublishError(f"无法点击「确认发布」按钮: {exc}\n页面内容摘要: {body_hint}", screenshot) from exc

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

    def _body_text_hint(self, page: Any, limit: int = 600) -> str:
        try:
            text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            return "无法读取页面内容"
        compact = re.sub(r"\s+", " ", text).strip()
        return compact[:limit] if compact else "页面正文为空"

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
