"""采购数量、阶梯价及同供应商运费合并全部使用 Decimal。"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


class QuoteError(ValueError):
    pass


def _decimal(value: Any) -> Decimal:
    if isinstance(value, (float, bool)):
        raise QuoteError("DECIMAL_STRING_REQUIRED")
    try:
        result = Decimal(value)
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise QuoteError("INVALID_DECIMAL") from exc
    if not result.is_finite() or result < 0:
        raise QuoteError("INVALID_DECIMAL")
    return result


def _number(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def price_quote(offer: Mapping[str, Any], *, units: int, qty_per_unit: int, spares: int = 0,
                fx_rates: Mapping[str, Any] | None = None, now: datetime | None = None,
                live_authorized: bool = False, owned_qty: int = 0) -> dict[str, Any]:
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (units, qty_per_unit, spares, owned_qty)):
        raise QuoteError("INVALID_QUANTITY")
    moq, multiple = offer["moq"], offer["order_multiple"]
    if not isinstance(moq, int) or not isinstance(multiple, int) or moq < 1 or multiple < 1:
        raise QuoteError("INVALID_ORDER_CONSTRAINT")
    need = units * qty_per_unit + spares
    buy = multiple * ((max(need, moq) + multiple - 1) // multiple) if need else 0
    tiers = [tier for tier in offer["price_breaks"] if isinstance(tier["min_qty"], int) and 0 < tier["min_qty"] <= buy]
    warnings: list[str] = []
    price = _decimal(max(tiers, key=lambda tier: tier["min_qty"])["unit_price"]) if tiers else Decimal(0) if buy == 0 else None
    if price is None:
        warnings.append("NO_APPLICABLE_PRICE_BREAK")
    parts_native = price * buy if price is not None else None
    fx_value = Decimal(1) if offer["currency"] == "CNY" else (fx_rates or {}).get(offer["currency"])
    fx_version = None
    if isinstance(fx_value, Mapping):
        fx_version, fx_value = fx_value.get("version_id"), fx_value.get("rate")
    fx = _decimal(fx_value) if fx_value is not None else None
    if fx is not None and fx <= 0:
        raise QuoteError("INVALID_FX_RATE")
    if fx is None:
        warnings.append("FX_UNKNOWN")
    parts = parts_native * fx if parts_native is not None and fx is not None else None
    included, rate = offer.get("tax_included"), offer.get("tax_rate")
    tax = Decimal(0) if included is True else parts * _decimal(rate) if included is False and rate is not None and parts is not None else None
    if tax is None:
        warnings.append("TAX_UNKNOWN")
    shipping = _decimal(offer["shipping"]) * fx if offer.get("shipping") is not None and fx is not None else None
    if shipping is None:
        warnings.append("SHIPPING_UNKNOWN")
    stock = offer.get("stock_qty")
    if stock is None:
        warnings.append("STOCK_UNKNOWN")
    elif stock < buy:
        warnings.append("STOCK_INSUFFICIENT")
    try:
        expiry = datetime.fromisoformat(offer["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= (now or datetime.now(timezone.utc)):
            warnings.append("QUOTE_EXPIRED")
    except (KeyError, TypeError, ValueError):
        warnings.append("QUOTE_EXPIRY_UNKNOWN")
    mode = offer.get("data_mode")
    labels = {"fixture": "测试报价", "authorized_import": "导入报价", "live": "实时报价"}
    if mode not in labels:
        warnings.append("SOURCE_MODE_UNKNOWN")
    if mode == "live" and not live_authorized:
        warnings.append("LIVE_SOURCE_NOT_AUTHORIZED")
    if not offer.get("source_uri") or not offer.get("evidence_sha256"):
        warnings.append("SOURCE_EVIDENCE_MISSING")
    total = parts + tax + shipping if all(value is not None for value in (parts, tax, shipping)) else None
    # 完整重购和库存抵扣的增量需求分别记录，不能混成一种成本口径。
    incremental_need = max(0, need - owned_qty)
    incremental_total, incremental_buy = total, buy
    if owned_qty > 0:
        if incremental_need == 0:
            incremental_total, incremental_buy = Decimal(0), 0
        else:
            incremental = price_quote(offer, units=1, qty_per_unit=incremental_need, fx_rates=fx_rates,
                                      now=now, live_authorized=live_authorized)
            incremental_total = _decimal(incremental["total_cny"]) if incremental["total_cny"] is not None else None
            incremental_buy = incremental["buy_qty"]
    return {"offer_id": offer.get("id"), "supplier": offer["supplier"], "product_key": [offer.get(key) for key in ("manufacturer", "mpn", "sku", "package", "condition")],
            "need_qty": need, "buy_qty": buy, "incremental_need_qty": incremental_need, "owned_qty": owned_qty,
            "incremental_buy_qty": incremental_buy, "full_repurchase_cost_cny": _number(total), "incremental_cash_cost_cny": _number(incremental_total),
            "unit_price": _number(price), "currency": offer["currency"], "parts_native": _number(parts_native),
            "parts_cny": _number(parts), "tax_cny": _number(tax), "shipping_cny": _number(shipping), "total_cny": _number(total),
            "fx_rate": _number(fx), "fx_version_id": fx_version, "comparable": not warnings and total is not None,
            "warnings": warnings, "data_mode": mode, "label": labels.get(mode, "来源未知"),
            "source_uri": offer.get("source_uri"), "evidence_sha256": offer.get("evidence_sha256"),
            "observed_at": offer.get("observed_at"), "expires_at": offer.get("expires_at")}


def price_order(lines: Iterable[Mapping[str, Any]], *, supplier_shipping: Mapping[str, Any] | None = None) -> dict[str, Any]:
    items = list(lines)
    known_parts = sum((_decimal(item["parts_cny"]) for item in items if item.get("parts_cny") is not None), Decimal(0))
    known_tax = sum((_decimal(item["tax_cny"]) for item in items if item.get("tax_cny") is not None), Decimal(0))
    warnings: list[str] = []
    shipping_sum = Decimal(0)
    for supplier in sorted({item["supplier"] for item in items}):
        charges = {item.get("shipping_cny") for item in items if item["supplier"] == supplier}
        override = (supplier_shipping or {}).get(supplier)
        if override is not None:
            shipping_sum += _decimal(override)
        elif None in charges:
            warnings.append(f"SHIPPING_UNKNOWN:{supplier}")
        elif len({_decimal(value) for value in charges}) != 1:
            warnings.append(f"SHIPPING_CONFLICT:{supplier}")
        else:
            shipping_sum += _decimal(next(iter(charges)))
    complete = bool(items) and not warnings and all(item.get("parts_cny") is not None and item.get("tax_cny") is not None for item in items)
    return {"parts_cny": _number(known_parts), "tax_cny": _number(known_tax), "shipping_cny": None if warnings else _number(shipping_sum),
            "known_subtotal_cny": _number(known_parts + known_tax), "total_cny": _number(known_parts + known_tax + shipping_sum) if complete else None,
            "comparable": complete and all(item.get("comparable") for item in items), "warnings": warnings}


def selling_price(cost: Any, gross_margin: Any) -> str:
    margin = _decimal(gross_margin)
    if margin >= 1:
        raise QuoteError("GROSS_MARGIN_OUT_OF_RANGE")
    return _number(_decimal(cost) / (1 - margin))
