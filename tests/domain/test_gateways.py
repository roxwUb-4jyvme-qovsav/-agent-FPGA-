from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from packages.model_gateway.budget import BudgetError, BudgetLedger
from packages.model_gateway.provider import OpenAICompatibleProvider


def test_budget_defaults_zero_and_scope_checks_are_atomic():
    ledger = BudgetLedger()
    with pytest.raises(BudgetError, match="BUDGET"):
        ledger.reserve("call", ["global", "project:p", "user:u", "run:r"], Decimal("0.01"), "p1")
    assert ledger.to_dict()["reservations"] == {}


def test_budget_unknown_retains_hold_and_idempotency_rejects_changes():
    ledger = BudgetLedger({"global": "10", "project:p": "10", "user:u": "10", "run:r": "2"})
    scopes = ["global", "project:p", "user:u", "run:r"]
    reservation = ledger.reserve("call", scopes, Decimal("1.20"), "p1")
    assert ledger.reserve("call", scopes, Decimal("1.20"), "p1") == reservation
    with pytest.raises(BudgetError, match="CONFLICT"):
        ledger.reserve("call", scopes, Decimal("1.21"), "p1")
    assert ledger.settle("call", None)["state"] == "unknown"
    with pytest.raises(BudgetError):
        ledger.reserve("call2", scopes, Decimal("1"), "p1")
    ledger.settle("call", Decimal("0.80"))
    assert ledger.reserve("call2", scopes, Decimal("1"), "p1")["reserved_cny"] == "1"


def profile():
    return {"id": "profile-v1", "enabled": True, "runtime_origin": "CN", "provider_kind": "openai_compatible", "base_url": "https://cn.test/v1", "model_id": "registered-model", "capabilities": ["text", "json"], "max_output_tokens": 128, "timeout_seconds": 2, "price_input_per_million_cny": "1", "price_output_per_million_cny": "2"}


def request():
    return {"request_id": "call", "model_profile_version_id": "profile-v1", "messages": [{"role": "user", "content": "hi", "artifact_refs": []}], "max_output_tokens": 32, "deadline": (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat(), "budget_reservation_id": "reservation", "output_schema_id": None}


def provider(handler, p=None):
    return OpenAICompatibleProvider(p or profile(), api_key="test", allowed_hosts=["cn.test"], external_send_allowed=True, reservation_validator=lambda reservation_id: True, resolver=lambda host: ["8.8.8.8"], transport=httpx.MockTransport(handler))


def test_provider_disabled_by_default_and_capabilities_checked_before_transport():
    calls = []
    instance = OpenAICompatibleProvider(profile(), transport=httpx.MockTransport(lambda r: calls.append(r)))
    assert instance.generate(request())["error_code"] == "PROVIDER_NOT_CONFIGURED"
    assert not calls
    p = profile()
    p["capabilities"] = []
    assert provider(lambda r: calls.append(r), p).generate(request())["error_code"] == "CAPABILITY_UNSUPPORTED"
    assert not calls


def test_provider_missing_usage_remains_unknown_and_identity_is_preserved():
    result = provider(lambda r: httpx.Response(200, json={"id": "server-id", "model": "actual-variant", "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]})).generate(request())
    assert result["status"] == "completed"
    assert result["requested_model"] == "registered-model"
    assert result["actual_model"] == "actual-variant"
    assert result["input_tokens"] is None and result["output_tokens"] is None
    assert result["actual_cost_cny"] is None and result["cost_state"] == "unknown"


@pytest.mark.parametrize("status,code", [(401, "AUTHENTICATION_FAILED"), (403, "PERMISSION_DENIED"), (429, "RATE_LIMITED"), (503, "PROVIDER_UNAVAILABLE")])
def test_provider_http_errors_have_stable_codes(status, code):
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(status, json={"error": {"message": "private"}})
    assert provider(handler).generate(request())["error_code"] == code
    assert len(calls) <= 3


def test_provider_deadline_and_timeout_never_claim_success():
    req = request()
    req["deadline"] = "2000-01-01T00:00:00+00:00"
    assert provider(lambda r: pytest.fail("transport must not run")).generate(req)["error_code"] == "DEADLINE_EXCEEDED"
    def timeout(r):
        raise httpx.ReadTimeout("uncertain charge")
    result = provider(timeout).generate(request())
    assert result["status"] == "unknown" and result["cost_state"] == "unknown"


@pytest.mark.parametrize("message,finish,status,code", [({"content": ""}, "stop", "failed", "EMPTY_OUTPUT"), ({"content": "partial"}, "length", "incomplete", "OUTPUT_TRUNCATED"), ({"refusal": "no"}, "stop", "refused", "MODEL_REFUSED")])
def test_provider_semantic_failure_is_not_transport_success(message, finish, status, code):
    result = provider(lambda r: httpx.Response(200, json={"choices": [{"message": message, "finish_reason": finish}]})).generate(request())
    assert (result["status"], result["error_code"]) == (status, code)

