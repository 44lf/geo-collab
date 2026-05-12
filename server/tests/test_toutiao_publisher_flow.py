from contextlib import contextmanager
from pathlib import Path

import pytest

from server.app.models import Account, Article, Asset
from server.app.services import browser as browser_module
from server.app.services import toutiao_publisher as publisher_module
from server.app.services.toutiao_publisher import PublishFillResult, ToutiaoPublishError, ToutiaoPublisher


def test_publish_article_handles_cover_before_body(monkeypatch, tmp_path: Path) -> None:
    state_path = Path("browser_states/toutiao/account/storage_state.json")
    absolute_state_path = tmp_path / state_path
    absolute_state_path.parent.mkdir(parents=True)
    absolute_state_path.write_text("{}", encoding="utf-8")
    order: list[str] = []
    page = PublishFlowPage()
    context = PublishFlowContext()

    @contextmanager
    def fake_browser_context(**_kwargs):
        yield None, context, page

    monkeypatch.setattr(publisher_module, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(browser_module, "managed_browser_context", fake_browser_context)

    publisher = OrderedPublisher(order)
    article = Article(
        user_id=1,
        title="title",
        plain_text="body",
        content_json="{}",
        cover_asset=make_asset("cover-id"),
    )
    account = Account(user_id=1, state_path=state_path.as_posix())

    result = publisher.publish_article(article, account)

    assert result == PublishFillResult(
        url="https://example.test/published",
        title="title",
        message="发布成功: https://example.test/published",
    )
    assert order == ["ensure", "close_ai", "title", "cover", "body", "images_ready", "publish"]
    assert context.storage_state_path == str(absolute_state_path)


def test_handle_cover_skips_upload_entry_when_cover_already_present(monkeypatch, tmp_path: Path) -> None:
    cover_path = tmp_path / "cover.png"
    cover_path.write_bytes(b"fake cover")
    page = ExistingCoverPage()
    publisher = ToutiaoPublisher()
    article = Article(user_id=1, cover_asset=make_asset("cover-id"))

    monkeypatch.setattr(publisher_module, "resolve_asset_path", lambda _asset: cover_path)

    publisher._handle_cover(page, article)

    assert page.add_icon_requested is False


def test_handle_cover_failure_includes_page_hint_and_screenshot(monkeypatch, tmp_path: Path) -> None:
    cover_path = tmp_path / "cover.png"
    cover_path.write_bytes(b"fake cover")
    page = MissingCoverEntryPage()
    publisher = ToutiaoPublisher()
    article = Article(user_id=1, cover_asset=make_asset("cover-id"))

    monkeypatch.setattr(publisher_module, "resolve_asset_path", lambda _asset: cover_path)

    with pytest.raises(ToutiaoPublishError) as exc_info:
        publisher._handle_cover(page, article)

    assert "无法点击封面上传按钮" in str(exc_info.value)
    assert "页面内容摘要: 展示封面 单图 三图 无封面" in str(exc_info.value)
    assert exc_info.value.screenshot == b"shot"
    assert page.add_icon_requested is True


def test_cover_upload_entry_keeps_trying_until_local_upload_appears() -> None:
    page = CoverEntryRetryPage()

    ToutiaoPublisher()._click_cover_upload_entry(page)

    assert page.clicked == ["编辑替换", "添加封面"]
    assert page.escape_count == 1


def make_asset(asset_id: str) -> Asset:
    return Asset(
        id=asset_id,
        user_id=1,
        filename=f"{asset_id}.png",
        ext=".png",
        mime_type="image/png",
        size=1,
        sha256="0" * 64,
        storage_key=f"assets/{asset_id}.png",
        width=1,
        height=1,
    )


class OrderedPublisher(ToutiaoPublisher):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    def _ensure_publish_page(self, _page):
        self.order.append("ensure")

    def _close_ai_drawer(self, _page):
        self.order.append("close_ai")

    def _fill_title(self, _page, _title: str):
        self.order.append("title")

    def _handle_cover(self, _page, _article):
        self.order.append("cover")

    def _fill_body(self, _page, _article):
        self.order.append("body")

    def _wait_publish_images_ready(self, _page):
        self.order.append("images_ready")

    def _click_publish_and_wait(self, _page, _stop_before_publish: bool = False) -> str:
        self.order.append("publish")
        return "https://example.test/published"


class PublishFlowRole:
    def wait_for(self, **_kwargs) -> None:
        return None


class PublishFlowPage:
    def goto(self, *_args, **_kwargs) -> None:
        return None

    def get_by_role(self, *_args, **_kwargs) -> PublishFlowRole:
        return PublishFlowRole()

    def wait_for_timeout(self, _timeout: int) -> None:
        return None


class PublishFlowContext:
    def __init__(self) -> None:
        self.storage_state_path: str | None = None

    def storage_state(self, *, path: str) -> None:
        self.storage_state_path = path


class ExistingCoverPage:
    def __init__(self) -> None:
        self.add_icon_requested = False

    def evaluate(self, *_args, **_kwargs) -> bool:
        return True

    def locator(self, selector: str):
        if selector == ".add-icon":
            self.add_icon_requested = True
        return CoverBodyLocator()


class MissingCoverEntryPage:
    def __init__(self) -> None:
        self.add_icon_requested = False

    def evaluate(self, *_args, **_kwargs) -> bool:
        return False

    def get_by_text(self, *_args, **_kwargs):
        return FailingCoverLocator()

    def locator(self, selector: str):
        if selector == ".add-icon":
            self.add_icon_requested = True
        if selector == "body":
            return CoverBodyLocator()
        return FailingCoverLocator()

    def screenshot(self, **_kwargs) -> bytes:
        return b"shot"


class FailingCoverLocator:
    @property
    def first(self):
        return self

    def wait_for(self, **_kwargs) -> None:
        raise TimeoutError("missing cover entry")

    def scroll_into_view_if_needed(self, **_kwargs) -> None:
        raise TimeoutError("missing cover entry")

    def click(self, **_kwargs) -> None:
        raise TimeoutError("missing cover entry")


class CoverBodyLocator:
    @property
    def first(self):
        return self

    def inner_text(self, **_kwargs) -> str:
        return "展示封面 单图 三图 无封面"


class CoverEntryRetryPage:
    def __init__(self) -> None:
        self.clicked: list[str] = []
        self.escape_count = 0
        self.keyboard = CoverEntryKeyboard(self)

    def get_by_text(self, text: str, **_kwargs):
        return CoverEntryCandidate(self, text)

    def locator(self, _selector: str):
        return FailingCoverLocator()

    def get_by_role(self, _role: str, *, name: str):
        return LocalUploadButton(self)

    def wait_for_timeout(self, _timeout: int) -> None:
        return None


class CoverEntryKeyboard:
    def __init__(self, page: CoverEntryRetryPage) -> None:
        self.page = page

    def press(self, key: str) -> None:
        if key == "Escape":
            self.page.escape_count += 1


class CoverEntryCandidate:
    def __init__(self, page: CoverEntryRetryPage, text: str) -> None:
        self.page = page
        self.text = text

    @property
    def first(self):
        return self

    def wait_for(self, **_kwargs) -> None:
        if self.text not in {"编辑替换", "添加封面"}:
            raise TimeoutError(f"unexpected candidate: {self.text}")

    def scroll_into_view_if_needed(self, **_kwargs) -> None:
        return None

    def click(self, **_kwargs) -> None:
        self.page.clicked.append(self.text)


class LocalUploadButton:
    def __init__(self, page: CoverEntryRetryPage) -> None:
        self.page = page

    def wait_for(self, **_kwargs) -> None:
        if self.page.clicked[-1] != "添加封面":
            raise TimeoutError("local upload did not appear")
