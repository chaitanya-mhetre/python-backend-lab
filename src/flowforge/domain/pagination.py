"""Pagination helpers.

``paginate`` is a generator: it yields one page at a time and never materialises the whole
input, so it works on huge or infinite iterables.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import islice


def paginate[T](items: Iterable[T], page_size: int) -> Iterator[list[T]]:
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    iterator = iter(items)
    while page := list(islice(iterator, page_size)):
        yield page
