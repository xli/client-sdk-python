# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from threading import Lock
from typing import List, Dict, Type, Callable
from .models import T
from ... import utils


class NotFoundError(ValueError):
    pass


@dataclass
class InMemory:
    """InMemory is a simple in-memory store for all resources"""

    resources: Dict[Type[T], List[T]] = field(default_factory=dict)  # pyre-ignore
    transaction: Lock = field(default_factory=Lock)

    def find_all(self, typ: Type[T], **conditions) -> List[T]:  # pyre-ignore
        return [res for res in self._table(typ) if self._match(res, **conditions)]

    def find(self, typ: Type[T], last=False, **conditions) -> T:  # pyre-ignore
        ret = self.find_all(typ, **conditions)
        if len(ret) == 1:
            return ret[0]
        elif last and ret:
            return ret[-1]
        raise NotFoundError("%s not found by %s in %s" % (typ.resource_name(), conditions, self._table(typ)))

    def create(self, typ: Type[T], before_create: Callable[[T], None] = lambda _: None, **data) -> T:  # pyre-ignore
        obj = typ(id=self.next_id(), **data)
        before_create(obj)
        self._table(typ).append(obj)
        return obj

    def next_id(self) -> str:
        return str(sum([len(i) for i in self.resources.values()]) + 1)

    def _table(self, typ: Type[T]) -> List[T]:
        return self.resources.setdefault(typ, [])

    def _match(self, res: T, **conditions) -> bool:  # pyre-ignore
        for k, v in conditions.items():
            if getattr(res, k) != v:
                return False
        return True
