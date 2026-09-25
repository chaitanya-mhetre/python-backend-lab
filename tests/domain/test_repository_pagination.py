from collections.abc import Iterator
from itertools import count
from uuid import uuid4

import pytest

from flowforge.domain.errors import NotFoundError
from flowforge.domain.pagination import paginate
from flowforge.domain.project import Project
from flowforge.domain.repository import InMemoryRepository, Repository


def test_in_memory_repository_satisfies_protocol() -> None:
    repo: Repository[Project] = InMemoryRepository[Project]("project")  # checked by mypy
    project = Project(org_id=uuid4(), name="Alpha")
    repo.add(project)
    assert repo.get(project.id) is project
    assert repo.list() == [project]
    repo.remove(project.id)
    with pytest.raises(NotFoundError):
        repo.get(project.id)


def test_find_is_lazy_and_filters() -> None:
    repo = InMemoryRepository[Project]()
    org = uuid4()
    for name in ["a", "b", "c"]:
        repo.add(Project(org_id=org, name=name, archived=name == "b"))
    found = repo.find(lambda p: not p.archived)
    assert isinstance(found, Iterator)
    assert sorted(p.name for p in found) == ["a", "c"]


def test_paginate_splits_into_pages() -> None:
    assert list(paginate(range(7), 3)) == [[0, 1, 2], [3, 4, 5], [6]]
    assert list(paginate([], 3)) == []


def test_paginate_works_on_infinite_iterables() -> None:
    pages = paginate(count(), 2)
    assert next(pages) == [0, 1]
    assert next(pages) == [2, 3]


def test_paginate_rejects_bad_page_size() -> None:
    with pytest.raises(ValueError):
        list(paginate([1], 0))
