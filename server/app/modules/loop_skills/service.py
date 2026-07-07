"""loop_skills.service —— 服务端「正本」模板的扫描 + 打包逻辑。

无 IO 副作用、无 DB 访问；纯文件读 + 内存 zip。Web 端 + MCP 端共用。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from server.app.modules.loop_skills.version import LOOP_SKILL_BUNDLE_VERSION

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class SkillFile:
    """单个模板文件的元信息 + 内容。"""

    path: str  # 相对 templates/ 的 posix 路径，如 "skills/geo-article-writer/SKILL.md"
    size: int  # bytes
    sha256: str  # hex digest
    content: str  # utf-8 文本


@dataclass(frozen=True)
class SkillBundle:
    version: str
    bundle_sha256: str
    files: list[SkillFile]


def build_bundle() -> SkillBundle:
    """扫描 templates/ 下所有文件，返回排好序的 bundle。

    遇到非 utf-8 文件直接抛 ValueError —— 模板就该是文本，加二进制是 bug。
    """
    # 先收 (posix_rel, path) 再**按 posix 字符串排序**——不要 sorted([Path])。
    # Path 对象比较在 Windows 大小写不敏感（"commands" 排在 "README" 前）、Linux 大小写敏感
    # （"README" 在 "commands" 前），同一份模板在两个 OS 上文件序不同 → bundle_sha 不同，
    # 叠加 CRLF/LF 就是 4 个 sha 的跨 OS 噩梦（历史 v7~v11 反复栽）。按 posix 串排序后
    # 排序跨 OS 确定，只剩行尾一个维度（LF=CI / CRLF=Windows 本地），bundle_sha 收敛到 2 个。
    entries: list[tuple[str, Path]] = []
    for path in _TEMPLATES_DIR.rglob("*"):
        if not path.is_file():
            continue
        entries.append((path.relative_to(_TEMPLATES_DIR).as_posix(), path))

    files: list[SkillFile] = []
    for rel, path in sorted(entries, key=lambda e: e[0]):
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"loop_skills template not UTF-8: {rel}") from exc
        files.append(
            SkillFile(
                path=rel,
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                content=content,
            )
        )

    # bundle 级 sha256: 对 (path, file_sha) 排序后串接再 hash
    h = hashlib.sha256()
    for f in files:
        h.update(f.path.encode("utf-8"))
        h.update(b"\x00")
        h.update(f.sha256.encode("ascii"))
        h.update(b"\x00")
    bundle_sha = h.hexdigest()

    return SkillBundle(
        version=LOOP_SKILL_BUNDLE_VERSION,
        bundle_sha256=bundle_sha,
        files=files,
    )


def build_zip(bundle: SkillBundle) -> bytes:
    """打包成 zip bytes。文件路径保持模板的目录结构（不带顶层前缀）。

    解压到 .claude/ 后直接是 README.md / commands/ / skills/。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in bundle.files:
            zf.writestr(f.path, f.content)
    return buf.getvalue()
