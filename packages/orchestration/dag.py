"""声明式依赖图；边指向当前节点依赖的上游节点。"""
from collections import defaultdict
from heapq import heapify, heappop, heappush
from typing import Iterable, Mapping


class GraphError(ValueError):
    pass


class DependencyGraph:
    def __init__(self, dependencies: Mapping[str, Iterable[str]]) -> None:
        self.dependencies = {key: frozenset(value) for key, value in dependencies.items()}
        self.children: dict[str, set[str]] = defaultdict(set)
        for node, parents in self.dependencies.items():
            for parent in parents:
                if parent not in self.dependencies:
                    raise GraphError(f"UNKNOWN_DEPENDENCY: {node} -> {parent}")
                self.children[parent].add(node)
        self._order = self._sort()

    def _sort(self) -> tuple[str, ...]:
        remaining = {node: len(parents) for node, parents in self.dependencies.items()}
        ready = [node for node, degree in remaining.items() if degree == 0]
        heapify(ready)
        result: list[str] = []
        # 同时就绪的节点用稳定字典序，避免映射输入顺序改变重建计划。
        while ready:
            node = heappop(ready)
            result.append(node)
            for child in sorted(self.children[node]):
                remaining[child] -= 1
                if remaining[child] == 0:
                    heappush(ready, child)
        if len(result) != len(remaining):
            cyclic = sorted(node for node, degree in remaining.items() if degree)
            raise GraphError(f"DEPENDENCY_CYCLE: {','.join(cyclic)}")
        return tuple(result)

    def topological_order(self) -> list[str]:
        return list(self._order)

    def invalidate(self, changed: Iterable[str]) -> dict[str, list[str]]:
        reasons: dict[str, set[str]] = defaultdict(set)
        for origin in sorted(set(changed)):
            if origin not in self.dependencies:
                raise GraphError(f"UNKNOWN_NODE: {origin}")
            pending = [origin]
            visited: set[str] = set()
            while pending:
                node = pending.pop()
                if node in visited:
                    continue
                visited.add(node)
                reasons[node].add(origin)
                pending.extend(self.children[node])
        return {node: sorted(reasons[node]) for node in self._order if node in reasons}

    def rebuild_plan(self, changed: Iterable[str]) -> list[str]:
        return list(self.invalidate(changed))


def can_reuse(previous: Mapping[str, object], current: Mapping[str, object]) -> bool:
    """复用要求所有相关指纹齐全且相等；授权撤销后立即不可复用。"""
    keys = ("artifact_sha256", "test_sha256", "toolchain_sha256", "generator_sha256", "config_sha256", "input_sha256")
    return bool(current.get("authorized") and previous.get("authorized")) and all(
        previous.get(key) is not None and previous[key] == current.get(key) for key in keys
    )
