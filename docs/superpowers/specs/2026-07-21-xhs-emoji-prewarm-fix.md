# 小红书样式库：Emoji 字体修复 + 版本化预热 设计

> 日期：2026-07-21　状态：待实现　类型：bug 修复 + 体验优化

## 问题

1. **Emoji 渲染成豆腐块（生产）**。根因（已用系统化调试确认）：生产 `geo-collab-base` 镜像只装 `fonts-noto-cjk`（只含中文字形、**无 emoji 字形**），从没装 color emoji 字体。本地能出彩色 emoji 纯属巧合——`playwright install-deps chromium` 顺带装了 `fonts-noto-color-emoji`，而生产 base 用 `playwright install chromium`（不带 `--with-deps`）没这字体。→ 生产 emoji 无字体可回退 → chromium 画豆腐块。铁证：本地（有 `NotoColorEmoji.ttf`）重渲封面 🍜 彩色正常；生产截图豆腐块。

2. **懒加载体验差**：首次进样式库要手动点「重新生成预览」+ 等 ~30s。且修 emoji 后旧缓存（豆腐块）非空 → 不会自动刷新。

## 方案（用户已选 C：版本化预热）

### ① Emoji 字体（治本，源头）
- `Dockerfile.base` 与 `Dockerfile.app` 的 apt install 加 `fonts-noto-color-emoji`。
- base 变更 → 走 needsBase 发版（bump `deploy/VERSION` BASE 1.0.2→1.0.3 → `base-1.0.3` → release）。
- 验证：本地已证明「有 NotoColorEmoji → emoji 彩色渲染」；修复=让生产也有该字体。

### ② 版本化缓存 + 启动预热（解决懒加载 + emoji 旧缓存失效）
- `previews.py` 加 `PREVIEW_VERSION = "v2"`；缓存 key 变 `theme-previews/{PREVIEW_VERSION}/{theme}/{cover,card}.png`。渲染输出变化（改示例 / 修 emoji / 改主题）就 bump 版本 → 旧缓存被绕过、自动重渲。本次 bump 到 `v2`（旧无版本路径 = 隐式 v1，其豆腐块图被弃用）。
- 加 `ensure_prewarmed()`：若当前版本缓存不全 → `spawn_regenerate()`（后台、单飞锁）。
- `main.py` 启动时调 `previews.ensure_prewarmed()`（后台线程，不阻塞启动）。→ 部署后自动渲当前版本的全新预览，样式库常态秒开、永不用手点。
- 暴露「生成中」态（复用现有 `is_generating()`，消灭死代码）：`GET /themes` 返回 `{"themes":[...], "generating": bool}`。前端据此：generating→显示"生成中"+轮询；否则空→"点击生成"兜底；有缓存→显示图。

### 前端
- `xhsThemes.ts`：`listXhsThemes()` 返回 `{themes, generating}`。
- `XhsStyleGallery.tsx`：generating 时显示进度态并轮询（复用现有 2s 轮询）；保留手动「重新生成」按钮作兜底。

## 测试
- `test_xhs_previews.py`：`preview_keys` 断言含 `v2`；`ensure_prewarmed`（monkeypatch object_exists/spawn 验证不全→spawn、全齐→不 spawn）；`GET /themes` 新形状（`data.themes` / `data.generating`）；其余既有测试更新到新 key/形状。
- 本地容器真渲验证 emoji（本地有字体）+ 新 key 路径。
- 前端 typecheck + build。

## 部署（按新纪律：先合 main 再发版）
needsBase：Dockerfile.base + VERSION 改动 → MR → 用户合 main → `base-1.0.3`（等绿）→ `release-<appVer>`（前后端都动）。发版前查主干冲突。

## 非目标
- 不做每主题独立示例 / 不做主题 CRUD / 不改渲染引擎本身（只加字体）。
- emoji 彩色 vs 单色：装 `fonts-noto-color-emoji` 即可（本地此 chromium 版本已证彩色 OK）。
