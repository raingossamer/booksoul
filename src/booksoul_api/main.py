"""FastAPI 应用装配。

异常处理器在这里收口：service 层抛自有异常（`ApiError` 的子类），路由不必逐个
try/except，也不必知道该回哪个状态码。

一条硬规则写在这里：**异常响应里不带 locals、不回显请求头**。用户的模型 API key
是经 `X-Brain-Key` 头送进来的，一次「把请求头一起报上去」的好心调试代码，就等于
把所有人的 key 抄进了日志。见 `backend.md`「认证授权」。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from booksoul_api.core.errors import ApiError
from booksoul_api.core.logging import configure as configure_logging
from booksoul_api.core.logging import log_requests
from booksoul_api.routers import (
    auth,
    books,
    brains,
    citations,
    conversations,
    formats,
    jobs,
    personas,
)
from booksoul_api.services.book_lifecycle import sweep_orphans
from booksoul_api.services.format_service import capability

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """起来先收尾上一条命。

    本地 `uvicorn --reload` 存个文件就重启，正在解析的任务当场没了下文。
    不收拾，那本书就永远卡在「解析中」——那比报错更让人没辙，
    因为卡住连个出路都不给。
    """
    # 先装日志再干别的：孤儿清扫那条 WARNING 也要有人接得住
    configure_logging()

    try:
        orphaned = await sweep_orphans()
        if orphaned:
            logger.warning(
                "上次退出时有 %d 个解析任务没跑完，已标为可重新解析", orphaned
            )
    except Exception:
        # 清扫失败不该拦着服务起来——那会把一个小问题放大成整站不可用
        logger.exception("启动清扫解析任务失败")

    capability()  # 探测一次，缺依赖的 ERROR 日志在启动时就给管理员看到
    yield


app = FastAPI(
    title="booksoul",
    description="把书解析成模型读得懂的样子，再让它回答你",
    lifespan=lifespan,
)

# 请求日志：白名单制，只记方法/路径/状态/耗时。见 core/logging.py 的说明。
app.middleware("http")(log_requests)

# 本地开发：Vite 跑在 5173。范围收窄至本地单实例（Grill D7），
# 上线前这里必须换成显式白名单。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 开会话回 201 + Location，前端靠它在流还在走时就换到新会话的地址。
    # 跨源时不在这里点名，脚本读到的 Location 是 null——不报错，只是换不过去
    expose_headers=["Location"],
)


@app.exception_handler(ApiError)
async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    """把自有异常转成 FastAPI 默认的 {detail} 结构。

    只取 message 和那个写死的 code，不取别的。异常对象上可能挂着 service 层的
    上下文，而请求头里可能有用户的模型 key——两者都不该出现在响应里。
    """
    body = {"detail": str(exc)}
    if exc.code:
        body["code"] = exc.code
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    """422：说清哪一栏、什么限制——**不回显填进来的值**。

    FastAPI 默认的 422 会把被拒的值原样放进 `input`（有时还有 `ctx`）。自由文本框里
    什么都可能被粘进来：往「环境变量名」那一栏粘一串 100 字的 key，默认处理器就会
    因为超长把整串 key 抄回响应——落进前端的报错、控制台与错误上报。
    形状保持 `{"detail": [{loc, msg, type}]}`，前端照旧读 `detail[0].msg`。
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "loc": list(e.get("loc", ())),
                    "msg": e.get("msg", ""),
                    "type": e.get("type", ""),
                }
                for e in exc.errors()
            ]
        },
    )


@app.exception_handler(Exception)
async def _unexpected_handler(_: Request, exc: Exception) -> JSONResponse:
    """没预料到的异常：对外一句人话，对内一条带回溯的日志。

    这一档最容易出错，因为前面几档都有人仔细写过，而这一档「反正跑不到」。
    两条纪律：

    - **响应里不带异常内容**。`str(exc)` 里可能有任何东西 —— 包括某个局部变量的
      `repr()`，而那个局部变量可能正是本次请求的模型 key。
    - **日志里不带请求上下文**。记回溯，不记请求头、不记请求体、不记 query。
      哪条路径炸了由访问日志那一行给出。
    """
    logger.exception("未处理的异常", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器这边出错了。这不是你的操作问题，稍后再试一次。"},
    )


app.include_router(auth.router)
app.include_router(books.router)
app.include_router(jobs.router)
app.include_router(formats.router)
app.include_router(brains.router)
app.include_router(conversations.router)
app.include_router(citations.router)
app.include_router(personas.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
