# Geo Collab 云端部署指南

## 前置条件
- Docker 20.10+ 和 Docker Compose v2
- 至少 4GB 可用内存

## 快速启动

1. 复制环境变量模板：
   cp .env.example .env

2. 编辑 `.env`，必填项：
   - MYSQL_ROOT_PASSWORD — 设一个强密码
   - GEO_JWT_SECRET — 设 32 位以上的随机字符串（可用 `openssl rand -hex 32`）
   - GEO_SEED_USERS — JSON 格式初始用户，示例见 .env.example

3. 启动：
   docker-compose up -d

4. 运行数据库迁移：
   docker-compose exec app alembic upgrade head

5. 创建初始用户：
   docker-compose exec app python -m server.scripts.seed_users

6. 打开浏览器访问 http://服务器IP:8000

## 环境变量清单
| 变量 | 必需 | 说明 |
|------|------|------|
| MYSQL_ROOT_PASSWORD | 是 | MySQL root 密码 |
| MYSQL_DATABASE | 是 | 数据库名 |
| MYSQL_USER | 是 | 数据库用户 |
| MYSQL_PASSWORD | 是 | 数据库密码 |
| GEO_JWT_SECRET | 是 | JWT 签名密钥 |
| GEO_SEED_USERS | 是 | 初始用户 JSON |
| GEO_DATABASE_URL | 否 | 完整 DSN（自动拼接） |
| GEO_PUBLISH_REMOTE_BROWSER_ENABLED | 是 | 设为 true |
| GEO_PUBLISH_NOVNC_WEB_DIR | 是 | noVNC 目录 |
| GEO_PUBLISH_REMOTE_BROWSER_HOST | 否 | 默认 0.0.0.0 |

## 首次部署流程
1. env 配置 → docker-compose up -d → 健康检查
2. alembic upgrade head → seed_users → 登录验证

## 注意事项
- 每个浏览器实例约 300-500MB 内存
- 人工介入会话 5 分钟无操作自动清理
- MySQL 数据挂载在 docker volume，备份请导出 dump
