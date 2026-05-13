# Geo Collab Docker 镜像
# FastAPI + React + Playwright + Chromium + noVNC 远程浏览器

FROM python:3.12-slim

# 系统依赖：Chromium 浏览器、Xvfb 虚拟显示、VNC 远程桌面、noVNC Web 客户端、
# 中文字体、Chromium/Playwright 运行时库
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    websockify \
    novnc \
    fonts-noto-cjk \
    libnss3 \
    libnspr4 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 Python 依赖（清华镜像加速）
COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# 复制项目源码
COPY . .

# 安装 Playwright 所需的 Chromium 浏览器
RUN playwright install chromium

EXPOSE 8000

CMD ["python", "launcher.py"]
