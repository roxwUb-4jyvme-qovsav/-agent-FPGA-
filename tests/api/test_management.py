import hashlib
from uuid import uuid4

from cryptography.fernet import Fernet

from tests.api.test_security import ORIGIN, open_client, project, setup, write


def install_router(client):
    from apps.api.routers.domain import router

    if "/api/v1/projects/{project_id}/knowledge/search" not in client.app.openapi()["paths"]:
        client.app.include_router(router)


def command(client, path, action, version, csrf, **parameters):
    return write(
        client,
        "post",
        path + "/commands",
        {
            "action": action,
            "expected_version": version,
            "parameters": {
                "target_version_id": None,
                "artifact_id": None,
                "approval_id": None,
                "reason": "集成测试",
                "preserve_history": True,
                **parameters,
            },
        },
        csrf,
    )


def upload(client, project_id, csrf, content=b"XC7A35T clock 100 MHz\n"):
    digest = hashlib.sha256(content).hexdigest()
    created = write(
        client,
        "post",
        f"/projects/{project_id}/uploads",
        {
            "filename": "manual.txt",
            "content_type": "text/plain",
            "bytes": len(content),
            "sha256": digest,
        },
        csrf,
    )
    assert created.status_code == 201, created.text
    ticket = created.json()["upload_id"]
    sent = client.put(
        created.json()["put_path"], content=content, headers={**ORIGIN, "X-CSRF-Token": csrf}
    )
    assert sent.status_code == 204, sent.text
    completed = write(
        client,
        "post",
        f"/projects/{project_id}/uploads/{ticket}/complete",
        {"sha256": digest},
        csrf,
    )
    assert completed.status_code == 200, completed.text
    return ticket


def test_document_real_upload_parse_publish_search_and_immediate_revoke(tmp_path):
    with open_client(tmp_path) as client:
        install_router(client)
        csrf = setup(client)
        p = project(client, csrf).json()["id"]
        uid = client.get("/api/v1/auth/session").json()["user_id"]
        collection = write(
            client,
            "post",
            f"/projects/{p}/collections",
            {
                "name": "器件资料",
                "description": "",
                "embedding_profile_version_id": None,
                "embedding_dimension": None,
                "acl_member_ids": [uid],
                "external_connection_id": None,
            },
            csrf,
        ).json()
        ticket = upload(client, p, csrf)
        document = write(
            client,
            "post",
            f"/projects/{p}/documents",
            {
                "upload_id": ticket,
                "collection_id": collection["id"],
                "scope": "project_knowledge",
                "license": "internal",
                "external_send_allowed": False,
            },
            csrf,
        ).json()
        path = f"/projects/{p}/documents/{document['id']}"
        assert command(client, path, "parse", 1, csrf).status_code == 202
        parsed = client.get("/api/v1" + path).json()
        assert parsed["state"] == "PARSED"
        assert command(client, path, "publish", parsed["version"], csrf).status_code == 202
        search = {
            "query": "XC7A35T",
            "collection_ids": [collection["id"]],
            "document_version_ids": [],
            "top_k": 5,
        }
        result = write(
            client, "post", f"/projects/{p}/knowledge/search", search, csrf, key="same-search-key"
        )
        assert result.status_code == 200, result.text
        assert (
            len(result.json()["hits"]) == 1 and result.json()["hits"][0]["locator"] == "lines:1-1"
        )
        current = client.get("/api/v1" + path).json()
        assert command(client, path, "revoke", current["version"], csrf).status_code == 202
        assert (
            write(
                client,
                "post",
                f"/projects/{p}/knowledge/search",
                search,
                csrf,
                key="same-search-key",
            ).json()["hits"]
            == []
        )
        history = client.get(f"/api/v1/system/resources/{document['id']}/versions")
        assert history.status_code == 200 and len(history.json()["items"]) >= 3


