from collections import OrderedDict
from typing import Generic, Hashable, TypeVar

V = TypeVar("V")


class LRUCache(Generic[V]):
    """Least-recently-used cache bounded by total cost (for pixmaps: bytes)."""

    def __init__(self, max_cost: int):
        self.max_cost = max_cost
        self._items: OrderedDict[Hashable, tuple[V, int]] = OrderedDict()
        self.total_cost = 0

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._items

    def get(self, key: Hashable, default: V | None = None) -> V | None:
        entry = self._items.get(key)
        if entry is None:
            return default
        self._items.move_to_end(key)
        return entry[0]

    def put(self, key: Hashable, value: V, cost: int = 1) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self.total_cost -= old[1]
        self._items[key] = (value, cost)
        self.total_cost += cost
        # Always keep the newest entry, even if it alone exceeds the budget.
        while self.total_cost > self.max_cost and len(self._items) > 1:
            _, (_, evicted_cost) = self._items.popitem(last=False)
            self.total_cost -= evicted_cost

    def discard(self, key: Hashable) -> None:
        entry = self._items.pop(key, None)
        if entry is not None:
            self.total_cost -= entry[1]

    def discard_where(self, predicate) -> None:
        for key in [key for key in self._items if predicate(key)]:
            self.discard(key)

    def clear(self) -> None:
        self._items.clear()
        self.total_cost = 0
