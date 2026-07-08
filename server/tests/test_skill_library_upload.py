import io
import zipfile

import pytest

from server.app.shared.errors import ValidationError


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in files.items():
            zf.writestr(k, v)
    return buf.getvalue()


def test_parse_zip_entry():
    from server.app.modules.loop_skills.upload import parse_upload

    z = _zip({"skills/foo/SKILL.md": b"hello"})
    raw = parse_upload([("bundle.zip", z)])
    assert raw == {"skills/foo/SKILL.md": b"hello"}


def test_parse_file_array():
    from server.app.modules.loop_skills.upload import parse_upload

    raw = parse_upload([("SKILL.md", b"hi"), ("refs/a.md", b"x")])
    assert set(raw) == {"SKILL.md", "refs/a.md"}


def test_validate_requires_skill_md():
    from server.app.modules.loop_skills.upload import validate_file_map

    with pytest.raises(ValidationError):
        validate_file_map({"README.md": b"x"})


def test_validate_rejects_oversize():
    from server.app.modules.loop_skills.upload import validate_file_map

    with pytest.raises(ValidationError):
        validate_file_map({"SKILL.md": b"a" * (5 * 1024 * 1024 + 1)})


def test_parse_rejects_zip_slip():
    from server.app.modules.loop_skills.upload import parse_upload

    z = _zip({"../evil.md": b"x"})
    with pytest.raises(ValidationError):
        parse_upload([("bundle.zip", z)])
