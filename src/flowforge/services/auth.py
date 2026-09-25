from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.config import Settings
from flowforge.db.models import User
from flowforge.domain.errors import AuthenticationError, ConflictError
from flowforge.repositories.users import UserRepository
from flowforge.security.passwords import hash_password, verify_password
from flowforge.security.tokens import create_access_token

# Verifying against a dummy hash when the email is unknown keeps response time the same,
# so an attacker cannot discover which emails are registered by timing the login endpoint.
_DUMMY_HASH = hash_password("timing-equaliser")


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._settings = settings

    async def register(self, email: str, password: str, full_name: str) -> User:
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("email is already registered")
        user = await self._users.add(
            User(email=email, password_hash=hash_password(password), full_name=full_name)
        )
        await self._session.commit()
        return user

    async def login(self, email: str, password: str) -> str:
        user = await self._users.get_by_email(email)
        if user is None:
            verify_password(_DUMMY_HASH, password)
            raise AuthenticationError("invalid email or password")
        if not verify_password(user.password_hash, password):
            raise AuthenticationError("invalid email or password")
        return create_access_token(user.id, self._settings)
