"""注册 / 登录 / 刷新 / 当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, status

from booksoul_api.deps import CurrentUser, SessionDep
from booksoul_api.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from booksoul_api.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: SessionDep) -> TokenPair:
    _, tokens = await AuthService(session).register(
        email=str(payload.email),
        password=payload.password,
        display_name=payload.display_name,
    )
    return tokens


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, session: SessionDep) -> TokenPair:
    _, tokens = await AuthService(session).login(
        email=str(payload.email), password=payload.password
    )
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    return await AuthService(session).refresh(refresh_token=payload.refresh_token)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)
