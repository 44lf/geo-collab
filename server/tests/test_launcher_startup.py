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
