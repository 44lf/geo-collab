"""幂等发布官方 /goal Skill 包（slug=goal）的新版本。

服务层直调 `add_version`（按 skill_id 追加版本 + 切 current），避开两个坑：
- 现成端点 `POST /api/mcp/skills/{id}/versions` 每次都追加新版本、不做 SHA 去重——
  重跑本脚本会灌一堆同内容版本；这里先比对 `templates/` 现扫描出的 bundle_sha256
  与 DB 里 skill(slug=goal) 的 current bundle_sha256，相同即跳过。
- Web 端「选文件夹上传」会把根目录名塞进相对路径（装错目录）；这里走
  `build_bundle()` 直接扫 `templates/`，路径天然正确。

用法（改完 `server/app/modules/loop_skills/templates/skills/*/SKILL.md` 后跑一次，
让 DB current bundle 真的驱动 `/goal`）：
    python -m server.scripts.publish_goal_skill

不做：不建 skill（首发走 `server/scripts/seed_skill_library.py`）、不touch 本机
`~/.claude/skills`（装到本机是 `install_loop_skills` MCP tool 或前端下载 zip 各自的事，
本脚本只管 DB 侧的 current bundle）。
"""

from __future__ import annotations

from server.app.modules.loop_skills.service import build_bundle
from server.app.modules.loop_skills.skill_service import (
    _active_skill_by_slug,
    add_version,
    get_current_bundle,
)

_SLUG = "goal"


def main() -> None:
    # 惰性导入：db.session 在 import 期就建引擎、需要 GEO_DATABASE_URL 就绪，
    # 放到 main() 里避免被其它脚本 / 测试收集期意外 import 本模块时炸掉（同
    # encrypt_secrets.py / repair_article_escaped_quotes.py 的做法）。
    from server.app.db.session import SessionLocal

    db = SessionLocal()
    try:
        new = build_bundle()
        try:
            current = get_current_bundle(db, _SLUG)
        except Exception:
            current = None  # 无 current（首发）或 skill 不存在 → 继续发布，让下面报出真实错误

        if current is not None and current.bundle_sha256 == new.bundle_sha256:
            print(f"goal bundle unchanged (sha={new.bundle_sha256}), skip")
            return

        skill = _active_skill_by_slug(db, _SLUG)
        entries = [(f.path, f.content.encode("utf-8")) for f in new.files]
        _, version = add_version(
            db, skill_id=skill.id, entries=entries, uploaded_by=None, is_admin=True
        )
        db.commit()
        print(f"published goal v={version.version_label} sha={version.bundle_sha256}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
