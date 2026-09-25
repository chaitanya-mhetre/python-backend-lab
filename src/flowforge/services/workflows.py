"""Workflow definitions and executions, API side (the worker lives in ``flowforge.worker``)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import (
    StepRunRow,
    WorkflowDefinitionRow,
    WorkflowExecutionRow,
    WorkflowStepRow,
)
from flowforge.domain.errors import ConflictError, DomainError, NotFoundError
from flowforge.domain.workflow import ExecutionStatus, StepType, TriggerType
from flowforge.repositories.audit import AuditRepository
from flowforge.repositories.workflows import ExecutionRepository, WorkflowRepository
from flowforge.security.permissions import Action
from flowforge.services.access import OrgAccess, requires


class InvalidStepConfigError(DomainError):
    code = "invalid_step_config"


# --- per-step-type config schemas (validated when a workflow is saved, not when it runs) ---


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTaskConfig(_Cfg):
    title: str = Field(min_length=1, max_length=300)
    project_id: uuid.UUID | None = None  # default: the triggering task's project


class UpdateFieldConfig(_Cfg):
    field: Literal["status", "priority"]
    value: str | int


class SendNotificationConfig(_Cfg):
    to: Literal["assignee", "creator"] | uuid.UUID
    message: Annotated[str, Field(min_length=1, max_length=1000)]


class CallWebhookConfig(_Cfg):
    webhook_id: uuid.UUID


class DelayConfig(_Cfg):
    seconds: int = Field(ge=1, le=7 * 24 * 3600)


STEP_CONFIGS: dict[StepType, type[_Cfg]] = {
    StepType.CREATE_TASK: CreateTaskConfig,
    StepType.UPDATE_FIELD: UpdateFieldConfig,
    StepType.SEND_NOTIFICATION: SendNotificationConfig,
    StepType.CALL_WEBHOOK: CallWebhookConfig,
    StepType.DELAY: DelayConfig,
}


def validate_step_config(step_type: StepType, config: dict[str, Any]) -> dict[str, Any]:
    try:
        model = STEP_CONFIGS[step_type].model_validate(config)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise InvalidStepConfigError(f"{step_type.value}.{where}: {first['msg']}") from None
    return model.model_dump(mode="json", exclude_none=True)


@dataclass(frozen=True, slots=True)
class StepSpec:
    type: StepType
    config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    name: str
    trigger_type: TriggerType
    trigger_filter: dict[str, Any]
    steps: list[StepSpec]


def _build_steps(spec: WorkflowSpec) -> list[WorkflowStepRow]:
    if not spec.steps:
        raise InvalidStepConfigError("a workflow needs at least one step")
    return [
        WorkflowStepRow(position=i, step_type=s.type, config=validate_step_config(s.type, s.config))
        for i, s in enumerate(spec.steps, start=1)
    ]


class WorkflowService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._workflows = WorkflowRepository(session)
        self._executions = ExecutionRepository(session)
        self._audit = AuditRepository(session)

    async def _load(self, access: OrgAccess, definition_id: uuid.UUID) -> WorkflowDefinitionRow:
        row = await self._workflows.get(definition_id)
        if row is None or row.org_id != access.org_id:
            raise NotFoundError("workflow", definition_id)
        return row

    @requires(Action.WORKFLOW_MANAGE)
    async def create(self, access: OrgAccess, spec: WorkflowSpec) -> WorkflowDefinitionRow:
        row = WorkflowDefinitionRow(
            org_id=access.org_id,
            name=spec.name,
            trigger_type=spec.trigger_type,
            trigger_filter=spec.trigger_filter,
            enabled=True,
            version=1,
            created_by=access.user_id,
            steps=_build_steps(spec),
        )
        await self._workflows.add(row)
        self._audit.record(
            org_id=access.org_id,
            action="workflow.created",
            entity_type="workflow",
            entity_id=row.id,
            after={"name": row.name, "trigger": row.trigger_type.value},
        )
        await self._session.commit()
        return row

    @requires(Action.WORKFLOW_READ)
    async def list_all(self, access: OrgAccess) -> list[WorkflowDefinitionRow]:
        return await self._workflows.list_for_org(access.org_id)

    @requires(Action.WORKFLOW_READ)
    async def get(self, access: OrgAccess, definition_id: uuid.UUID) -> WorkflowDefinitionRow:
        return await self._load(access, definition_id)

    @requires(Action.WORKFLOW_MANAGE)
    async def replace(
        self, access: OrgAccess, definition_id: uuid.UUID, spec: WorkflowSpec
    ) -> WorkflowDefinitionRow:
        row = await self._load(access, definition_id)
        row.name = spec.name
        row.trigger_type = spec.trigger_type
        row.trigger_filter = spec.trigger_filter
        new_steps = _build_steps(spec)
        # Delete old steps first: the unit of work would otherwise INSERT new rows before
        # DELETEing old ones and trip the (definition_id, position) unique constraint.
        row.steps.clear()
        await self._session.flush()
        row.steps = new_steps
        row.version += 1  # running executions keep their pinned snapshot
        self._audit.record(
            org_id=access.org_id,
            action="workflow.updated",
            entity_type="workflow",
            entity_id=row.id,
            after={"version": row.version},
        )
        await self._session.commit()
        await self._session.refresh(row)
        return row

    @requires(Action.WORKFLOW_MANAGE)
    async def set_enabled(
        self, access: OrgAccess, definition_id: uuid.UUID, enabled: bool
    ) -> WorkflowDefinitionRow:
        row = await self._load(access, definition_id)
        row.enabled = enabled
        self._audit.record(
            org_id=access.org_id,
            action="workflow.enabled" if enabled else "workflow.disabled",
            entity_type="workflow",
            entity_id=row.id,
        )
        await self._session.commit()
        return row

    @requires(Action.WORKFLOW_READ)
    async def executions(
        self, access: OrgAccess, definition_id: uuid.UUID
    ) -> list[WorkflowExecutionRow]:
        await self._load(access, definition_id)
        return await self._executions.list_for_definition(definition_id)

    @requires(Action.WORKFLOW_READ)
    async def execution(
        self, access: OrgAccess, execution_id: uuid.UUID
    ) -> tuple[WorkflowExecutionRow, list[StepRunRow]]:
        row = await self._executions.get(execution_id)
        if row is None or row.org_id != access.org_id:
            raise NotFoundError("execution", execution_id)
        return row, await self._executions.step_runs(execution_id)

    @requires(Action.WORKFLOW_MANAGE)
    async def cancel(self, access: OrgAccess, execution_id: uuid.UUID) -> WorkflowExecutionRow:
        # FOR UPDATE: waits for a step that is currently running to finish, then cancels.
        row = await self._executions.get(execution_id, for_update=True)
        if row is None or row.org_id != access.org_id:
            raise NotFoundError("execution", execution_id)
        if row.status not in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
            raise ConflictError(f"execution is already {row.status.value}")
        row.status = ExecutionStatus.CANCELLED
        row.finished_at = datetime.now(UTC)
        self._audit.record(
            org_id=access.org_id,
            action="execution.cancelled",
            entity_type="execution",
            entity_id=row.id,
        )
        await self._session.commit()
        return row
