
from packages.knowledge.index import KnowledgeIndex
from packages.knowledge.parsing import parse_document
from packages.quotes.pricing import price_order, price_quote, selling_price


def test_text_parser_retains_line_and_table_source_and_blocks_ocr():
    parsed = parse_document(b"Part | V\nQFN | 3.3V\n", "text/plain", source_uri="artifact:one")
    assert parsed["status"] == "PARSED"
    assert parsed["chunks"][0]["locator"] == "lines:1-2"
    assert "Part | V" in parsed["chunks"][0]["text"]
    assert parse_document(b"image", "image/png", source_uri="artifact:image")["status"] == "BLOCKED"


def doc(id, project="p", users=None, text="XC7A35T data"):
    return {"id": id, "project_id": project, "collection_id": "c", "version_id": id + ":v1", "status": "PUBLISHED", "allowed_user_ids": users or ["u"], "model_numbers": ["XC7A35T"], "source_uri": "artifact:" + id, "license": "internal", "chunks": [{"locator": "page:1", "text": text}]}


def test_acl_and_version_pre_filter_before_any_ranker_and_revoke_immediate():
    observed = []
    index = KnowledgeIndex([doc("ok"), doc("private", users=["other"]), doc("cross", project="q"), doc("old")], scorer=lambda query, chunks: observed.extend(c["document_id"] for c in chunks) or [1] * len(chunks))
    context = {"project_id": "p", "user_id": "u", "member": True}
    result = index.search(context, "XC7A35T", ["c"], ["ok:v1"])
    assert observed == ["ok"]
    assert len(result["hits"]) == 1 and result["hits"][0]["locator"] == "page:1"
    index.revoke("ok")
    assert index.search(context, "XC7A35T", ["c"], ["ok:v1"])["hits"] == []
    assert index.search({**context, "member": False}, "XC7A35T", ["c"])["hits"] == []


def offer(**updates):
    value = {"id": "part", "supplier": "vendor", "manufacturer": "m", "mpn": "p", "package": "QFN", "condition": "new", "currency": "CNY", "moq": 10, "order_multiple": 10, "price_breaks": [{"min_qty": 1, "unit_price": "2"}, {"min_qty": 40, "unit_price": "1.5"}], "stock_qty": 100, "tax_included": True, "tax_rate": None, "shipping": "10", "source_uri": "fixture:quote", "evidence_sha256": "a" * 64, "data_mode": "fixture", "expires_at": "2099-01-01T00:00:00+00:00"}
    return {**value, **updates}


def test_quote_decimal_moq_tier_and_shipping_once_per_supplier():
    line = price_quote(offer(), units=10, qty_per_unit=3, spares=2)
    assert line["need_qty"] == 32 and line["buy_qty"] == 40
    assert line["parts_cny"] == "60.0" and line["label"] == "测试报价"
    order = price_order([line, price_quote(offer(id="other"), units=1, qty_per_unit=1)])
    assert order["total_cny"] == "90.0" and order["shipping_cny"] == "10"
    assert selling_price("80", "0.2") == "100"


def test_unknown_tax_fx_stock_or_expiry_never_appears_comparable():
    line = price_quote(offer(currency="USD", tax_included=None, shipping=None, stock_qty=None), units=1, qty_per_unit=1)
    assert line["total_cny"] is None and not line["comparable"]
    assert {"FX_UNKNOWN", "TAX_UNKNOWN", "SHIPPING_UNKNOWN", "STOCK_UNKNOWN"} <= set(line["warnings"])
    expired = price_quote(offer(expires_at="2000-01-01T00:00:00+00:00"), units=1, qty_per_unit=1)
    assert "QUOTE_EXPIRED" in expired["warnings"]
