import struct
from pathlib import Path

from server.app.models import Asset
from server.app.services import toutiao_publisher as publisher_module
from server.app.services.toutiao_publisher import ToutiaoPublisher


def test_build_hdrop_payload_uses_wide_file_list() -> None:
    path = r"C:\Users\Administrator\AppData\Local\GeoCollab\assets\2026\05\image.png"

    payload = ToutiaoPublisher._build_hdrop_payload([path])
    p_files, x, y, fnc, f_wide = struct.unpack("<IiiII", payload[:20])

    assert (p_files, x, y, fnc, f_wide) == (20, 0, 0, 0, 1)
    assert payload[20:] == (path + "\0\0").encode("utf-16le")


def test_paste_body_image_uses_file_clipboard_and_ctrl_v(monkeypatch, tmp_path: Path) -> None:
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
    copied_paths: list[Path] = []

    monkeypatch.setattr(publisher_module, "resolve_asset_path", lambda _: image_path)
    monkeypatch.setattr(publisher, "_set_clipboard_files", lambda paths: copied_paths.extend(paths))

    publisher._paste_body_image(page, asset)

    assert copied_paths == [image_path]
    assert page.keyboard.pressed == ["Control+V"]
    assert page.evaluate_calls == 0
    assert page.waited_for_image_count == 0
    assert page.wait_arg_was_keyword is True
    assert page.wait_for_function_timeouts == [30000, 30000]
    assert page.waited_after_success_ms == 4000


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


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)


class FakeLocator:
    def count(self) -> int:
        return 0


class FakePage:
    def __init__(self) -> None:
        self.keyboard = FakeKeyboard()
        self.evaluate_calls = 0
        self.waited_for_image_count: int | None = None
        self.wait_arg_was_keyword = False
        self.wait_for_function_timeouts: list[int] = []
        self.waited_after_success_ms: int | None = None

    def locator(self, selector: str) -> FakeLocator:
        assert selector == "[contenteditable='true'] img"
        return FakeLocator()

    def evaluate(self, *_args, **_kwargs) -> None:
        self.evaluate_calls += 1

    def wait_for_function(self, _script: str, *, arg: int, timeout: int) -> None:
        self.wait_for_function_timeouts.append(timeout)
        self.waited_for_image_count = arg
        self.wait_arg_was_keyword = True

    def wait_for_timeout(self, timeout: int) -> None:
        self.waited_after_success_ms = timeout


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
