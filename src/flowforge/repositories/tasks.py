from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from flowforge.db.models import TaskRow
from flowforge.domain.errors import DomainError
from flowforge.domain.task import TaskStatus
from flowforge.repositories.pagination import (
    InvalidCursorError,
    Page,
    decode_cursor,
    encode_cursor,
)


class InvalidSortError(DomainError):
    code = "invalid_sort"


# Only non-null columns can be keyset-paginated simply. due_at is nullable, so it is a filter,
# not a sort key (documented trade-off; see docs/adr/0003-keyset-pagination.md).
SORTABLE: dict[str, InstrumentedAttribute[Any]] = {
    "created_at": TaskRow.created_at,
    "updated_at": TaskRow.updated_at,
    "priority": TaskRow.priority,
    "title": TaskRow.title,
}


@dataclass(frozen=True, slots=True)
class TaskFilter:
    project_id: uuid.UUID
    status: TaskStatus | None = None
    assignee_id: uuid.UUID | None = None
    due_before: datetime | None = None


def parse_sort(sort: str) -> tuple[str, bool]:
    """``"-created_at"`` -> ``("created_at", True)`` (descending)."""
    descending = sort.startswith("-")
    name = sort.removeprefix("-")
    if name not in SORTABLE:
        raise InvalidSortError(f"cannot sort by {name!r}; allowed: {', '.join(SORTABLE)}")
    return name, descending


def _cursor_value(name: str, raw: Any) -> Any:
    if name in ("created_at", "updated_at"):
        return datetime.fromisoformat(str(raw))
    if name == "priority":
        return int(raw)
    return str(raw)


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, task_id: uuid.UUID) -> TaskRow | None:
        return await self._session.get(TaskRow, task_id)

    async def add(self, task: TaskRow) -> TaskRow:
        self._session.add(task)
        await self._session.flush()
        await self._session.refresh(task)  # load server defaults (created_at, updated_at)
        return task

    async def delete(self, task: TaskRow) -> None:
        await self._session.delete(task)
        await self._session.flush()

    async def update_if_version(
        self, task_id: uuid.UUID, expected_version: int, values: dict[str, Any]
    ) -> TaskRow | None:
        """Optimistic locking: the UPDATE only matches if nobody changed the row meanwhile.

        Returns the fresh row, or None if the version did not match (a concurrent edit won).
        """
        result = await self._session.execute(
            update(TaskRow)
            .where(TaskRow.id == task_id, TaskRow.version == expected_version)
            .values(**values, version=TaskRow.version + 1)
            .returning(TaskRow)
            .execution_options(synchronize_session=False)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self._session.refresh(row)
        return row

    async def list(
        self, flt: TaskFilter, *, sort: str = "created_at", limit: int = 50, cursor: str | None
    ) -> Page[TaskRow]:
        name, descending = parse_sort(sort)
        column = SORTABLE[name]
        conditions: list[ColumnElement[bool]] = [TaskRow.project_id == flt.project_id]
        if flt.status is not None:
            conditions.append(TaskRow.status == flt.status)
        if flt.assignee_id is not None:
            conditions.append(TaskRow.assignee_id == flt.assignee_id)
        if flt.due_before is not None:
            conditions.append(TaskRow.due_at < flt.due_before)

        if cursor is not None:
            values = decode_cursor(cursor)
            if len(values) != 3 or values[0] != sort:
                raise InvalidCursorError("cursor does not belong to this sort order")
            key = tuple_(column, TaskRow.id)
            after = tuple_(_cursor_value(name, values[1]), uuid.UUID(str(values[2])))
            conditions.append(key < after if descending else key > after)

        order = (column.desc(), TaskRow.id.desc()) if descending else (column, TaskRow.id)
        # Fetch one extra row to know whether another page exists without a COUNT(*).
        rows = list(
            await self._session.scalars(
                select(TaskRow).where(*conditions).order_by(*order).limit(limit + 1)
            )
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more:
            last = rows[-1]
            next_cursor = encode_cursor([sort, getattr(last, name), last.id])
        return Page(items=rows, next_cursor=next_cursor)
