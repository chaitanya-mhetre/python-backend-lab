from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import StepRunRow, WorkflowDefinitionRow, WorkflowExecutionRow
from flowforge.domain.workflow import Step, TriggerType, WorkflowDefinition


def to_domain(row: WorkflowDefinitionRow) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        trigger_type=row.trigger_type,
        trigger_filter=row.trigger_filter,
        enabled=row.enabled,
        version=row.version,
        steps=tuple(Step(s.position, s.step_type, s.config) for s in row.steps),
    )


class WorkflowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, definition_id: uuid.UUID) -> WorkflowDefinitionRow | None:
        return await self._session.get(WorkflowDefinitionRow, definition_id)

    async def add(self, row: WorkflowDefinitionRow) -> WorkflowDefinitionRow:
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_org(self, org_id: uuid.UUID) -> list[WorkflowDefinitionRow]:
        rows = await self._session.scalars(
            select(WorkflowDefinitionRow)
            .where(WorkflowDefinitionRow.org_id == org_id)
            .order_by(WorkflowDefinitionRow.created_at, WorkflowDefinitionRow.id)
        )
        return list(rows)

    async def enabled_for_trigger(
        self, org_id: uuid.UUID, trigger: TriggerType
    ) -> list[WorkflowDefinitionRow]:
        rows = await self._session.scalars(
            select(WorkflowDefinitionRow).where(
                WorkflowDefinitionRow.org_id == org_id,
                WorkflowDefinitionRow.trigger_type == trigger,
                WorkflowDefinitionRow.enabled.is_(True),
            )
        )
        return list(rows)


class ExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, execution_id: uuid.UUID, *, for_update: bool = False
    ) -> WorkflowExecutionRow | None:
        return await self._session.get(
            WorkflowExecutionRow, execution_id, with_for_update=for_update
        )

    async def list_for_definition(
        self, definition_id: uuid.UUID, limit: int = 50
    ) -> list[WorkflowExecutionRow]:
        rows = await self._session.scalars(
            select(WorkflowExecutionRow)
            .where(WorkflowExecutionRow.definition_id == definition_id)
            .order_by(WorkflowExecutionRow.created_at.desc(), WorkflowExecutionRow.id.desc())
            .limit(limit)
        )
        return list(rows)

    async def step_runs(self, execution_id: uuid.UUID) -> list[StepRunRow]:
        rows = await self._session.scalars(
            select(StepRunRow)
            .where(StepRunRow.execution_id == execution_id)
            .order_by(StepRunRow.position, StepRunRow.attempt)
        )
        return list(rows)
