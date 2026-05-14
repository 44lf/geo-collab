# Geo 架构可靠性收口计划

> 当前目标：内部 5 人使用的自动化发文平台，采用单 worker + DB 队列，不追求水平高并发。

## 已落地决策

- 发布 worker 只运行 1 个实例；同一账号发布优先保证串行和稳定，不做多 worker 扩容。
- 任务取消改为协作式取消：停止后续未开始的发布，已进入平台页面的 running record 允许走到下一个安全结束点。
- 任务控制状态落 DB：`publish_tasks.cancel_requested`、`worker_heartbeat_at`；worker 在线状态落 `worker_heartbeats`。
- noVNC 不直接公网暴露：Compose 只绑定宿主机 `127.0.0.1:6080-6090`，远程访问走 VPN/SSH 隧道。
- 平台抽象收口：driver 层提供通用 `PublishResult` / `PublishError` / `UserInputRequired`，头条保留兼容别名。
- browser session 复用 key 包含 `platform_code + account_key`，避免未来多平台账号目录名碰撞。

## 关键变更

- 后端任务执行：worker 续租并写心跳，取消请求由 DB flag 驱动，`_finish_record_future` 不再覆盖已非 running 的 record。
- 系统状态：`/api/system/status` 增加 worker、待执行任务、远程浏览器会话、noVNC runtime 状态。
- 账号平台：新增 `/api/accounts/platforms`，前端从接口读取当前支持平台，短期仍默认头条。
- 运维配置：`.env.example`、`README.md`、`docs/deploy.md` 与当前 Docker 部署模型一致。

## 验收重点

- `POST /api/tasks/{id}/cancel` 对 running 任务返回“已请求取消”，不会把 running record 强制改成 cancelled。
- running record 完成后，worker 不再调度新的 pending record，任务聚合为 cancelled。
- noVNC 端口不会在宿主机公网地址监听。
- 系统状态页能看到 worker 是否在线和活跃远程浏览器会话数。

## 后续可选

- 如果确实需要多 worker，再引入 DB 级 account lock 或独立队列系统。
- 给 noVNC 增加 Nginx 鉴权反代和短期访问 token，替代 SSH/VPN 访问方式。
- 接入第二个平台时，优先验证 `PlatformDriver` 抽象是否足够。
