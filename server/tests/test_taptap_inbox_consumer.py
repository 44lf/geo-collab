from __future__ import annotations

import hashlib
import stat
import zipfile

import pytest

from server.scripts import consume_taptap_inbox_spike as consumer


def test_safe_extract_accepts_regular_bundle(tmp_path):
    archive = tmp_path / "bundle.zip"
    destination = tmp_path / "extracted"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr("games/42/game.json", "{}")

    consumer._safe_extract(archive, destination)

    assert (destination / "manifest.json").read_text() == "{}"
    assert (destination / "games" / "42" / "game.json").read_text() == "{}"


def test_safe_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bundle.zip"
    destination = tmp_path / "extracted"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escaped.json", "{}")

    with pytest.raises(RuntimeError, match="unsafe archive member"):
        consumer._safe_extract(archive, destination)

    assert not (tmp_path / "escaped.json").exists()


def test_safe_extract_rejects_symlink(tmp_path):
    archive = tmp_path / "bundle.zip"
    destination = tmp_path / "extracted"
    destination.mkdir()
    link = zipfile.ZipInfo("games/42/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(link, "../../outside")

    with pytest.raises(RuntimeError, match="symlink is forbidden"):
        consumer._safe_extract(archive, destination)


def test_sha256_streams_file_contents(tmp_path):
    payload = b"geo-taptap-channel" * 1024
    path = tmp_path / "bundle.zip"
    path.write_bytes(payload)

    assert consumer._sha256(path) == hashlib.sha256(payload).hexdigest()
