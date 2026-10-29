import hashlib

import pytest

from packages.tool_gateway.extensions import ExtensionError, permission_diff, validate_package
from packages.tool_gateway.policy import PolicyError, ToolPolicy, validate_endpoint
from workers.runner import TemplateRunner


def manifest():
    return {"id": "test.pkg", "version": "1.0.0", "engine_api": "1.0", "source_ref": "approved", "payload_sha256": hashlib.sha256(b"payload").hexdigest(), "signature": None, "permissions": [], "dependencies": [], "transport": "stdio", "entrypoint": "worker.py"}


def test_extension_hash_trust_dependency_lock_and_cycles():
    m = manifest()
    with pytest.raises(ExtensionError, match="TRUST"):
        validate_package(m, b"payload", {}, approved_sources=["approved"])
    with pytest.raises(ExtensionError, match="HASH"):
        validate_package(m, b"corrupt", {}, trusted=True, approved_sources=["approved"])
    m["dependencies"] = [{"id": "test.pkg", "version": "1.0.0", "payload_sha256": m["payload_sha256"]}]
    with pytest.raises(ExtensionError, match="CYCLE"):
        validate_package(m, b"payload", {"test.pkg": m}, trusted=True, approved_sources=["approved"])
    m["dependencies"] = []
    assert validate_package(m, b"payload", {}, trusted=True, approved_sources=["approved"])["status"] == "VALIDATED"


def test_permission_diff_detects_expansion_and_policy_denies_shell_and_stale():
    added = {"capability": "network", "scope": "cn.test", "approval_required": True}
    assert permission_diff([], [added])["added"] == [added]
    policy = ToolPolicy()
    context = {"project_id": "p", "revision_id": "r", "member": True, "capabilities": ["rtl.lint"], "enabled": True, "trusted": True, "healthy": True}
    request = {"project_id": "p", "revision_id": "r", "capability": "rtl.lint", "input": {"artifact_id": "a"}, "deadline": "2099-01-01T00:00:00+00:00", "approval_ids": []}
    policy.authorize(request, context)
    with pytest.raises(PolicyError):
        policy.authorize({**request, "input": {"command": "echo hello"}}, context)
    with pytest.raises(PolicyError):
        policy.authorize(request, {**context, "revision_id": "new"})
    with pytest.raises(PolicyError):
        policy.authorize(request, {**context, "trusted": False})


def test_endpoint_rejects_metadata_and_unapproved_private_dns():
    with pytest.raises(PolicyError):
        validate_endpoint("http://169.254.169.254/latest", ["169.254.169.254"])
    with pytest.raises(PolicyError):
        validate_endpoint("https://cn.test", ["cn.test"], resolver=lambda h: ["127.0.0.1"])


def test_worker_cannot_execute_without_isolation_even_when_tool_exists(tmp_path):
    result = TemplateRunner().execute("rtl.lint", tmp_path, {})
    assert result["status"] == "blocked" and result["error_code"] == "ISOLATION_UNAVAILABLE"
    assert TemplateRunner().execute("shell", tmp_path, {"command": "whoami"})["error_code"] == "TEMPLATE_NOT_ALLOWED"
