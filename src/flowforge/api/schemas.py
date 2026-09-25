"""Pydantic request/response models: the HTTP contract, separate from ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from flowforge.db.models import DeliveryStatus, Role
from flowforge.domain.task import TaskStatus
from flowforge.domain.workflow import ExecutionStatus, StepType, TriggerType
from flowforge.security.permissions import Action


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RegisterIn(Schema):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)


class LoginIn(Schema):
    email: EmailStr
    password: str


class TokenOut(Schema):
    access_token: str
    token_type: str = "bearer"


class UserOut(Schema):
    id: uuid.UUID
    email: str
    full_name: str
    created_at: datetime


class OrgIn(Schema):
    name: str = Field(min_length=1, max_length=200)


class OrgOut(Schema):
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class OrgWithRoleOut(OrgOut):
    role: Role


class ProjectIn(Schema):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(Schema):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    created_at: datetime


class ProjectPatch(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class ProjectDetailOut(ProjectOut):
    archived: bool
    task_counts: dict[str, int]


class ApiKeyIn(Schema):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Action] = Field(min_length=1)


class ApiKeyOut(Schema):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreatedOut(ApiKeyOut):
    key: str = Field(description="The full key. Shown only once: store it now.")


class TaskIn(Schema):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=10_000)
    priority: int = Field(default=3, ge=1, le=5)
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None


class TaskPatch(Schema):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None


class TaskOut(Schema):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str
    status: TaskStatus
    priority: int
    assignee_id: uuid.UUID | None
    due_at: datetime | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    version: int


class MemberIn(Schema):
    email: EmailStr
    role: Role = Role.MEMBER


class MemberRoleIn(Schema):
    role: Role


class MemberOut(Schema):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role


class AuditLogOut(Schema):
    id: int
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
    created_at: datetime


class StepIn(Schema):
    type: StepType
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowIn(Schema):
    name: str = Field(min_length=1, max_length=200)
    trigger_type: TriggerType
    trigger_filter: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepIn] = Field(min_length=1, max_length=20)


class StepOut(Schema):
    position: int
    step_type: StepType
    config: dict[str, Any]


class WorkflowOut(Schema):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    trigger_type: TriggerType
    trigger_filter: dict[str, Any]
    enabled: bool
    version: int
    steps: list[StepOut]
    created_at: datetime


class StepRunOut(Schema):
    position: int
    step_type: StepType
    attempt: int
    status: ExecutionStatus
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class ExecutionOut(Schema):
    id: uuid.UUID
    definition_id: uuid.UUID
    definition_version: int
    trigger_event: dict[str, Any]
    status: ExecutionStatus
    error: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionDetailOut(ExecutionOut):
    step_runs: list[StepRunOut]


class WebhookIn(Schema):
    url: str = Field(min_length=8, max_length=2000)
    events: list[Literal["task.created", "task.status_changed"]] = Field(min_length=1)


class WebhookOut(Schema):
    id: uuid.UUID
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class WebhookCreatedOut(WebhookOut):
    secret: str = Field(description="Signing secret. Shown only once.")


class DeliveryOut(Schema):
    id: uuid.UUID
    webhook_id: uuid.UUID
    event_type: str
    status: DeliveryStatus
    attempt: int
    status_code: int | None
    last_error: str | None
    next_retry_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime


class NotificationOut(Schema):
    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class PageOut[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorOut(BaseModel):
    error: ErrorBody
