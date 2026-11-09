# 三个阶段：前端构建 → nginx（静态 + 反代）→ Python（API）。
#
# 为什么静态文件交给 nginx 而不是 FastAPI：路由是 `createWebHistory()`，
# 深链（/books/3/read/2）必须回 index.html。在 Python 里做这件事要同时处理
# 条件挂载、SPA 回退、以及「别让回退吞掉 /api/* 的 404」—— 最后那条一旦漏掉，
# 一个打错的接口路径会变成 200 的 HTML，正撞上 backend.md 那条
# 「『没有』有好几种，不能压成同一个值」。nginx 的 try_files 两行就够，
# 而且产品代码一行都不用改。

# ---------------------------------------------------------------- 前端构建
FROM node:22-alpine AS web-build
WORKDIR /web

# 先只拷 lock 文件：依赖没变时这一层命中缓存，改前端代码不会重装 node_modules
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
# `npm run build` = vue-tsc --noEmit && vite build。类型不过就构建失败，
# 这是刻意的：让类型错误停在这里，而不是等到浏览器里才发现。
RUN npm run build

# ---------------------------------------------------------------- 前端运行
FROM nginx:1.27-alpine AS web
COPY --from=web-build /web/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# ---------------------------------------------------------------- API
FROM python:3.13-slim AS api
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖，再拷源码：改代码不会让 pip 重跑一遍
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e ".[api,all]"

COPY alembic.ini booksoul.toml ./
COPY migrations/ ./migrations/
COPY prompts/ ./prompts/
COPY docker/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh
RUN chmod +x /usr/local/bin/api-entrypoint.sh

# 以 editable 装在 /app：`persona.prompt_dir()` 的兜底是
# 「src/booksoul 往上两级」= /app，于是 /app/prompts 正好是它要找的那份。
# 换成非 editable 装进 site-packages，那个兜底会指到一个不存在的地方。

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/api-entrypoint.sh"]
