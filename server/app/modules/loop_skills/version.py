"""手工维护的 bundle 版本号。

Task 7 起，「改模板必同步登记 sha」的 KNOWN_BUNDLE_SHAS 白名单纪律已退场：
skill 包内容改为存 DB（`models.py` 的 `Skill` / `SkillVersion`，见
`skill_service.py`），不再靠 CI 校验 build_bundle() 的 sha 是否在手工维护的
已知集合里——那套机制只服务于「模板文件即真相」的单 bundle 时代。

LOOP_SKILL_BUNDLE_VERSION 仍保留：`service.py` 的 `build_bundle()`（读
templates/ 目录）仍用它当种子内容的版本标签，供 Task 8 seed 官方 skill
（slug="goal"）首个版本时使用。
"""

LOOP_SKILL_BUNDLE_VERSION = "2026-07-07-v12"
