"""人格提示词（界面稿⑧屏右栏）。

**要登录才能看，也才能改。** 没有 user_id 参与鉴权之外的任何判断——按 Spec N5，
本轮人格提示词是**全局共享**的：谁改了，所有人下一轮都用新的。这在代码里看不
出来（这里根本没有 user_id），所以界面上必须写明白，见⑧屏那行说明。
多用户真的上线之前，这条要回头处理：建 `user_personas` 表或按用户目录隔离。

写盘那一摊全在 `services/prompt_store` 里：占位符体检、原子替换、出厂基线。
路由只做一件事——把 `KeyError` 翻成 404、把 `PromptError` 翻成 422。
"""

from __future__ import annotations

from fastapi import APIRouter

from booksoul_api.core.errors import NotFoundError, UnprocessableError
from booksoul_api.deps import CurrentUser
from booksoul_api.schemas.persona import PersonaOut, PersonaUpdate
from booksoul_api.services import prompt_store

router = APIRouter(prefix="/api/personas", tags=["personas"])


@router.get("", response_model=list[PersonaOut])
async def list_personas(user: CurrentUser) -> list[PersonaOut]:
    """三种人格的名录，不带正文。"""
    return [PersonaOut(**vars(p)) for p in prompt_store.personas()]


@router.get("/{key}", response_model=PersonaOut)
async def get_persona(key: str, user: CurrentUser) -> PersonaOut:
    return PersonaOut(**vars(_guard(prompt_store.read, key)))


@router.put("/{key}", response_model=PersonaOut)
async def put_persona(
    key: str, payload: PersonaUpdate, user: CurrentUser
) -> PersonaOut:
    """覆写提示词。改完即时生效——引擎每一轮都重新读文件，不必重启。

    占位符拼错了在这里就被拦下（422）。不拦的话它会在**下一次提问时**炸在
    后台任务里，用户看到的只是「这条回答没说完」。
    """
    return PersonaOut(**vars(_guard(prompt_store.write, key, payload.text)))


@router.post("/{key}/restore", response_model=PersonaOut)
async def restore_persona(key: str, user: CurrentUser) -> PersonaOut:
    """还原成第一次从这里改动之前的那一版。

    `prompts/` 不在版本控制里，一次覆写不留退路等于把这三个文件交给手气。
    """
    return PersonaOut(**vars(_guard(prompt_store.restore, key)))


def _guard(fn, key: str, *args) -> prompt_store.PromptFile:
    try:
        return fn(key, *args)
    except KeyError as exc:
        options = "、".join(prompt_store.persona_keys())
        raise NotFoundError(f"没有名为 {_echo(key)} 的人格。可选：{options}。") from exc
    except prompt_store.PromptError as exc:
        raise UnprocessableError(str(exc)) from exc


def _echo(key: str) -> str:
    """把用户发来的那个 key 回显给他看——但**先修剪过**。

    Spec N1 要求错误消息给出路，所以要把他敲的那个词说出来；可这是这个应用里
    第一个自由文本的路径参数，原样回显就是把任意一段字抄进响应体和日志里。
    两条剪法：去掉控制字符（换行会让一行日志变成两行，看起来像另一次请求），
    再截短（一个 4KB 的路径不该换来一条 4KB 的错误消息）。
    """
    cleaned = "".join(c for c in key if c.isprintable())
    return cleaned[:40] + "…" if len(cleaned) > 40 else cleaned
