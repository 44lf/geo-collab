# Windows TapTap Collector 运行手册

> 本工具仅用于已获得授权的 TapTap 数据采集。禁止用它轮换代理、伪装指纹或绕过
> `403/405/429`；Collector 遇到这些状态码会立即停止整轮任务。

Collector 独立仓库：
`https://hlgit.5518game.com/geo/geo-taptap-collector`。GEO 仓库只维护生产侧目标导出、
bundle 校验和幂等导入。

## 架构边界

```text
生产容器（只读导出 targets.json）
        │
        ▼
公司 Windows Collector（抓取 + 下载图片 + 生成完整性 bundle）
        │ 手动复制 / SCP；首版不建实时 API
        ▼
生产容器（全包校验 → dry-run → 逐游戏 upsert）
        │
        ├── MySQL games / game_tags
        └── MinIO stock_images
```

- Windows 不持有生产数据库或对象存储凭据。
- 生产不实时依赖 Windows；Collector 离线时继续使用已有游戏库和百度自动来源。
- 导入只合并，不因 miss/error/blocked 删除或停用游戏。

## 1. 从生产导出 10–20 个目标

在生产应用容器内：

```bash
python -m server.scripts.export_game_backfill_targets \
  /tmp/taptap-collector/targets.json \
  --limit 20 \
  --max-screenshots 3 \
  --delay-seconds 30
```

若要精确指定生产游戏行，可重复传 `--game-id`：

```bash
python -m server.scripts.export_game_backfill_targets \
  /tmp/taptap-collector/targets.json \
  --game-id 101 \
  --game-id 205
```

把输出的 `targets.json` 放到 Windows Collector 目录，并覆盖示例文件名
`targets.json`。不要手工把 TapTap ID 当作生产 `target_game_id`。

## 2. Windows 手动采集

先从 GitLab 获取 Collector：

```powershell
git clone ssh://git@hlgit.5518game.com:2222/geo/geo-taptap-collector.git
cd geo-taptap-collector
```

Collector 目录必须包含：

```text
collector.ps1
run-collector.cmd
targets.json
```

双击 `run-collector.cmd`。默认策略：

- 最多 20 个显式目标。
- 每游戏间隔取自 manifest，稳定性测试建议 30 秒。
- 每游戏独立落盘并更新 checkpoint。
- 重跑同一 `bundle_id` 时跳过已成功目标。
- 任意阶段遇到 `403/405/429` 立即停止，不继续请求后续游戏。
- 不在 bundle 中保存 Cookie 或 XSRF token。

输出位于：

```text
output/<bundle_id>/
  checkpoint.json
  manifest.json
  report.json
  games/<target_game_id>/...
```

## 3. 24–48 小时稳定性门禁

用同一份 10–20 游戏清单进行首轮、数小时后、次日复测。每次使用新的
`bundle_id`，避免断点续跑跳过已成功目标。

建议 GO 标准：

- 连续两天无 `403/405/429`。
- 元数据成功率超过 95%。
- 图片成功率超过 90%。
- 无运行一段时间后整批被封。
- 出口 IP 变化得到运维确认。
- 已取得组织要求的书面采集授权。

未达到任一门禁时，不配置 Windows Task Scheduler。

## 4. 传输与生产 dry-run

把完整 bundle 目录复制到生产应用容器可读路径。不要只复制 `manifest.json`。

先做不打开数据库写 session 的完整性验证：

```bash
python -m server.scripts.remote_game_bundle_import \
  /tmp/taptap-collector/<bundle_id> \
  --dry-run
```

dry-run 会验证：

- schema 和必填字段；
- 路径穿越、重复路径和重复目标；
- 文件数量、单文件和全包大小；
- PNG/JPEG/JSON 内容签名；
- 每个文件的字节数和 SHA-256；
- 计划写入的目标、图标和截图数量。

dry-run 失败时不得正式导入。

## 5. 正式导入与验证

避开后端发版窗口，在生产应用容器运行：

```bash
python -m server.scripts.remote_game_bundle_import \
  /tmp/taptap-collector/<bundle_id>
```

Importer 每游戏新建一个 DB session：

- 成功则 commit；
- 失败则只 rollback 当前游戏并继续；
- 图片字节通过 `pre_downloaded` / `pre_downloaded_icon` 交给现有 MinIO 入库；
- 同一 bundle 可安全重跑，源身份和图片 source URL hash 负责幂等。

导入后检查：

1. CLI 汇总的 `failed` 必须为 0。
2. 目标游戏的 `last_verified_at` 已更新。
3. `sources` 出现对应 TapTap `source_game_id`。
4. 图片在素材桶和 MinIO 中可读取。
5. 前端游戏库与 MCP 读取路径能看到合并后的字段。

## 6. 停止与回滚

本功能没有数据库迁移，也不修改生产 source registry。

遇到异常：

1. 停止 Windows Collector 或禁用未来的计划任务。
2. 停止导入后续 bundle。
3. 保留 bundle/report 供排查，不重复高频请求。
4. 生产继续使用已有镜像数据和百度自动来源。

Importer 不提供删除回滚，因为它只做幂等合并。若某条数据确需人工纠正，应通过现有
游戏库管理流程处理，不使用整包反向删除。
