# Geo 协作平台

本仓库是 Geo 协作平台 Windows 本地 MVP 的工程目录。实施计划见 `plan/README.md`。

## 环境

- Python 使用 conda 环境：`geo_xzpt`
- 前端使用 Node.js + pnpm

## 后端开发

```powershell
conda activate geo_xzpt
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/system/status
```

## 前端开发

```powershell
pnpm install
pnpm --filter @geo/web dev
```

## 数据目录

默认数据目录为 `%LOCALAPPDATA%/GeoCollab`，可用环境变量覆盖：

```powershell
$env:GEO_DATA_DIR="E:\geo\GeoAppData"
```

## 头条号 Spike

先运行登录状态保存脚本，按浏览器提示人工登录或扫码：

```powershell
conda activate geo_xzpt
python -m server.scripts.toutiao_login_spike --account-key spike
```

如果验证码阶段卡住，先停止重试，等待一段时间后改用本机 Edge 或 Chrome channel：

```powershell
python -m server.scripts.toutiao_login_spike --account-key edge-spike --channel msedge
python -m server.scripts.toutiao_login_spike --account-key chrome-spike --channel chrome
```

本机路径可显式指定：

```powershell
python -m server.scripts.toutiao_login_spike --account-key edge-spike --executable-path "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

登录状态验证通过后，再打开发布页 Spike：

```powershell
python -m server.scripts.toutiao_publish_spike --account-key spike
```
