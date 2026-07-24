# geo-collab-server —— 薄代码层：FROM geo-collab-base + 仅复制源码。
# 慢变的浏览器/依赖在 base 里；改代码只重建下面的 COPY 层（几 MB）。
# 前端由 nginx（geo-collab-web）serve，生产 app 不 serve SPA → 不再含 node 构建 / web/dist。
ARG BASE_IMAGE=geo-collab-base:latest
FROM ${BASE_IMAGE}

WORKDIR /app

# 源码（.dockerignore 已排除 node_modules/.git/data 等）
COPY . .

EXPOSE 8000

# 迁移不在此执行（由 compose 的一次性 migrate 服务单点跑）。此处只起 web。
CMD ["sh", "-c", "uvicorn server.app.main:app --host 0.0.0.0 --port 8000"]
