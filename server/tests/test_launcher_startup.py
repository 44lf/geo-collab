"""Tests for launcher.py startup stability fixes."""

import logging

import launcher


class TestTokenPersistence:
    def test_token_persists_across_restarts(self, tmp_path):
        """Same data_dir used twice returns the same token."""
        data_dir = tmp_path / "geo_data"
        data_dir.mkdir(parents=True, exist_ok=True)

        token1 = launcher._read_or_generate_token(data_dir)
        token2 = launcher._read_or_generate_token(data_dir)

        assert token1 == token2
        assert len(token1) == 64  # 32 bytes hex

        token_file = data_dir / "local_token.txt"
        assert token_file.exists()
        assert token_file.read_text(encoding="utf-8").strip() == token1

    def test_token_file_content_is_valid_hex(self, tmp_path):
        """Generated token is 64 hex characters."""
        data_dir = tmp_path / "geo_data"
        data_dir.mkdir(parents=True, exist_ok=True)

        token = launcher._read_or_generate_token(data_dir)
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_token_not_logged(self, tmp_path, caplog):
        """Token value does not appear in log output."""
        data_dir = tmp_path / "geo_data"
        data_dir.mkdir(parents=True, exist_ok=True)

        with caplog.at_level(logging.INFO):
            token = launcher._read_or_generate_token(data_dir)

        for record in caplog.records:
            assert token not in record.getMessage(), (
                f"Token leaked in log: {record.getMessage()}"
            )


class TestChromeMissing:
    def test_show_error_raises_with_chinese_message(self, monkeypatch):
        """_show_chrome_missing_error raises RuntimeError in Chinese (no GUI in tests)."""
        import builtins
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("tkinter", "tkinter.messagebox"):
                raise ImportError(f"Mocked: no {name}")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        raised = False
        try:
            launcher._show_chrome_missing_error()
        except RuntimeError as e:
            raised = True
            msg = str(e)
            assert "Chrome" in msg
            assert "安装" in msg
        assert raised, "Expected RuntimeError was not raised"

    def test_ensure_chromium_calls_error_when_chrome_missing(self, monkeypatch):
        """_ensure_chromium triggers error function when Chrome is absent."""
        monkeypatch.setattr(launcher, "_check_chrome", lambda: False)
        calls = []

        def fake_error():
            calls.append(1)

        monkeypatch.setattr(launcher, "_show_chrome_missing_error", fake_error)
        launcher._ensure_chromium()
        assert len(calls) == 1

    def test_ensure_chromium_no_error_when_chrome_present(self, monkeypatch):
        """_ensure_chromium does nothing when Chrome is found."""
        monkeypatch.setattr(launcher, "_check_chrome", lambda: True)

        def should_not_call():
            raise AssertionError("_show_chrome_missing_error should not be called")

        monkeypatch.setattr(launcher, "_show_chrome_missing_error", should_not_call)
        launcher._ensure_chromium()


class TestUvicornWindowedExe:
    def test_uvicorn_run_disables_default_log_config_when_stdout_none(self, monkeypatch):
        """_setup_logging does not crash when sys.stdout is None (PyInstaller console=False)."""
        import sys as _sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            log_file = Path(td) / "test.log"
            saved = _sys.stdout
            _sys.stdout = None
            try:
                launcher._setup_logging(log_file)
            finally:
                _sys.stdout = saved
