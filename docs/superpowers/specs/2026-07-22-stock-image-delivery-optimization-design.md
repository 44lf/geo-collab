# 图片交付优化：HTTP 缓存头 + 按需缩略图

> 设计稿 · 2026-07-22 · 作者 Claude Code + lufeng

## 背景 / 问题

游戏库前台加载慢，根因是带宽——不是数据库、不是 Redis 能救的东西。追字节路径确认：

浏览器 `<img src="/api/stock-images/{id}/file">`
→ nginx `location /api/`（`proxy_cache off`，不加任何缓存头）
→ FastAPI `serve_image_file`（`server/app/modules/image_library/router.py:527`）：
`db.get` ×2 → `minio_store.get_object_bytes()` 把**整张原图**读进内存 → `Response(content=data)`，**无 `Cache-Control` / `ETag` / `Last-Modified`**。

两处浪费：

1. **零 HTTP 缓存** —— 对比 `/assets/`（`expires 1y; immutable`），图片端点什么缓存头都没有，浏览器每次切 tab / 翻页 / 回看都重新全量下载，连 304 都不可能（没 ETag）。
2. **拿原图渲染缩略图** —— 网格卡片才百来 px，却下载全分辨率原图（一张 2–4MB 的截图为了画个小卡）。

该端点是图片库与游戏库**共用**的唯一图片字节出口（`GameMaterialPanel.tsx:142` 与 image-library 网格都走它），在这一处修即同时惠及两者。

## 目标 / 非目标

**目标**

- 重复访问不再重新下载图片（HTTP 缓存头）。
- 网格只下载小图，原图只在灯箱/详情加载（按需缩略图）。

**非目标（明确不做）**

- 不改存量原图、不改 MinIO 里的字节。
- 不加/改数据库列、不写迁移、不回填。
- 不碰图片上传流程、不碰游戏库 ingest。
- 不引入 Redis / 服务端缩略图持久缓存（本轮刻意按需现算，见「方案选择」）。
- 本轮不改 image-library 网格前端（后端改动天然惠及它，前端 `?w=` 接入列为可选跟进）。

## 方案选择

缩略图三种形态，选**按需生成 · 不落服务端缓存**（用户拍板）：

| 方案 | 省什么 | 代价 | 结论 |
|---|---|---|---|
| **A. 按需现算，不落服务端缓存**（选中）| 首屏带宽 + 重复下载 | 每次冷请求多几毫秒 CPU 缩放 | 零迁移、对存量 679 游戏立即生效、部件最少 |
| B. 按需 + 落持久缓存（MinIO 派生 key / 文件）| 同上，且省重复 CPU | 要管缓存存储/失效 | YAGNI，内部工具流量不值 |
| C. 上传时预生成 + 回填 | 服务端零缩放 | 迁移/回填脚本 + 额外存储 + 更多部件 | 过重 |

A 靠「① 的 HTTP 缓存头」把重复请求挡在浏览器/CDN 侧，冷缓存 miss 时才现算，对内部工具的中等流量完全够用。

## 详细设计

只碰 **1 个后端端点 + 前端 1 行 `<img>` + 测试**。

### ① `serve_image_file` 加 HTTP 缓存头 —— 纯传输层

`server/app/modules/image_library/router.py:527`。

- 给函数签名加 `request: Request`（当前无）。
- **ETag（强校验）**：源用不可变的 `minio_key`（每张图唯一、字节永不变——改图=新行新 key，`PATCH` 只改 tags/描述不动字节）。ETag 编码**实际返回的字节**：
  - 返回原图 → `ETag: "<minio_key>"`
  - 返回缩略图 → `ETag: "<minio_key>.w<width>"`
- **`Cache-Control: public, max-age=31536000, immutable`**（1 年）。给定 `image_id` 的字节不可变，`immutable` 安全。用模块常量 `STOCK_IMAGE_CACHE_MAX_AGE = 31536000`，不新增 env（YAGNI）。
- **`If-None-Match` → 304**：请求头带的 ETag 与本次将返回的 ETag 相等，则返回 `Response(status_code=304)`（空体，仍带 ETag + Cache-Control），连字节都不发。
- nginx 的 `proxy_cache off` **不动**——浏览器缓存靠响应头即可；未来上 CDN 时这些头也是前提。

### ② 同端点加 `?w=` 按需缩略图

