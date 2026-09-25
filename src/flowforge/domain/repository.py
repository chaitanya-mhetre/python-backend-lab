"""Repository abstraction used by the pure domain layer.

``Repository`` is a :class:`typing.Protocol`: anything with these methods *is* a repository
(structural typing). No inheritance needed, which keeps fakes in tests trivial.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Protocol, TypeVar
from uuid import UUID

from flowforge.domain.errors import NotFoundError


class HasId(Protocol):
    @property
    def id(self) -> UUID: ...


T = TypeVar("T", bound=HasId)


class Repository(Protocol[T]):
    def add(self, item: T) -> None: ...
    def get(self, item_id: UUID) -> T: ...
    def list(self) -> list[T]: ...
    def remove(self, item_id: UUID) -> None: ...


class InMemoryRepository[E: HasId]:
    """Dict-backed repository. Satisfies ``Repository[E]`` without inheriting from it."""

    def __init__(self, entity_name: str = "entity") -> None:
        self._items: dict[UUID, E] = {}
        self._entity_name = entity_name

    def add(self, item: E) -> None:
        self._items[item.id] = item

    def get(self, item_id: UUID) -> E:
        try:
            return self._items[item_id]
        except KeyError:
            raise NotFoundError(self._entity_name, item_id) from None

    def list(self) -> list[E]:
        return list(self._items.values())

    def remove(self, item_id: UUID) -> None:
        self.get(item_id)
        del self._items[item_id]

    def find(self, predicate: Callable[[E], bool]) -> Iterator[E]:
        """Lazily yield matching items (a generator, nothing is copied up front)."""
        return (item for item in self._items.values() if predicate(item))

    def __len__(self) -> int:
        return len(self._items)
