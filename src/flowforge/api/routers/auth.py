from __future__ import annotations

from fastapi import APIRouter, Response, status

from flowforge.api.deps import CurrentUser, SessionDep, SettingsDep
from flowforge.api.schemas import LoginIn, RefreshIn, RegisterIn, TokenOut, UserOut
from flowforge.services.auth import AuthService, TokenPair

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: SessionDep, settings: SettingsDep) -> object:
    return await AuthService(session, settings).register(
        str(body.email), body.password, body.full_name
    )


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, session: SessionDep, settings: SettingsDep) -> TokenOut:
    pair = await AuthService(session, settings).login(str(body.email), body.password)
    return _token_out(pair)


@router.post("/auth/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, session: SessionDep, settings: SettingsDep) -> TokenOut:
    """Exchange a refresh token for a new pair. The old refresh token stops working."""
    pair = await AuthService(session, settings).refresh(body.refresh_token)
    return _token_out(pair)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, session: SessionDep, settings: SettingsDep) -> Response:
    """Revoke every refresh token from this login. Access tokens still expire on their own."""
    await AuthService(session, settings).logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _token_out(pair: TokenPair) -> TokenOut:
    return TokenOut(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> object:
    return user
