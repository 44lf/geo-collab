"""上传归一 + 校验：把「一个 zip」或「多文件数组」统一成 file map 并安全校验。

放宽自旧 versions_service：不再限定 README.md/commands/skills/ 顶层白名单、不再要求
/goal 专属必需文件，改为「含 ≥1 个 SKILL.md（任意层级）+ 总 ≤5MB」的通用规则。
"""

from __future__ import annotations

import io
import zipfile

from server.app.shared.errors import ValidationError

MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_ENTRY_BYTES = 5 * 1024 * 1024
MAX_ENTRIES = 200


def _safe_name(name: str) -> str:
    n = name.replace("\\", "/")
    if n.startswith("/") or ".." in n.split("/"):
        raise ValidationError(f"非法路径(zip-slip): {name}")
    return n


def parse_upload(entries: list[tuple[str, bytes]]) -> dict[str, bytes]:
    """entries=[(filename, bytes)]。单个 .zip → 解开；否则按文件数组原样收。"""
    raw: dict[str, bytes] = {}
    if len(entries) == 1 and entries[0][0].lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(entries[0][1]))
        except zipfile.BadZipFile as exc:
            raise ValidationError("不是合法的 zip 文件") from exc
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            raise ValidationError(f"文件过多: {len(infos)}(上限 {MAX_ENTRIES})")
        for info in infos:
            name = _safe_name(info.filename)
            if info.file_size > MAX_ENTRY_BYTES:
                raise ValidationError(f"单文件过大: {name}")
            if name in raw:
                raise ValidationError(f"重复路径: {name}")
            raw[name] = zf.read(info)
        return raw
    if len(entries) > MAX_ENTRIES:
        raise ValidationError(f"文件过多: {len(entries)}(上限 {MAX_ENTRIES})")
    for fname, data in entries:
        name = _safe_name(fname)
        if len(data) > MAX_ENTRY_BYTES:
            raise ValidationError(f"单文件过大: {name}")
        if name in raw:
            raise ValidationError(f"重复路径: {name}")
        raw[name] = data
    return raw


def validate_file_map(raw: dict[str, bytes]) -> None:
    if not raw:
        raise ValidationError("上传内容为空")
    total = sum(len(v) for v in raw.values())
    if total > MAX_TOTAL_BYTES:
        raise ValidationError(f"总大小 {total} 超过 {MAX_TOTAL_BYTES} 字节(5 MB)上限")
    has_skill_md = any(p.rsplit("/", 1)[-1] == "SKILL.md" for p in raw)
    if not has_skill_md:
        raise ValidationError("包内未找到任何 SKILL.md")
    for name, data in raw.items():
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"非 UTF-8 文本文件: {name}") from exc
