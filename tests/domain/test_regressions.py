import hashlib
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from jsonschema import ValidationError

from packages.model_gateway.provider import OpenAICompatibleProvider
from packages.tool_gateway.extensions import ExtensionError, validate_package
from packages.tool_gateway.policy import PolicyError, validate_endpoint
from workers.runner import TemplateRunner


def provider(transport, **extras):
    profile = {"id": "p", "enabled": True, "runtime_origin": "CN", "provider_kind": "openai_compatible", "base_url": "https://cn.test/v1", "model_id": "test", "capabilities": ["text", "json"], "max_output_tokens": 50, "timeout_seconds": 1, "price_input_per_million_cny": "2", "price_output_per_million_cny": "3"}
    return OpenAICompatibleProvider(profile, api_key="test", external_send_allowed=True, allowed_hosts=["cn.test"], resolver=lambda h: ["8.8.8.8"], reservation_validator=lambda r: True, transport=transport, **extras)


def request(**extras):
    return {"request_id": "i", "model_profile_version_id": "p", "messages": [{"role": "user", "content": "hi"}], "max_output_tokens": 10, "budget_reservation_id": "b", "deadline": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(), "output_schema_id": None, **extras}


def response(content="ok"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 80}, "completion_tokens_details": {"reasoning_tokens": 10}}})


def test_schema_validator_failure_is_stable_and_usage_does_not_double_count():
    def invalid(name, output):
        raise ValidationError("wrong")
    result = provider(httpx.MockTransport(lambda r: response("{}")), output_validator=invalid).generate(request(output_schema_id="test"))
    assert result["status"] == "failed" and result["error_code"] == "OUTPUT_SCHEMA_INVALID"
    assert result["actual_cost_cny"] == "0.000260"


def test_late_provider_result_does_not_succeed_after_absolute_deadline():
    def late(req):
        time.sleep(0.02)
        return response()
    result = provider(httpx.MockTransport(late)).generate(request(deadline=(datetime.now(timezone.utc) + timedelta(milliseconds=5)).isoformat()))
    assert result["status"] == "unknown" and result["error_code"] == "DEADLINE_EXCEEDED"


def test_private_ipv4_mapped_into_ipv6_does_not_bypass_metadata_guard():
    with pytest.raises(PolicyError):
        validate_endpoint("http://local.test", ["local.test"], approved_local=True, resolver=lambda h: ["::ffff:169.254.169.254"])


def test_dependencies_must_have_current_approval_and_trust():
    m = {"id": "main", "version": "1.0.0", "engine_api": "1.0", "source_ref": "s", "payload_sha256": hashlib.sha256(b"main").hexdigest(), "signature": None, "dependencies": [{"id": "dep", "version": "1.0.0", "payload_sha256": "a" * 64}]}
    dependency = {"id": "dep", "version": "1.0.0", "source_ref": "s", "payload_sha256": "a" * 64, "dependencies": [], "status": "DISABLED", "trusted": False}
    with pytest.raises(ExtensionError, match="DEPENDENCY_NOT_APPROVED"):
        validate_package(m, b"main", {"dep": dependency}, trusted=True, approved_sources=["s"])


def test_document_lifecycle_cannot_publish_unreviewed_or_revoke_back_to_published():
    from packages.knowledge.index import transition_document
    with pytest.raises(ValueError):
        transition_document("UPLOADED", "PUBLISHED")
    with pytest.raises(ValueError):
        transition_document("INDEXING", "PUBLISHED")
    assert transition_document("INDEXING", "PUBLISHED", reviewed=True, index_ready=True) == "PUBLISHED"
    assert transition_document("PUBLISHED", "REVOKED") == "REVOKED"
    with pytest.raises(ValueError):
        transition_document("REVOKED", "PUBLISHED", reviewed=True, index_ready=True)


def test_worker_rejects_inconsistent_success(tmp_path):
    class Backend:
        def health(self):
            return {"status": "READY", **{key: True for key in ("network_disabled", "secrets_absent", "process_limits", "disk_quota", "process_tree_cancel")}}
        def run(self, argv, workspace, config):
            return {"status": "completed", "exit_code": 1}
    result = TemplateRunner(Backend()).execute("rtl.lint", tmp_path, {})
    assert result["status"] == "failed" and result["error_code"] == "BACKEND_RESULT_INVALID"
