"""
系统托盘功能测试，顺带演示三种常用测试套路：
  1. 直接调用函数，断言返回值
  2. 用 monkeypatch 替换外部依赖（webbrowser、pystray）
  3. 用 MagicMock 记录"某个方法有没有被调用、被用什么参数调用"
"""

from unittest.mock import MagicMock, patch

import launcher


# ── 套路 1：直接调用，断言返回值 ──────────────────────────────────────────────
class TestMakeTrayImage:
    def test_returns_rgba_image(self):
        """_make_tray_image 应该返回一个 64×64 的 RGBA 图片对象。"""
        img = launcher._make_tray_image()

        assert img.size == (64, 64)
        assert img.mode == "RGBA"

    def test_icon_is_not_blank(self):
        """图标不能是全透明的空白图（说明绘制代码确实执行了）。"""
        img = launcher._make_tray_image()

        # 用 getpixel 逐像素检查，避免使用已废弃的 getdata()
        has_visible_pixel = any(
            img.getpixel((x, y))[3] > 0        # alpha 通道 > 0 表示有内容
            for x in range(img.width)
            for y in range(img.height)
        )
        assert has_visible_pixel, "图标是全透明的，说明绘制失败"


# ── 套路 2：monkeypatch 替换外部依赖 ──────────────────────────────────────────
class TestTrayMenuOpen:
    def test_open_menu_item_calls_webbrowser(self, monkeypatch):
        """点「打开界面」菜单项时，应该调用 webbrowser.open(url)。"""
        opened_urls = []
        monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened_urls.append(url))

        # fake_pystray 本身是 MagicMock，所以 fake_pystray.Menu 也是 MagicMock，
        # 调用它时会自动记录参数（call_args），无需额外设置。
        fake_pystray = MagicMock()
        fake_pystray.Menu.SEPARATOR = None
        # MenuItem 返回一个 dict，方便后面检查 label 和 callback
        fake_pystray.MenuItem = lambda label, callback, **kw: {"label": label, "callback": callback}

        monkeypatch.setattr(launcher, "_make_tray_image", lambda: MagicMock())

        with patch.dict("sys.modules", {"pystray": fake_pystray}):
            launcher._run_tray("http://127.0.0.1:8765", MagicMock())

        # call_args[0] 是 Menu(...) 被调用时的位置参数元组，即所有菜单项
        menu_items = fake_pystray.Menu.call_args[0]
        open_item = next(
            item for item in menu_items
            if isinstance(item, dict) and item.get("label") == "打开界面"
        )
        open_item["callback"](None, None)       # 模拟用户点击

        assert opened_urls == ["http://127.0.0.1:8765"]


# ── 套路 3：MagicMock 记录调用，断言副作用 ────────────────────────────────────
class TestTrayMenuQuit:
    def test_quit_menu_item_sets_server_exit_and_stops_icon(self, monkeypatch):
        """点「退出 GeoCollab」时，应该设置 server.should_exit=True 并调用 icon.stop()。"""
        fake_pystray = MagicMock()
        fake_pystray.Menu.SEPARATOR = None
        fake_pystray.MenuItem = lambda label, callback, **kw: {"label": label, "callback": callback}

        captured_icon = {}

        def fake_icon_class(name, img, title, menu):
            mock_icon = MagicMock()

            def fake_run():
                # 拿到传给 Menu() 的菜单项，找到「退出」并触发它的 callback
                menu_items = fake_pystray.Menu.call_args[0]
                quit_item = next(
                    item for item in menu_items
                    if isinstance(item, dict) and "退出" in item.get("label", "")
                )
                quit_item["callback"](mock_icon, None)

            mock_icon.run = fake_run
            captured_icon["icon"] = mock_icon
            return mock_icon

        fake_pystray.Icon = fake_icon_class

        monkeypatch.setattr(launcher, "_make_tray_image", lambda: MagicMock())

        fake_server = MagicMock()
        fake_server.should_exit = False

        with patch.dict("sys.modules", {"pystray": fake_pystray}):
            launcher._run_tray("http://127.0.0.1:8765", fake_server)

        assert fake_server.should_exit is True, "退出时应设置 server.should_exit = True"
        captured_icon["icon"].stop.assert_called_once()     # icon.stop() 被调用了一次