def test_model_health_does_not_spend_and_enable_stays_blocked(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        identifier = str(uuid4())
        profile = {
            "schema_version": "1.0",
            "id": identifier,
            "display_name": "测试配置",
            "provider_kind": "openai_compatible",
            "base_url": "https://not-configured.invalid/v1",
            "model_id": "unverified-model",
            "runtime_origin": "CN",
            "secret_ref": None,
            "capabilities": ["text"],
            "context_limit": 4096,
            "max_output_tokens": 32,
            "timeout_seconds": 30,
            "price_input_per_million_cny": None,
            "price_output_per_million_cny": None,
            "daily_budget_cny": "0",
            "fallback_profile_ids": [],
            "enabled": False,
        }
        created = write(client, "post", "/admin/model-profiles", profile, csrf).json()
        path = "/admin/model-profiles/" + created["id"]
        tested = command(client, path, "test", created["version"], csrf)
        assert tested.status_code == 202, tested.text
        current = client.get("/api/v1" + path).json()
        assert current["state"] == "BLOCKED"
        enabled = command(client, path, "enable", current["version"], csrf)
        assert (
            enabled.status_code == 409 and enabled.json()["error"]["code"] == "HEALTH_TEST_REQUIRED"
        )


def test_secret_ciphertext_meta_only_and_audit_never_contain_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("FPGA_SECRET_KEY", Fernet.generate_key().decode())
    with open_client(tmp_path) as client:
        install_router(client)
        csrf = setup(client)
        secret = "private-provider-value"
        created = write(
            client, "post", "/admin/secrets", {"label": "模型凭证", "value": secret}, csrf
        )
        assert created.status_code == 201, created.text
        identifier = created.json()["id"]
        assert secret not in created.text and created.json()["configured"] is True
        assert secret not in client.get(f"/api/v1/admin/secrets/{identifier}").text
        audit = client.get("/api/v1/admin/audit")
        assert audit.status_code == 200 and secret not in audit.text
        from apps.api.models import SecretRecord

        with client.app.state.db.transaction() as db:
            assert secret not in db.get(SecretRecord, identifier).ciphertext


def test_search_rejects_cross_project_collection_before_document_read(tmp_path):
    with open_client(tmp_path) as client:
        install_router(client)
        csrf = setup(client)
        p = project(client, csrf).json()["id"]
        q = project(client, csrf, "other").json()["id"]
        c = write(
            client,
            "post",
            f"/projects/{q}/collections",
            {
                "name": "private",
                "description": "",
                "embedding_profile_version_id": None,
                "embedding_dimension": None,
                "acl_member_ids": [],
                "external_connection_id": None,
            },
            csrf,
        ).json()
        result = write(
            client,
            "post",
            f"/projects/{p}/knowledge/search",
            {"query": "x", "collection_ids": [c["id"]], "document_version_ids": [], "top_k": 5},
            csrf,
        )
        assert result.status_code == 404


def test_user_disable_changes_authentication_fact_and_enable_needs_no_provider(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        user = write(
            client,
            "post",
            "/admin/users",
            {"username": "managed", "global_role": "user", "enabled": False},
            csrf,
        ).json()
        path = "/admin/users/" + user["id"]
        enabled = command(client, path, "enable", user["version"], csrf)
        assert enabled.status_code == 202, enabled.text
        from apps.api.models import User

        with client.app.state.db.transaction() as db:
            assert db.get(User, user["id"]).enabled is True
        current = client.get("/api/v1" + path).json()
        assert command(client, path, "disable", current["version"], csrf).status_code == 202
        with client.app.state.db.transaction() as db:
            assert db.get(User, user["id"]).enabled is False


def test_missing_secret_master_key_and_backup_cannot_report_success(tmp_path, monkeypatch):
    monkeypatch.delenv("FPGA_SECRET_KEY", raising=False)
    with open_client(tmp_path) as client:
        install_router(client)
        csrf = setup(client)
        result = write(client, "post", "/admin/secrets", {"label": "key", "value": "private"}, csrf)
        assert (
            result.status_code == 409
            and result.json()["error"]["code"] == "SECRET_KEY_NOT_CONFIGURED"
        )
        backup = write(client, "post", "/admin/backups", {"reason": "test"}, csrf)
        assert (
            backup.status_code == 409 and backup.json()["error"]["code"] == "BACKUP_NOT_CONFIGURED"
        )


def test_fixture_quote_compare_uses_real_decimal_calculation_and_preserves_source(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    from packages.contracts.validation import reference_json

    with open_client(tmp_path) as client:
        csrf = setup(client)
        p = project(client, csrf).json()["id"]
        offer = reference_json("examples/quote-offer.json")
        offer["observed_at"] = datetime.now(timezone.utc).isoformat()
        offer["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resource = write(client, "post", f"/projects/{p}/quotes", offer, csrf).json()
        lookup = {
            "schema_version": "1.0",
            "manufacturer": offer["manufacturer"],
            "mpn": offer["mpn"],
            "package": offer["package"],
            "quantity": 32,
            "country_class": offer["country_class"],
            "currency": offer["currency"],
            "max_lead_days": None,
        }
        ticket = upload(client, p, csrf, json.dumps(lookup).encode())
        from apps.api.models import ResourceRecord, UploadRecord

        with client.app.state.db.transaction() as db:
            artifact_id = db.get(UploadRecord, ticket).artifact_id
        result = command(
            client,
            f"/projects/{p}/quotes/{resource['id']}",
            "compare",
            resource["version"],
            csrf,
            artifact_id=artifact_id,
        )
        assert result.status_code == 202, result.text
        with client.app.state.db.transaction() as db:
            comparison = db.get(ResourceRecord, resource["id"]).internal["comparison"]
            assert comparison["buy_qty"] == 40 and comparison["total_cny"] == "70.0"
            assert (
                comparison["label"] == "测试报价"
                and comparison["source_uri"] == offer["source_uri"]
            )
