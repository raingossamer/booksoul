"""对话的请求与响应（界面稿⑦屏）。

`complete` 是这组 schema 里最要紧的一个布尔值：false 表示这条回答是断流留下的
残文。界面据此渲染「这条回答没说完」以及「重问 / 接着往下」两个按钮 ——
把它漏掉，用户会把一段半截话当成模型的完整回答读下去。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from booksoul_api.schemas import ApiModel
from booksoul_api.schemas.citation import CitationOut
from booksoul_api.schemas.retrieval import RetrievalBrief


class ConversationCreate(ApiModel):
    """开会话，连同第一问。

    没有「只建一个会话」的选项：会话从第一问开始存在。先建后问，点进来又走掉
    就留下一个空会话。
    """

    book_id: int
    persona: str = "scholar"
    brain_name: str
    # 0 = 按所选模型的上下文窗口自动估；非 0 为手动指定
    budget_chars: int = Field(default=0, ge=0)
    reflection: bool = False
    # 第一问。限制与 `AskRequest` 的同一个
    question: str = Field(min_length=1, max_length=4000)
    # 第一问就对读。理由见 `AskRequest.compare`
    compare: bool = False


class AskRequest(ApiModel):
    question: str = Field(min_length=1, max_length=4000)
    # 切人格：不给就沿用会话的主干人格
    persona: str | None = None
    # 对读：一轮同时跑三种人格。默认 false。
    # 不弹确认（人工卡点已裁决）——三路共享整本书的缓存，真实成本不是三倍，
    # 而对读正是这个产品的立意；每次多一步点击会把它变成一个没人用的功能。
    # 代价写在按钮旁与底部仪表上，用户随时能在⑥屏看到真实账单。
    compare: bool = False
    # 重新挑一次章节。默认 false —— 重选会换掉整套章节、缓存作废，这一轮的输入
    # 要按全价重算一次。那笔钱由用户点头才花，所以它必须是一个显式动作。
    reselect: bool = False


class ConversationPatch(ApiModel):
    """改会话的两个旋钮。都是可选的，**没给的那个不动**。

    `reflection=False` 是「把处境模式关掉」，和「这次没提这件事」是两回事，
    所以用 `None` 表示后者，不用默认值。
    """

    # 「跟这个继续聊」：把主干人格换成对读里的某一路（S10）
    persona: str | None = None
    # 处境模式（S11）。随时可拨：人格那一段在缓存断点之后，附加一段指令
    # 不动整本书那一块，缓存照常命中——不像重选章节那样要用户点头。
    reflection: bool | None = None


class MessageOut(ApiModel):
    id: int
    seq: int
    turn: int
    role: str
    # user 消息为 None；assistant 消息记它是哪一路人格
    persona: str | None
    content: str
    # false = 断流留下的残文。已经生成的部分要保住，用户花的钱不能白花。
    complete: bool
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    created_at: datetime
    # 这条回答引了哪些原文。跟着会话详情一起给，免得一屏几十条回答
    # 各自再去问一次（单条回答的那个端点仍在，⑤屏那条来路条走的是它）。
    citations: list[CitationOut] = []


class ConversationOut(ApiModel):
    id: int
    book_id: int
    book_title: str
    persona: str
    brain_name: str
    model: str
    # 第一个问题，用作会话标题 —— ③屏「最近问过的」显示的就是它
    title: str
    reflection: bool
    turn_count: int
    created_at: datetime
    updated_at: datetime

    # 只有详情才带
    messages: list[MessageOut] = []
    book_word_count: int = 0
    book_chapter_count: int = 0
    # 这个会话当下的选章结论，够⑦屏底下那条仪表写「读了 5/16 节」。
    # null = 还没问过。整张排名表走 `GET /api/conversations/{id}/retrieval`。
    retrieval: RetrievalBrief | None = None
