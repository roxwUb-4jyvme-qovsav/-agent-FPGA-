from datetime import datetime, timedelta, timezone

import httpx
import pytest

from packages.quotes.pricing import price_quote
from packages.tool_gateway.mcp import ControlledMCPClient, MCPError, tools_schema_hash


def test_full_repurchase_and_incremental_cash_cost_are_different():
    offer = {"id": "p", "supplier": "s", "currency": "CNY", "moq": 10, "order_multiple": 10,
             "price_breaks": [{"min_qty": 1, "unit_price": "2"}, {"min_qty": 40, "unit_price": "1.5"}],
             "stock_qty": 100, "tax_included": True, "shipping": "10", "data_mode": "fixture",
             "source_uri": "fixture:one", "evidence_sha256": "a" * 64, "observed_at": datetime.now(timezone.utc).isoformat(),
             "expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}
    line = price_quote(offer, units=10, qty_per_unit=3, spares=2, owned_qty=22)
    assert line["full_repurchase_cost_cny"] == "70.0"
    assert line["incremental_buy_qty"] == 10 and line["incremental_cash_cost_cny"] == "30"
    assert price_quote(offer, units=10, qty_per_unit=3, spares=2, owned_qty=32)["incremental_cash_cost_cny"] == "0"


def test_mcp_discovered_schema_cannot_trigger_implicit_remote_ref_fetch():
    tools = [{"name": "lint", "inputSchema": {"$ref": "https://unapproved.test/schema"}}]
    config = {"transport": "streamable_http", "endpoint": "https://mcp.test", "timeout_seconds": 1, "schema_sha256": tools_schema_hash(tools)}
    def handler(request):
        import json
        rpc = json.loads(request.content)
        if rpc["method"].startswith("notifications/"):
            return httpx.Response(202)
        result = {"protocolVersion": "2025-03-26"} if rpc["method"] == "initialize" else {"tools": tools}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result})
    client = ControlledMCPClient(config, capability_tools={}, allowed_hosts=["mcp.test"], resolver=lambda h: ["8.8.8.8"], transport=httpx.MockTransport(handler))
    with pytest.raises(MCPError, match="REMOTE_SCHEMA_REF_FORBIDDEN"):
        client.connect({"manage_connections": True, "enabled": True, "trusted": True, "healthy": True})
