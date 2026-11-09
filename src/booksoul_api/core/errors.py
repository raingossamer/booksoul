"""HTTP 层各子系统的自有异常。

引擎那边的先例是 `brain.py` 的 `BrainError`：把 anthropic SDK 错误和标准库 HTTP
错误归一成一个类型，上层只展示 message，不关心底层是谁抛的。这里沿用同一做法。

异常消息一律中文。按 Spec N1，**面向用户的边界必须给出下一步动作或可选值**，
引擎内部路径才允许只陈述事实——HTTP 层整个就是面向用户的边界，所以这里每条
消息都得带上「那我该怎么办」。
"""

from __future__ import annotations


class ApiError(RuntimeError):
    """本层所有自有异常的根。上层只看 message、状态码，必要时再加一个 code。

    `code` 是给**前端分支用**的机读标识，默认空：绝大多数错误只要把话说给人听
    就够了。只有当同一个状态码下有两种前端必须区别对待的情况时才给它一个 code——
    否则前端只能去匹配中文文案，那句话改个措辞就会把分支改坏，而且不报错。

    code 一律是写死的字面量，**绝不来自请求**：它会原样出现在响应体里。
    """

    status_code = 400
    code = ""


class AuthError(ApiError):
    """认证失败：token 无效、过期、类型不对，或凭据不匹配。

    刻意**不区分**「用户不存在」和「密码错」——那个区分等于提供了一个
    「这个邮箱在不在这个站上注册过」的查询接口。
    """

    status_code = 401


class ConflictError(ApiError):
    """与已有数据冲突，如邮箱已被注册。"""

    status_code = 409


class ReselectRequired(ConflictError):
    """问到了这次没载入的章节。要重新挑一次，但得用户点头。

    和「上一个问题还在回答」同为 409，两者前端必须区别对待：一个要弹出
    「重新挑章节」的确认，另一个只要让人等一会儿。靠中文文案分辨它们，
    是把界面逻辑挂在一句随时会被润色的话上。
    """

    code = "reselect_required"


class BookNotReady(ConflictError):
    """这本书此刻不能拿来做这件事：问，或者重新解析。

    409 带 `code`，三种出路不同，前端要分得开：`book_parsing`（正在解析，等它停下来）/
    `book_failed`（上一次解析停下了，去书籍详情看）/ `source_missing`（原件不在了，只能再传一次）。
    **code 与措辞都只来自 `services/book_lifecycle` 的门表**。
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class UnsupportedFormat(ApiError):
    """认得这种格式，这个部署却没装它的解析后端。415，在建任何一行之前拒。

    话里不带装包命令：那是管理员的事，进 ERROR 日志。原先照样收下、建行、存原件，
    解析失败之后再给一句「点重新解析再试一次」——那条出路永远走不通。
    """

    status_code = 415
    code = "unsupported_format"


class NotFoundError(ApiError):
    """找不到，或者不属于当前用户。

    越权与不存在返回同一个 404 是刻意的：403 会告诉对方「这个 id 确实存在，
    只是不归你」，那本身就是一次信息泄露。见 `backend.md`「认证授权」。
    """

    status_code = 404


class BrainUnavailable(ApiError):
    """去问供应商的那一下失败了（⑧屏「列出模型」）。

    502：错的不是这个请求，是它背后的那一家。`code` 四选一——`brain_auth` /
    `brain_unsupported` / `brain_timeout` / `brain_network`——前端按它给出路：
    回去查 key、改成手填、稍后再试。**code 只来自 `brain_service` 里那张写死的表。**
    服务器自己缺依赖时 code 为空：那不是用户或供应商能解决的，前端不给任何出路按钮。
    """

    status_code = 502

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class UnprocessableError(ApiError):
    """请求本身合法，但内容用不了。

    和 Pydantic 的字段校验同为 422：对前端而言两者是一回事——「你发来的东西
    我读懂了，但它不对」。区别只在于有些不对是 schema 能看出来的，有些不能，
    比如一段人格提示词里的占位符拼错了。
    """

    status_code = 422
