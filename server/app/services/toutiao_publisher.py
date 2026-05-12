from __future__ import annotations

import logging
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
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


class ToutiaoUserInputRequired(ToutiaoPublishError):
    pass


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
            raise ToutiaoPublishError("标题不能为空")
        if not article_has_publishable_body(article):
            raise ToutiaoPublishError("正文不能为空")
        if article.cover_asset is None:
            raise ToutiaoPublishError("封面图片是必填项")

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
                self._dismiss_blocking_popups(page)
                self._fill_title(page, article.title)
                self._dismiss_blocking_popups(page)
                self._handle_cover(page, article)
                self._dismiss_blocking_popups(page)
                self._fill_body(page, article)
                self._dismiss_blocking_popups(page)
                self._wait_publish_images_ready(page)
                publish_url = self._click_publish_and_wait(page, stop_before_publish)
                context.storage_state(path=str(state_path))
                message = "已进入发布预览，等待手动确认" if stop_before_publish else f"发布成功: {publish_url}"
                return PublishFillResult(
                    url=publish_url,
                    title=article.title,
                    message=message,
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

    def _dismiss_blocking_popups(self, page: Any) -> None:
        """Best-effort close for marketing/help popups that block the editor."""
        workflow_text_re = re.compile(
            r"确认发布|预览并发布|本地上传|已上传|选择封面|裁剪封面|封面设置|发布设置|定时发布"
        )
        close_text_re = re.compile(r"关闭|取消|我知道了|稍后再说|暂不|以后再说|跳过|不再提示|×|✕")

        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            logger.debug("Failed to press Escape while dismissing popups", exc_info=True)

        for _ in range(3):
            try:
                closed = bool(
                    page.evaluate(
                        """
                        ({ workflowPattern, closePattern }) => {
                          const workflowRe = new RegExp(workflowPattern);
                          const closeRe = new RegExp(closePattern, "i");
                          const visible = (node) => {
                            if (!node || !node.getBoundingClientRect) return false;
                            const style = window.getComputedStyle(node);
                            const rect = node.getBoundingClientRect();
                            return style.display !== "none" &&
                              style.visibility !== "hidden" &&
                              rect.width > 0 &&
                              rect.height > 0;
                          };
                          const roots = Array.from(document.querySelectorAll([
                            "[role='dialog']",
                            "[aria-modal='true']",
                            "[class*='modal']",
                            "[class*='dialog']",
                            "[class*='popup']",
                            "[class*='popover']",
                            "[class*='drawer']"
                          ].join(","))).filter(visible);
                          for (const root of roots) {
                            const text = String(root.innerText || "");
                            if (workflowRe.test(text)) continue;
                            const candidates = Array.from(root.querySelectorAll([
                              "button",
                              "[role='button']",
                              "a",
                              "span",
                              "i",
                              "svg",
                              "[class*='close']",
                              "[aria-label*='关闭']",
                              "[title*='关闭']"
                            ].join(","))).filter(visible);
                            for (const node of candidates) {
                              const haystack = [
                                node.innerText,
                                node.getAttribute("aria-label"),
                                node.getAttribute("title"),
                                node.getAttribute("class")
                              ].join(" ");
                              if (!closeRe.test(String(haystack || ""))) continue;
                              node.click();
                              return true;
                            }
                          }
                          return false;
                        }
                        """,
                        {
                            "workflowPattern": workflow_text_re.pattern,
                            "closePattern": close_text_re.pattern,
                        },
                    )
                )
            except Exception:
                logger.debug("Failed to dismiss blocking popup via DOM", exc_info=True)
                return
            if not closed:
                return
            try:
                page.wait_for_timeout(500)
            except Exception:
                return

    def _fill_title(self, page: Any, title: str) -> None:
        """填充文章标题（最多 50 字）。"""
        # 主选择器：aria role（慢机器给足 20 秒）
        try:
            field = page.get_by_role("textbox", name="请输入文章标题")
            field.wait_for(state="visible", timeout=20000)
            field.click()
            field.press_sequentially(title[:50])
            return
        except Exception:
            logger.warning("Title field not found via textbox role, trying CSS fallback", exc_info=True)
        # 兜底：placeholder 包含"标题"的 input
        try:
            field = page.locator("input[placeholder*='标题']").first
            field.wait_for(state="visible", timeout=5000)
            field.click()
            field.press_sequentially(title[:50])
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
                page.keyboard.press("End")
                page.keyboard.press("Enter")

    def _insert_body_text(self, page: Any, text: str) -> None:
        if not text:
            return
        page.keyboard.type(text)

    def _body_segments(self, article: Article) -> list[BodySegment]:
        content_json = loads_content_json(article.content_json)
        segments: list[BodySegment] = []
        self._append_tiptap_segments(content_json, segments)
        segments = self._compact_segments(segments)
        if segments:
            return segments

        body = (article.plain_text or re.sub(r"<[^>]+>", "", article.content_html or "")).strip()
        return [BodySegment(kind="text", text=body)] if body else []

    def _append_tiptap_segments(self, node: Any, segments: list[BodySegment], depth: int = 0) -> None:
        if isinstance(node, list):
            for child in node:
                self._append_tiptap_segments(child, segments, depth)
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
                self._append_tiptap_segments(child, segments, depth + (1 if node_type in ("orderedList", "bulletList") else 0))

        if node_type in {"paragraph", "heading"}:
            segments.append(BodySegment(kind="text", text="\n"))

    def _compact_segments(self, segments: list[BodySegment]) -> list[BodySegment]:
        compacted: list[BodySegment] = []
        for segment in segments:
            if segment.kind == "text":
                if not segment.text:
                    continue
                # Keep standalone newline segments unmerged (they separate images/paragraphs)
                if segment.text == "\n":
                    compacted.append(segment)
                    continue
                if compacted and compacted[-1].kind == "text" and compacted[-1].text != "\n":
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

        try:
            self._open_body_image_drawer(page)
            self._upload_body_image_in_drawer(page, image_path)
            self._confirm_body_image_drawer(page)
            self._wait_body_image_inserted(page, before_count)
        except Exception as exc:
            after_count = self._body_image_count(page)
            page_closed = self._page_is_closed(page)
            screenshot = self._screenshot(page)
            raise ToutiaoPublishError(
                (
                    f"正文图片未能插入编辑器: {asset.id}; "
                    f"before={before_count}; after={after_count}; "
                    f"page_closed={page_closed}; error={type(exc).__name__}: {exc}"
                ),
                screenshot,
            ) from exc

    def _open_body_image_drawer(self, page: Any) -> None:
        candidates = [
            "div.syl-toolbar-tool.image.static",
            ".syl-toolbar-tool.image",
            "[class*='syl-toolbar-tool'][class*='image']",
        ]
        last_error: Exception | None = None
        for selector in candidates:
            try:
                button = page.locator(selector).first
                button.wait_for(state="visible", timeout=5000)
                button.click(timeout=5000)
                page.locator(".mp-ic-img-drawer").wait_for(state="visible", timeout=10000)
                return
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise ToutiaoPublishError("未找到正文图片上传入口")

    def _upload_body_image_in_drawer(self, page: Any, image_path: Path) -> None:
        drawer = page.locator(".mp-ic-img-drawer").last
        file_input = drawer.locator("input[type='file'][accept*='image']").first
        file_input.wait_for(state="attached", timeout=10000)
        file_input.set_input_files(str(image_path))
        try:
            drawer.get_by_text(re.compile(r"已上传\s*\d+\s*张图片")).wait_for(timeout=60000)
        except Exception as exc:
            raise ToutiaoPublishError(f"正文图片上传超时（60s）: {exc}") from exc

    def _confirm_body_image_drawer(self, page: Any) -> None:
        drawer = page.locator(".mp-ic-img-drawer").last
        candidates = [
            drawer.get_by_role("button", name="确定"),
            drawer.locator("button:has-text('确定')").last,
            page.locator(".byte-drawer button:has-text('确定')").last,
        ]
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                candidate.wait_for(state="visible", timeout=10000)
                candidate.click(timeout=5000)
                page.wait_for_timeout(1000)
                return
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise ToutiaoPublishError("未找到正文图片确认按钮")

    def _wait_body_image_inserted(self, page: Any, before_count: int, timeout_ms: int = 30000) -> None:
        page.wait_for_function(
            "count => document.querySelectorAll(\"[contenteditable='true'] img\").length > count",
            arg=before_count,
            timeout=timeout_ms,
        )
        page.wait_for_timeout(1000)

    def _wait_body_image_ready(self, page: Any, before_count: int, timeout_ms: int = 30000) -> None:
        page.wait_for_function(
            "count => document.querySelectorAll(\"[contenteditable='true'] img\").length > count",
            arg=before_count,
            timeout=timeout_ms,
        )
        page.wait_for_function(
            """
            count => {
              const images = Array.from(document.querySelectorAll("[contenteditable='true'] img"));
              return images.length > count &&
                images.every((img) => img.complete && img.naturalWidth > 0);
            }
            """,
            arg=before_count,
            timeout=timeout_ms,
        )
        page.wait_for_timeout(4000)

    def _wait_publish_images_ready(self, page: Any, timeout_ms: int = 120000) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        stable_rounds = 0
        last_state: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            state = self._publish_image_state(page)
            last_state = state
            if (
                state["invalid_count"] == 0
                and state["pending_count"] == 0
                and not state["has_progress"]
                and not state["has_uploading_text"]
            ):
                stable_rounds += 1
                if stable_rounds >= 2:
                    page.wait_for_timeout(500)
                    return
            else:
                stable_rounds = 0
            page.wait_for_timeout(2000)

        screenshot = self._screenshot(page)
        raise ToutiaoPublishError(f"正文图片上传未完成，仍存在临时图片 URI: {last_state}", screenshot)

    def _publish_image_state(self, page: Any) -> dict[str, Any]:
        return page.evaluate(
            """
            () => {
              const editables = Array.from(document.querySelectorAll("[contenteditable='true']"));
              const images = editables.flatMap((node) => Array.from(node.querySelectorAll("img")));
              const isVisible = (node) => {
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== "none" &&
                  style.visibility !== "hidden" &&
                  rect.width > 0 &&
                  rect.height > 0;
              };
              const isTemporarySrc = (src) => {
                if (!src) return true;
                const value = String(src);
                return value.startsWith("blob:") ||
                  value.startsWith("data:") ||
                  value.startsWith("file:") ||
                  value.includes("127.0.0.1") ||
                  value.includes("localhost") ||
                  value.includes("/api/assets/");
              };
              const states = images.map((img, index) => {
                const src = img.currentSrc || img.src || img.getAttribute("src") || "";
                return {
                  index,
                  src: String(src).slice(0, 180),
                  complete: Boolean(img.complete),
                  naturalWidth: Number(img.naturalWidth || 0),
                  temporary: isTemporarySrc(src),
                };
              });
              const progressSelectors = [
                "[role='progressbar']",
                ".byte-progress",
                ".semi-progress",
                "[class*='progress']",
                "[class*='Progress']",
                "[class*='uploading']",
                "[class*='Uploading']"
              ];
              const progressNodes = editables.flatMap((node) =>
                progressSelectors.flatMap((selector) => Array.from(node.querySelectorAll(selector)))
              );
              const bodyText = document.body?.innerText || "";
              return {
                image_count: states.length,
                invalid_count: states.filter((item) => item.temporary).length,
                pending_count: states.filter((item) => !item.complete || item.naturalWidth <= 0).length,
                invalid_sources: states.filter((item) => item.temporary).map((item) => item.src),
                pending_sources: states
                  .filter((item) => !item.complete || item.naturalWidth <= 0)
                  .map((item) => item.src),
                has_progress: progressNodes.some(isVisible),
                has_uploading_text: /上传中|正在上传|图片处理中|加载中|处理中/.test(bodyText),
              };
            }
            """
        )

    @staticmethod
    def _set_clipboard_files(paths: list[Path]) -> None:
        """将文件路径写入 Windows 剪贴板（CF_HDROP），模拟资源管理器复制文件。"""
        if sys.platform != "win32":
            raise ToutiaoPublishError("正文图片粘贴仅支持 Windows 文件剪贴板")

        absolute_paths = [str(path.resolve()) for path in paths]
        if not absolute_paths:
            raise ToutiaoPublishError("正文图片粘贴文件列表为空")

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL

        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL

        cf_hdrop = 15
        preferred_drop_effect = user32.RegisterClipboardFormatW("Preferred DropEffect")
        gmem_moveable = 0x0002
        gmem_zeroinit = 0x0040
        payload = ToutiaoPublisher._build_hdrop_payload(absolute_paths)
        drop_effect_payload = struct.pack("<I", 1)

        handle = kernel32.GlobalAlloc(gmem_moveable | gmem_zeroinit, len(payload))
        if not handle:
            raise ToutiaoPublishError("无法分配正文图片剪贴板内存")
        drop_effect_handle = kernel32.GlobalAlloc(gmem_moveable | gmem_zeroinit, len(drop_effect_payload))
        if not drop_effect_handle:
            kernel32.GlobalFree(handle)
            raise ToutiaoPublishError("无法分配正文图片剪贴板操作内存")

        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            kernel32.GlobalFree(drop_effect_handle)
            raise ToutiaoPublishError("无法锁定正文图片剪贴板内存")

        try:
            ctypes.memmove(locked, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)

        locked = kernel32.GlobalLock(drop_effect_handle)
        if not locked:
            kernel32.GlobalFree(handle)
            kernel32.GlobalFree(drop_effect_handle)
            raise ToutiaoPublishError("无法锁定正文图片剪贴板操作内存")

        try:
            ctypes.memmove(locked, drop_effect_payload, len(drop_effect_payload))
        finally:
            kernel32.GlobalUnlock(drop_effect_handle)

        opened = False
        try:
            for _ in range(10):
                if user32.OpenClipboard(None):
                    opened = True
                    break
                time.sleep(0.05)
            if not opened:
                raise ToutiaoPublishError("无法打开 Windows 剪贴板")

            if not user32.EmptyClipboard():
                raise ToutiaoPublishError("无法清空 Windows 剪贴板")
            if not user32.SetClipboardData(cf_hdrop, handle):
                raise ToutiaoPublishError("无法写入正文图片文件到 Windows 剪贴板")
            if not preferred_drop_effect or not user32.SetClipboardData(preferred_drop_effect, drop_effect_handle):
                raise ToutiaoPublishError("无法写入正文图片剪贴板复制标记")
            handle = None
            drop_effect_handle = None
        finally:
            if opened:
                user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)
            if drop_effect_handle:
                kernel32.GlobalFree(drop_effect_handle)

    @staticmethod
    def _build_hdrop_payload(absolute_paths: list[str]) -> bytes:
        dropfiles_header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
        file_list = ("\0".join(absolute_paths) + "\0\0").encode("utf-16le")
        return dropfiles_header + file_list

    def _handle_cover(self, page: Any, article: Article) -> None:
        """上传封面图片。封面图是必填项。"""
        if article.cover_asset is None:
            raise ToutiaoPublishError("封面图片是必填项，article 没有关联 cover_asset")

        cover_path = resolve_asset_path(article.cover_asset)
        if not cover_path.exists():
            raise ToutiaoPublishError(f"Cover asset file not found: {article.cover_asset_id}")

        if self._cover_already_present(page):
            return

        try:
            self._click_cover_upload_entry(page)
        except Exception as exc:
            body_hint = self._body_text_hint(page)
            screenshot = self._screenshot(page)
            raise ToutiaoPublishError(
                f"无法点击封面上传按钮: {exc}\n页面内容摘要: {body_hint}",
                screenshot,
            ) from exc

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

    def _cover_already_present(self, page: Any) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                      const bodyText = document.body?.innerText || "";
                      if (!/(编辑替换|已上传\\s*1\\s*张图片)/.test(bodyText)) return false;
                      const visibleImage = Array.from(document.querySelectorAll("img")).some((img) => {
                        if (img.closest("[contenteditable='true']")) return false;
                        const rect = img.getBoundingClientRect();
                        const style = window.getComputedStyle(img);
                        return img.complete &&
                          img.naturalWidth > 0 &&
                          rect.width >= 40 &&
                          rect.height >= 40 &&
                          style.display !== "none" &&
                          style.visibility !== "hidden";
                      });
                      return visibleImage || /已上传\\s*1\\s*张图片/.test(bodyText);
                    }
                    """
                )
            )
        except Exception:
            logger.warning("Failed to detect existing Toutiao cover", exc_info=True)
            return False

    def _click_cover_upload_entry(self, page: Any) -> None:
        candidates = [
            page.get_by_text("编辑替换").first,
            page.get_by_text("添加封面").first,
            page.locator("[class*='cover'] .add-icon").first,
            page.locator(".add-icon").first,
        ]
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                candidate.wait_for(state="visible", timeout=3000)
                candidate.scroll_into_view_if_needed(timeout=3000)
                candidate.click(timeout=3000)
                page.get_by_role("button", name="本地上传").wait_for(state="visible", timeout=7000)
                return
            except Exception as exc:
                last_error = exc
                self._dismiss_cover_candidate_side_effect(page)
                continue
        if last_error is not None:
            raise last_error
        raise ToutiaoPublishError("未找到封面上传入口")

    def _dismiss_cover_candidate_side_effect(self, page: Any) -> None:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            logger.debug("Failed to dismiss failed cover entry side effect", exc_info=True)

    def _click_publish_and_wait(self, page: Any, stop_before_publish: bool = False) -> str:
        """两步发布：先点"预览并发布"，再点"确认发布"。"""
        before_url = page.url

        # 第一步：点击"预览并发布"
        try:
            self._dismiss_blocking_popups(page)
            page.get_by_role("button", name="预览并发布").click()
        except Exception as exc:
            raise ToutiaoPublishError(f"无法点击「预览并发布」按钮: {exc}") from exc

        page.wait_for_timeout(1500)
        self._dismiss_blocking_popups(page)

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
            raise ToutiaoUserInputRequired("Toutiao account appears logged out; user login or verification is required")
        if "mp.toutiao.com" not in page.url or not any(hint in haystack for hint in PUBLISH_HINTS):
            raise ToutiaoPublishError("Toutiao publish page not detected")

    def _screenshot(self, page: Any) -> bytes | None:
        """截取当前页面全屏截图（用于失败诊断）。"""
        try:
            return page.screenshot(full_page=True)
        except Exception:
            logger.warning("Failed to capture screenshot", exc_info=True)
            return None

    def _page_is_closed(self, page: Any) -> bool | str:
        try:
            return bool(page.is_closed())
        except Exception as exc:
            return f"unknown: {type(exc).__name__}: {exc}"
