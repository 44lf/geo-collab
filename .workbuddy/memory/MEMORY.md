# Geo 项目长期记忆

## 测试 / 运行环境（重要）
- 本机 `127.0.0.1:3306` **无** MySQL。测试库在 LAN：`mysql+pymysql://root:123456!@172.25.20.57:3307/geo_test`
  跑测试需设 `GEO_TEST_DATABASE_URL` 指向它；连不上时 `@pytest.mark.mysql` 用例会自动 skip（预期，非失败）。
- Python 用 conda 环境 `geo_xzpt`：`C:/Users/Administrator/miniconda3/envs/geo_xzpt/python.exe`。
- Bash 工具是 Git Bash（Windows），路径用 `/e/geo` 或 `E:/geo`。
- 前端：`pnpm --filter @geo/web typecheck|build`；无单测框架，typecheck+build 即前端门禁。

## 当前进行中的大功能
- **loop skill 包版本化**（`feat/loop-skill-bundle-versioning` 分支）：Tasks 1-9 已全部完成并提交（截至 2026-07-07，最新 `41b11a8`）。设计 spec：`docs/superpowers/plans/2026-07-07-loop-skill-bundle-versioning.md`。

## 用户习惯
- 对"删除 / 破坏性改动"高度敏感，涉及前务必先明示范围与理由。
