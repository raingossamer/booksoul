#!/bin/sh
# API 容器的入口：先把 schema 迁到最新，再起服务。
#
# 迁移放在这里而不是镜像构建里：构建时数据库还不存在。compose 用
# depends_on: service_healthy 保证 Postgres 已经能接连接，所以这里不再自己重试。
set -e

echo "[booksoul] alembic upgrade head ..."
alembic upgrade head

echo "[booksoul] starting uvicorn ..."
# --no-access-log：backend.md「日志」那条要求「必须一并关掉框架自带的访问日志」
# （uvicorn.access 记的是完整 URL，白名单制的请求日志只要它还在就全是空话）。
# core/logging.py 里已经关过一次，这里显式再关一次 —— 这种事不该只靠一处。
# --proxy-headers：前面有 nginx，不带它拿到的 scheme 与来源地址都是容器内网的。
exec uvicorn booksoul_api.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --no-access-log \
    --proxy-headers \
    --forwarded-allow-ips '*'