- 新增可选 query `w: int | None`。
- **白名单** `ALLOWED_THUMB_WIDTHS = {320, 480, 640, 960}`；`w` 非空且不在白名单 → **400**（端点公开无鉴权，不做白名单会被任意大 `w` 打爆内存 / 制造无界缓存 key）。`w` 为空 → 走原图路径（现行为 + ①的头）。
- 有合法 `w` 时：
  1. `minio_store.get_object_bytes()` 取原图字节（与现行同）。
  2. `PIL.Image.open(BytesIO(data))`，`ImageOps.exif_transpose` 摆正方向。
  3. **不放大**：若 `w >= im.width`（用**打开后的实际宽**判断，不依赖可能为 null 的 `StockImage.width` 列）→ 直接返回原图字节 + 原图 ETag（`"<minio_key>"`）+ 原 content-type。
  4. 否则保宽比缩到目标宽（`Image.LANCZOS`），`convert("RGB")`（游戏截图不透明，丢 alpha 可接受、更小），`save(buf, "WEBP", quality=80)`。
  5. 返回 `Response(content=webp_bytes, media_type="image/webp")` + `ETag: "<minio_key>.w<width>"` + Cache-Control。
- **优雅降级**：`open`/`resize` 抛异常（图损坏、动图等 Pillow 打不开）→ 回原图字节 + 原图 ETag，**不抛 500**。
- **并发**：`serve_image_file` 保持 `def`（同步），FastAPI 自动丢线程池，Pillow 阻塞不冻事件循环。复用 `server/app/modules/tasks/drivers/image_upload.py:24` 的 PIL 模式。

### ③ 前端：只改游戏库网格

`web/src/features/game-library/GameMaterialPanel.tsx`

- 网格缩略图（`:142`）：`<img src={img.url}>` → `<img src={\`${img.url}?w=640\`}>`（`img.url` 无 query，拼接安全）。
- **灯箱（`:201`）保持 `img.url`（全图）**。
- 640 兼顾放大后的素材卡（480–560px）清晰度，仍比 2–4MB 原图小 ~30×。

## 数据流

```
网格卡片   GET /file?w=640  → [冷] 取原图→Pillow缩→WebP→200 image/webp + ETag + max-age=1y
                            → [浏览器已缓存] 不发请求（immutable）
                            → [带 If-None-Match] 304 空体
灯箱       GET /file        → 原图字节 + ETag + max-age=1y（同样可被缓存/304）
w >= 原宽                    → 回原图（不放大、不重编码）
w 非白名单                   → 400
图损坏 + ?w                  → 回原图（降级，不 500）
```

## 错误处理

| 场景 | 行为 |
|---|---|
| 图片 id 不存在 | 404（现行不变）|
| 栏目不存在 | 404（现行不变）|
| MinIO 读失败 | 502（现行不变）|
| `w` 非白名单 | 400，`detail` 说明允许值 |
| Pillow 打不开/缩放失败 | 回原图字节，200，不 500 |
| `w >= 原图宽` | 回原图字节，200，不放大 |

## 测试（pytest，需 MySQL）

夹具对齐现有：monkeypatch `server.app.modules.image_library.router.minio_store.get_object_bytes` 返回**真 Pillow 生成的**小图字节（让缩放路径真的跑），`upload_image` 等按 `test_image_library_folder_ops.py:_patch_minio` 模式 stub；anon 客户端用 `TestClient(test_app.client.app)`（见 `test_public_endpoints.py`）。新建测试文件 `server/tests/test_stock_image_delivery.py`：

1. 全图响应带 `Cache-Control: public, max-age=31536000, immutable` + `ETag`。
2. 第二次带 `If-None-Match: <上次 ETag>` → **304**，空体。
3. `?w=640` → **200**、`Content-Type: image/webp`、Pillow 解回宽 ≤ 640、字节数 < 原图。
4. `?w=99999`（非白名单）→ **400**。
5. `?w=` 大于等于原图宽（如原图 400 宽请求 `?w=640`）→ **200**、回原图（非 webp）、ETag 为 `"<key>"`。
6. 损坏字节 + `?w=640` → **200** 降级回原字节，不 500。
7. 回归：`?w=640` 与无 `w` 的 ETag 不同（尺寸不串味）。

前端无单测框架，靠 `pnpm --filter @geo/web typecheck` + `build` 门禁。

## 影响面 / 回滚

- 后端仅 `image_library/router.py` 一处；无 schema / 迁移 / 存量数据变更。回滚=还原该文件 + 前端一行。
- 图片库网格未改，但因共用端点，其重复访问也自动少下载（无负面）。
- CI 硬门禁：`backend-lint`（ruff/mypy）+ `frontend`（typecheck/build）；`backend-test` 当前禁用，故新测试本地跑（`GEO_TEST_DATABASE_URL`）自证。

## 拍板的默认值（可调）

网格宽 **640** · 白名单 **{320,480,640,960}** · WebP **q80** · max-age **1 年** · 范围只碰游戏库网格 + 共享端点。
