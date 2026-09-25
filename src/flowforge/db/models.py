"""SQLAlchemy ORM models. Only repositories import these."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowforge.db.base import Base, CreatedAt, UUIDPk
from flowforge.domain.task import TaskStatus
from flowforge.domain.workflow import ExecutionStatus, StepType, TriggerType


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


def pg_enum[E: StrEnum](enum_cls: type[E], name: str) -> Enum:
    # Store enum *values* ("in_progress"), not member names ("IN_PROGRESS").
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class User(UUIDPk, CreatedAt, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))


class Organization(UUIDPk, CreatedAt, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)


class Membership(Base):
    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = mapped_column(pg_enum(Role, "member_role"))


class Project(UUIDPk, CreatedAt, Base):
    __tablename__ = "projects"
    __table_args__ = (
        # Partial index: listing active projects never scans archived ones.
        Index("ix_projects_active_org", "org_id", postgresql_where=text("archived_at IS NULL")),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskRow(UUIDPk, CreatedAt, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("priority BETWEEN 1 AND 5", name="priority_range"),
        Index("ix_tasks_project_status", "project_id", "status"),
        Index("ix_tasks_assignee_status", "assignee_id", "status"),
        Index("ix_tasks_project_created", "project_id", "created_at", "id"),
        Index("ix_tasks_open_due", "due_at", postgresql_where=text("status <> 'done'")),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[TaskStatus] = mapped_column(
        pg_enum(TaskStatus, "task_status"), default=TaskStatus.TODO
    )
    priority: Mapped[int] = mapped_column(SmallInteger, default=3)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(default=0)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_org_created", "org_id", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowDefinitionRow(UUIDPk, CreatedAt, Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (Index("ix_workflow_definitions_org_trigger", "org_id", "trigger_type"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    trigger_type: Mapped[TriggerType] = mapped_column(pg_enum(TriggerType, "trigger_type"))
    trigger_filter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    steps: Mapped[list[WorkflowStepRow]] = relationship(
        order_by="WorkflowStepRow.position",
        cascade="all, delete-orphan",
        lazy="selectin",  # steps are always needed with the definition: avoid N+1
    )


class WorkflowStepRow(UUIDPk, Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (UniqueConstraint("definition_id", "position"),)

    definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    step_type: Mapped[StepType] = mapped_column(pg_enum(StepType, "step_type"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class WorkflowExecutionRow(UUIDPk, Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        Index("ix_workflow_executions_definition_status", "definition_id", "status"),
        Index(
            "ix_workflow_executions_active",
            "status",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    # Pinned: editing a workflow never changes executions already in flight.
    definition_version: Mapped[int]
    steps_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    trigger_event: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[ExecutionStatus] = mapped_column(
        pg_enum(ExecutionStatus, "execution_status"), default=ExecutionStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StepRunRow(UUIDPk, Base):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("execution_id", "position", "attempt"),
        # At most one *successful* run per step: the database enforces step idempotency.
        Index(
            "uq_step_runs_one_success",
            "execution_id",
            "position",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_executions.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    step_type: Mapped[StepType] = mapped_column(pg_enum(StepType, "step_type"))
    attempt: Mapped[int]
    status: Mapped[ExecutionStatus] = mapped_column(pg_enum(ExecutionStatus, "execution_status"))
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(UUIDPk, CreatedAt, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "ix_notifications_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(UUIDPk, CreatedAt, Base):
    __tablename__ = "api_keys"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(8), unique=True)  # public part, used for lookup
    key_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex of the full key
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Webhook(UUIDPk, CreatedAt, Base):
    __tablename__ = "webhooks"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(2000))
    secret_encrypted: Mapped[str] = mapped_column(Text)  # Fernet token, never plaintext
    events: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    active: Mapped[bool] = mapped_column(default=True)


class WebhookDelivery(UUIDPk, CreatedAt, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_webhook_created", "webhook_id", "created_at"),)

    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[DeliveryStatus] = mapped_column(
        pg_enum(DeliveryStatus, "delivery_status"), default=DeliveryStatus.PENDING
    )
    attempt: Mapped[int] = mapped_column(default=0)
    status_code: Mapped[int | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
