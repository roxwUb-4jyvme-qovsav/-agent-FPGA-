"""Decimal 预算账本；调用方须在数据库事务锁内加载、更新并持久化。"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping
from uuid import uuid4


class BudgetError(ValueError):
    pass


def money(value: Any) -> Decimal:
    if isinstance(value, (float, bool)):
        raise BudgetError("DECIMAL_STRING_REQUIRED")
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BudgetError("INVALID_AMOUNT") from exc
    if not amount.is_finite() or amount < 0:
        raise BudgetError("INVALID_AMOUNT")
    return amount


class BudgetLedger:
    def __init__(self, limits: Mapping[str, Any] | None = None,
                 reservations: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.limits = {key: money(value) for key, value in (limits or {}).items()}
        self.reservations = deepcopy(dict(reservations or {}))

    def _committed(self, scope: str) -> Decimal:
        return sum((money(item["actual_cost_cny"] if item["state"] == "actual" else item["reserved_cny"])
                    for item in self.reservations.values() if scope in item["scope_keys"]), Decimal(0))

    def reserve(self, invocation_id: str, scope_keys: Iterable[str], estimate: Decimal,
                price_version_id: str) -> dict[str, Any]:
        amount = money(estimate)
        scopes = sorted(set(scope_keys))
        # 全局、项目、用户、Run 额度同时检查；缺省额度是零。
        if not all(any(scope == kind or scope.startswith(kind + ":") for scope in scopes)
                   for kind in ("global", "project", "user", "run")):
            raise BudgetError("BUDGET_SCOPES_REQUIRED")
        existing = self.reservations.get(invocation_id)
        if existing:
            if existing["scope_keys"] != scopes or money(existing["reserved_cny"]) != amount or existing["price_version_id"] != price_version_id:
                raise BudgetError("INVOCATION_CONFLICT")
            return deepcopy(existing)
        for scope in scopes:
            if self._committed(scope) + amount > self.limits.get(scope, Decimal(0)):
                raise BudgetError(f"BUDGET_EXCEEDED: {scope}")
        item = {"id": str(uuid4()), "invocation_id": invocation_id, "scope_keys": scopes,
                "reserved_cny": str(amount), "actual_cost_cny": None, "state": "reserved", "price_version_id": price_version_id}
        self.reservations[invocation_id] = item
        return deepcopy(item)

    def settle(self, invocation_id: str, actual_cost: Decimal | None) -> dict[str, Any]:
        if invocation_id not in self.reservations:
            raise BudgetError("RESERVATION_NOT_FOUND")
        item = self.reservations[invocation_id]
        actual = money(actual_cost) if actual_cost is not None else None
        if item["state"] == "actual":
            if actual is None or money(item["actual_cost_cny"]) != actual:
                raise BudgetError("SETTLEMENT_CONFLICT")
            return deepcopy(item)
        # 未知消费保留整个预留；后续真实对账可将 unknown 转成 actual。
        item.update(state="unknown" if actual is None else "actual", actual_cost_cny=None if actual is None else str(actual))
        return deepcopy(item)

    def reservation_valid(self, reservation_id: str) -> bool:
        return any(item["id"] == reservation_id and item["state"] == "reserved" for item in self.reservations.values())

    def to_dict(self) -> dict[str, Any]:
        return {"limits": {key: str(value) for key, value in self.limits.items()}, "reservations": deepcopy(self.reservations)}
