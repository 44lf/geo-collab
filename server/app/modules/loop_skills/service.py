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


def build_bundle_from_file_map(raw: dict[str, bytes], *, version: str) -> SkillBundle:
    """给定 {posix_path: raw_bytes} 构造排序好的 bundle。上传路径与文件夹扫描共用同一 sha 算法。

    非 utf-8 抛 ValueError —— 上传路径必须在调用前自行校验 utf-8 并抛 ValidationError,
    此处只当内部不变式(种子模板必是文本)。
    """
    files: list[SkillFile] = []
    for rel in sorted(raw.keys()):  # 按 posix 字符串排序(跨 OS 确定)
        data = raw[rel]
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"loop_skills template not UTF-8: {rel}") from exc
        files.append(
            SkillFile(
                path=rel,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                content=content,
            )
        )

    h = hashlib.sha256()
    for f in files:
        h.update(f.path.encode("utf-8"))
        h.update(b"\x00")
        h.update(f.sha256.encode("ascii"))
        h.update(b"\x00")

    return SkillBundle(version=version, bundle_sha256=h.hexdigest(), files=files)


def build_bundle() -> SkillBundle:
    """扫描 templates/ 下所有文件,返回 bundle(种子/兜底路径)。"""
    raw: dict[str, bytes] = {}
    for path in _TEMPLATES_DIR.rglob("*"):
        if not path.is_file():
            continue
        raw[path.relative_to(_TEMPLATES_DIR).as_posix()] = path.read_bytes()
    return build_bundle_from_file_map(raw, version=LOOP_SKILL_BUNDLE_VERSION)


def build_zip(bundle: SkillBundle) -> bytes:
    """打包成 zip bytes。文件路径保持模板的目录结构（不带顶层前缀）。

    解压到 .claude/ 后直接是 README.md / commands/ / skills/。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in bundle.files:
            zf.writestr(f.path, f.content)
    return buf.getvalue()
