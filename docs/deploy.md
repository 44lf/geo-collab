# Geo Collab 云端部署指南

## 前置条件

- Linux 服务器（Ubuntu 20.04+ 已验证）
- Python 3.10+ + conda 环境 `geo_xzpt`
- Google Chrome 或 Chromium
- Xvfb、x11vnc、websockify、noVNC

## 环境变量清单

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `GEO_DATA_DIR` | 否 | `%LOCALAPPDATA%/GeoCollab` | 数据目录 |
| `GEO_PUBLISH_REMOTE_BROWSER_ENABLED` | 是 | `false` | 启用远程浏览器（必须设为 true） |
| `GEO_PUBLISH_XVFB_PATH` | 是 | `Xvfb` | Xvfb 可执行文件路径 |
| `GEO_PUBLISH_X11VNC_PATH` | 是 | `x11vnc` | x11vnc 可执行文件路径 |
| `GEO_PUBLISH_WEBSOCKIFY_PATH` | 是 | `websockify` | websockify 可执行文件路径 |
| `GEO_PUBLISH_NOVNC_WEB_DIR` | 是 | 无 | noVNC 静态文件目录（如 `/opt/noVNC`） |
| `GEO_PUBLISH_REMOTE_BROWSER_HOST` | 否 | `127.0.0.1` | 对外暴露的 host（需改为服务器 IP） |
| `GEO_PUBLISH_REMOTE_BROWSER_DISPLAY_BASE` | 否 | `99` | X display 起始编号 |
| `GEO_PUBLISH_REMOTE_BROWSER_VNC_BASE_PORT` | 否 | `5900` | VNC 端口起始值 |
| `GEO_PUBLISH_REMOTE_BROWSER_NOVNC_BASE_PORT` | 否 | `6080` | noVNC 端口起始值 |
| `GEO_PUBLISH_REMOTE_BROWSER_START_TIMEOUT` | 否 | `15` | Xvfb/VNC/noVNC 启动超时（秒） |
| `GEO_LOCAL_API_TOKEN` | 否 | 自动生成 | 本地 API 鉴权 token |

## 启动步骤

```powershell
# 1. 安装系统依赖
sudo apt install -y xvfb x11vnc novnc python3-pip

# 2. 安装 websockify
pip install websockify

# 3. 设置环境变量
$env:GEO_PUBLISH_REMOTE_BROWSER_ENABLED = "true"
$env:GEO_PUBLISH_NOVNC_WEB_DIR = "/usr/share/novnc"
$env:GEO_PUBLISH_REMOTE_BROWSER_HOST = "0.0.0.0"

# 4. 运行数据库迁移
alembic upgrade head

# 5. 启动服务
uvicorn server.app.main:app --host 0.0.0.0 --port 8000
```

## 架构说明

```
用户浏览器 ──http──→ noVNC (websockify:6080)
                        └──websocket──→ x11vnc:5900
                                           └──RFB──→ Xvfb (:99)
                                                        └──── Chromium (Playwright)
```

每个发布任务占用一个 display + VNC 端口 + noVNC 端口。并发任务数受限于服务器资源。

## 注意事项

- 远程浏览器模式仅支持 Linux
- 每个浏览器实例约占用 300-500MB 内存
- 人工介入的浏览器会话在 5 分钟无操作后自动清理
- 建议使用 systemd 或 Docker 管理进程生命周期
