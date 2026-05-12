import struct
from pathlib import Path

from server.app.models import Asset
from server.app.services import toutiao_publisher as publisher_module
from server.app.services.clipboard import build_hdrop_payload, set_clipboard_files
from server.app.services.toutiao_publisher import ToutiaoPublisher


def test_build_hdrop_payload_uses_wide_file_list() -> None:
    path = r"C:\Users\Administrator\AppData\Local\GeoCollab\assets\2026\05\image.png"

    payload = build_hdrop_payload([path])
    p_files, x, y, fnc, f_wide = struct.unpack("<IiiII", payload[:20])

    assert (p_files, x, y, fnc, f_wide) == (20, 0, 0, 0, 1)
    assert payload[20:] == (path + "\0\0").encode("utf-16le")


def test_paste_body_image_uses_toutiao_upload_drawer(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"fake image")
    asset = Asset(
        id="asset-id",
        filename="image.png",
        ext=".png",
        mime_type="image/png",
        size=image_path.stat().st_size,
        sha256="0" * 64,
        storage_key="assets/image.png",
        width=1,
        height=1,
    )
    page = FakePage()
    publisher = ToutiaoPublisher()

    monkeypatch.setattr(publisher_module, "resolve_asset_path", lambda _: image_path)

    publisher._paste_body_image(page, asset)

    assert page.clicked == ["div.syl-toolbar-tool.image.static", "drawer-confirm-role"]
    assert page.uploaded_files == [str(image_path)]
    assert page.waited_for_uploaded_text is True
    assert page.waited_for_image_count == 0
    assert page.wait_arg_was_keyword is True
    assert page.wait_for_function_timeouts == [30000]
    assert page.waited_after_success_ms == [1000, 1000]


def test_wait_publish_images_ready_waits_for_non_temporary_uri() -> None:
    page = PublishImageStatePage(
        [
            {
                "image_count": 1,
                "invalid_count": 1,
                "pending_count": 0,
                "invalid_sources": ["blob:https://mp.toutiao.com/local"],
                "pending_sources": [],
                "has_progress": False,
                "has_uploading_text": False,
            },
            {
                "image_count": 1,
                "invalid_count": 0,
                "pending_count": 0,
                "invalid_sources": [],
                "pending_sources": [],
                "has_progress": False,
                "has_uploading_text": False,
            },
            {
                "image_count": 1,
                "invalid_count": 0,
                "pending_count": 0,
                "invalid_sources": [],
                "pending_sources": [],
                "has_progress": False,
                "has_uploading_text": False,
            },
        ]
    )

    ToutiaoPublisher()._wait_publish_images_ready(page)

    assert page.waits == [2000, 2000, 500]


class FakeLocator:
    def __init__(self, page: "FakePage", name: str) -> None:
        self.page = page
        self.name = name

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def count(self) -> int:
        return 0

    def wait_for(self, **_kwargs) -> None:
        if self.name == "uploaded-text":
            self.page.waited_for_uploaded_text = True

    def click(self, **_kwargs) -> None:
        self.page.clicked.append(self.name)

    def locator(self, selector: str) -> "FakeLocator":
        if selector == "input[type='file'][accept*='image']":
            return FakeLocator(self.page, "drawer-file-input")
        if selector == "button:has-text('确定')":
            return FakeLocator(self.page, "drawer-confirm-css")
        return FakeLocator(self.page, selector)

    def get_by_text(self, _pattern) -> "FakeLocator":
        return FakeLocator(self.page, "uploaded-text")

    def get_by_role(self, _role: str, *, name: str) -> "FakeLocator":
        assert name == "确定"
        return FakeLocator(self.page, "drawer-confirm-role")

    def set_input_files(self, path: str) -> None:
        self.page.uploaded_files.append(path)


class FakePage:
    def __init__(self) -> None:
        self.clicked: list[str] = []
        self.uploaded_files: list[str] = []
        self.waited_for_uploaded_text = False
        self.waited_for_image_count: int | None = None
        self.wait_arg_was_keyword = False
        self.wait_for_function_timeouts: list[int] = []
        self.waited_after_success_ms: list[int] = []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def evaluate(self, *_args, **_kwargs) -> None:
        return None

    def wait_for_function(self, _script: str, *, arg: int, timeout: int) -> None:
        self.wait_for_function_timeouts.append(timeout)
        self.waited_for_image_count = arg
        self.wait_arg_was_keyword = True

    def wait_for_timeout(self, timeout: int) -> None:
        self.waited_after_success_ms.append(timeout)


class PublishImageStatePage:
    def __init__(self, states: list[dict[str, object]]) -> None:
        self.states = states
        self.waits: list[int] = []

    def evaluate(self, *_args, **_kwargs) -> dict[str, object]:
        if len(self.states) == 1:
            return self.states[0]
        return self.states.pop(0)

    def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)
