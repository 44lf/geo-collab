"""Plan B · 应用宝(sj.qq.com)榜单发现扩库腿。

独立采集层：网页内嵌 __NEXT_DATA__ JSON 解析（stdlib，无第三方依赖），产出新游戏经
`db_ingest.ingest_discovery` → `service.upsert_game`（category_id=None 自动建 companion 桶）。
与补全腿（九游按名巡检并入现有巡检循环）互补：扩库管"发现新游戏 + 全字段（含截图/精选评论）"。

模块边界：本包只负责扩库；补全腿的九游源在 `sources/ninegame.py`、跑在现有 scheduler。
"""
