from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from flowforge.api.deps import CurrentUser, OrgAccessDep, SessionDep
from flowforge.api.schemas import ExecutionDetailOut, ExecutionOut, WorkflowIn, WorkflowOut
from flowforge.services.access import AccessResolver
from flowforge.services.workflows import StepSpec, WorkflowService, WorkflowSpec

router = APIRouter(tags=["workflows"])


def to_spec(body: WorkflowIn) -> WorkflowSpec:
    return WorkflowSpec(
        name=body.name,
        trigger_type=body.trigger_type,
        trigger_filter=body.trigger_filter,
        steps=[StepSpec(type=s.type, config=s.config) for s in body.steps],
    )


@router.post(
    "/orgs/{org_id}/workflows", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED
)
async def create_workflow(body: WorkflowIn, access: OrgAccessDep, session: SessionDep) -> object:
    return await WorkflowService(session).create(access, to_spec(body))


@router.get("/orgs/{org_id}/workflows", response_model=list[WorkflowOut])
async def list_workflows(access: OrgAccessDep, session: SessionDep) -> object:
    return await WorkflowService(session).list_all(access)


@router.get("/workflows/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> object:
    access = await AccessResolver(session).for_workflow(user, workflow_id)
    return await WorkflowService(session).get(access, workflow_id)


@router.put("/workflows/{workflow_id}", response_model=WorkflowOut)
async def replace_workflow(
    workflow_id: uuid.UUID, body: WorkflowIn, user: CurrentUser, session: SessionDep
) -> object:
    access = await AccessResolver(session).for_workflow(user, workflow_id)
    return await WorkflowService(session).replace(access, workflow_id, to_spec(body))


# Custom methods use the ":verb" style (Google API design guide) for actions that aren't CRUD.
@router.post("/workflows/{workflow_id}:enable", response_model=WorkflowOut)
async def enable_workflow(workflow_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> object:
    access = await AccessResolver(session).for_workflow(user, workflow_id)
    return await WorkflowService(session).set_enabled(access, workflow_id, True)


@router.post("/workflows/{workflow_id}:disable", response_model=WorkflowOut)
async def disable_workflow(
    workflow_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> object:
    access = await AccessResolver(session).for_workflow(user, workflow_id)
    return await WorkflowService(session).set_enabled(access, workflow_id, False)


@router.get("/workflows/{workflow_id}/executions", response_model=list[ExecutionOut])
async def list_executions(workflow_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> object:
    access = await AccessResolver(session).for_workflow(user, workflow_id)
    return await WorkflowService(session).executions(access, workflow_id)


@router.get("/executions/{execution_id}", response_model=ExecutionDetailOut)
async def get_execution(
    execution_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ExecutionDetailOut:
    access = await AccessResolver(session).for_execution(user, execution_id)
    row, runs = await WorkflowService(session).execution(access, execution_id)
    base = ExecutionOut.model_validate(row).model_dump()
    return ExecutionDetailOut.model_validate({**base, "step_runs": runs})


@router.post("/executions/{execution_id}:cancel", response_model=ExecutionOut)
async def cancel_execution(
    execution_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> object:
    access = await AccessResolver(session).for_execution(user, execution_id)
    return await WorkflowService(session).cancel(access, execution_id)
