from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.config import Settings
from flowforge.db.models import RefreshToken, User
from flowforge.domain.errors import AuthenticationError, ConflictError
from flowforge.repositories.refresh_tokens import RefreshTokenRepository
from flowforge.repositories.users import UserRepository
from flowforge.security.passwords import hash_password, verify_password
from flowforge.security.tokens import create_access_token, hash_refresh_token, new_refresh_token

# Verifying against a dummy hash when the email is unknown keeps response time the same,
# so an attacker cannot discover which emails are registered by timing the login endpoint.
_DUMMY_HASH = hash_password("timing-equaliser")

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int  # access-token lifetime in seconds


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings, clock: Clock = _utcnow) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._refresh = RefreshTokenRepository(session)
        self._settings = settings
        self._clock = clock

    async def register(self, email: str, password: str, full_name: str) -> User:
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("email is already registered")
        user = await self._users.add(
            User(email=email, password_hash=hash_password(password), full_name=full_name)
        )
        await self._session.commit()
        return user

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self._users.get_by_email(email)
        if user is None:
            verify_password(_DUMMY_HASH, password)
            raise AuthenticationError("invalid email or password")
        if not verify_password(user.password_hash, password):
            raise AuthenticationError("invalid email or password")
        # Every login starts a new token family.
        pair = await self._issue(user.id, family_id=uuid.uuid4(), parent_id=None)
        await self._session.commit()
        return pair

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Rotate: the presented token is spent and a new one in the same family is issued.

        A token that was already spent means it was copied and replayed (by an attacker, or by
        the legitimate client after the attacker used it first). We cannot tell which party is
        genuine, so we revoke the whole family and force both to log in again.
        """
        now = self._clock()
        row = await self._refresh.get_by_hash_for_update(hash_refresh_token(refresh_token))
        if row is None or row.revoked_at is not None:
            raise AuthenticationError("invalid refresh token")
        if row.used_at is not None:
            await self._refresh.revoke_family(row.family_id, now)
            await self._session.commit()  # the revocation must stick even though we reject
            raise AuthenticationError("refresh token reuse detected; please log in again")
        if row.expires_at <= now:
            raise AuthenticationError("refresh token expired")
        row.used_at = now
        pair = await self._issue(row.user_id, family_id=row.family_id, parent_id=row.id)
        await self._session.commit()
        return pair

    async def logout(self, refresh_token: str) -> None:
        """Revoke the token's family. Unknown tokens are ignored so logout is idempotent."""
        row = await self._refresh.get_by_hash_for_update(hash_refresh_token(refresh_token))
        if row is not None:
            await self._refresh.revoke_family(row.family_id, self._clock())
            await self._session.commit()

    async def _issue(
        self, user_id: uuid.UUID, *, family_id: uuid.UUID, parent_id: uuid.UUID | None
    ) -> TokenPair:
        plaintext, token_hash = new_refresh_token()
        await self._refresh.add(
            RefreshToken(
                user_id=user_id,
                family_id=family_id,
                parent_id=parent_id,
                token_hash=token_hash,
                expires_at=self._clock() + timedelta(days=self._settings.refresh_ttl_days),
            )
        )
        return TokenPair(
            access_token=create_access_token(user_id, self._settings),
            refresh_token=plaintext,
            expires_in=self._settings.jwt_ttl_minutes * 60,
        )
