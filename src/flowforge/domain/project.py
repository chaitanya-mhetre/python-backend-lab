from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass
class Project:
    org_id: UUID
    name: str
    id: UUID = field(default_factory=uuid4)
    archived: bool = False

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name must not be blank")
