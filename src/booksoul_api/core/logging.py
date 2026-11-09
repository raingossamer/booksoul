"""请求日志：白名单制。

只记四件事：**方法、路径、状态码、耗时**。别的一概不记。

这是白名单而不是黑名单，区别不是风格问题。黑名单（「把 X-Brain-Key 过滤掉」）
只挡得住今天想得到的那个头；明天加一个 `X-Vendor-Token`，它就照样进日志，
而且没人会注意到——日志坏掉的时候不会报错，它只是安静地多记了一行。

三处具体的克制，每一处都对应一条真实的泄漏路径：

- **不记 query string**，只记 path。key 不该出现在 URL 里，那是前端的纪律；
  服务端这一侧不替它兜着，但也绝不把它抄进日志。
- **不记任何请求头**。`Authorization` 和 `X-Brain-Key` 都在里面。
- **不记请求体与响应体**。上传的是整本书，key 也可能被谁塞进 JSON 里。

记 `request.url.path` 而不是 `request.scope["path"]`，这两个看着等价，**不等价**：
后者是解码后的原样，路径里一个 `%0A` 就能把一行访问日志劈成两行，后一行看起来
就是另一次请求——伪造一条「某人登录成功」不需要任何权限。`request.url.path` 走
`urlsplit`，它会剥掉 ASCII 的换行与制表符。`/api/personas/{key}`（S11）是这个应用
里第一个自由文本的路径参数，在那之前没人能把控制字符送到这里来。
`tests/test_personas.py::test_路径里的换行进不了日志` 钉着这一点。

还有一个不在这个中间件里、但必须在这里一并处理的洞：**uvicorn 自带的访问日志
会把完整 URL（含 query string）记下来**。我们这边多小心都没用——真要有谁把 key
拼进了 URL，那一行会照抄不误。所以 `configure()` 把它关掉，换成上面这一行
同样内容、但只到 path 为止的日志。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("booksoul_api.access")

Handler = Callable[[Request], Awaitable[Response]]


async def log_requests(request: Request, call_next: Handler) -> Response:
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # 异常也要留下这一行，否则日志里会看到请求进来却没有下文。
        # 只记「哪条路径炸了」；异常本身由异常处理器去记，那里同样不带请求上下文。
        elapsed = (time.perf_counter() - started) * 1000
        logger.warning(
            "%s %s -> 未处理的异常 %.0fms", request.method, request.url.path, elapsed
        )
        raise

    elapsed = (time.perf_counter() - started) * 1000
    logger.info(
        "%s %s %d %.0fms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


def configure(*, level: int = logging.INFO) -> None:
    """装好应用自己的日志，并关掉 uvicorn 自带的访问日志。

    关掉不是嫌它吵，是因为它记的是**完整 URL**：

        INFO: 127.0.0.1 - "GET /api/brains?api-key=sk-... HTTP/1.1" 200 OK

    上面那个白名单中间件再克制，只要这一行还在，「key 不进日志」就是一句空话。
    换掉它没有损失——method / path / status / 耗时四样，上面那一行全都记了，
    少的只有 query string 和客户端端口。

    uvicorn 只给 `uvicorn.*` 装处理器，根 logger 通常是空的，所以我们自己的
    日志得在这里接上，否则它一行都不会出现——而「看不到日志」和「日志是干净的」
    在屏幕上长得一模一样。
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(levelname)s:     %(message)s")
    logging.getLogger("booksoul_api").setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True
