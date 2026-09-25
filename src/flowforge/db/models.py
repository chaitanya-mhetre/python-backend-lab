"""SQLAlchemy ORM models. Only repositories import these."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from flowforge.db.base import Base, CreatedAt, UUIDPk
from flowforge.domain.task import TaskStatus


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
