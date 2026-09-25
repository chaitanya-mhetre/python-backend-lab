from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from flowforge.api.deps import CurrentUser, OrgAccessDep, SessionDep
from flowforge.api.schemas import ApiKeyCreatedOut, ApiKeyIn, ApiKeyOut
from flowforge.db.models import ApiKey
from flowforge.security.principal import Principal
from flowforge.services.access import AccessResolver
from flowforge.services.api_keys import ApiKeyService

router = APIRouter(tags=["api-keys"])


@router.post(
    "/orgs/{org_id}/api-keys",
    response_model=ApiKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    body: ApiKeyIn, access: OrgAccessDep, _user: CurrentUser, session: SessionDep
) -> ApiKeyCreatedOut:
    key, full_key = await ApiKeyService(session).create(access, body.name, body.scopes)
    return ApiKeyCreatedOut(**ApiKeyOut.model_validate(key).model_dump(), key=full_key)


@router.get("/orgs/{org_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(access: OrgAccessDep, _user: CurrentUser, session: SessionDep) -> object:
    return await ApiKeyService(session).list_all(access)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    key = await session.get(ApiKey, key_id)
    org_id = key.org_id if key is not None else uuid.uuid4()  # unknown id -> 404 below
    access = await AccessResolver(session).for_org(Principal(user=user), org_id)
    await ApiKeyService(session).revoke(access, key_id)
