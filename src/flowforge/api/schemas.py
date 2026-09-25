"""Pydantic request/response models: the HTTP contract, separate from ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from flowforge.db.models import Role
from flowforge.domain.task import TaskStatus


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


class PageOut[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorOut(BaseModel):
    error: ErrorBody
