from __future__ import annotations

from fastapi import APIRouter, status

from flowforge.api.deps import CurrentUser, SessionDep, SettingsDep
from flowforge.api.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from flowforge.services.auth import AuthService

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: SessionDep, settings: SettingsDep) -> object:
    return await AuthService(session, settings).register(
        str(body.email), body.password, body.full_name
    )


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, session: SessionDep, settings: SettingsDep) -> TokenOut:
    token = await AuthService(session, settings).login(str(body.email), body.password)
    return TokenOut(access_token=token)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> object:
    return user
