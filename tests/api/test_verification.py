"""独立验收入口的协议/权限测试，不代表真实 EDA 验收。"""

import base64
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from acceptance_service.signing import sign_evidence
from apps.api.models import ResourceRecord, TaskRecord, utcnow
from apps.api.services.common import iso, sha
from packages.contracts.validation import validate_payload

from .test_security import open_client, setup
from .test_service_identity import claim, registry, service, service_post
from .test_worker import queued_run
from .test_worker_artifacts import finish, initialize


def prepare(client, path, key_path, *, mode="static"):
    from apps.api.services.verification import register_assignment

    csrf = setup(client)
    pid, run, snapshot = queued_run(client, csrf)
    wid, issuer, job = str(uuid4()), str(uuid4()), str(uuid4())
    private = Ed25519PrivateKey.generate()
    key_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "keys": [
                    {
                        "issuer_id": issuer,
                        "key_id": "acceptance-1",
                        "public_key_base64": base64.b64encode(
                            private.public_key().public_bytes_raw()
                        ).decode(),
                        "revoked_at": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry(path, service(wid, "verifier-token", [pid], role="verifier", issuer_id=issuer))
    with client.app.state.db.transaction() as db:
        source = db.get(ResourceRecord, run["id"]).internal["snapshot"]
        template = db.query(TaskRecord).filter_by(run_id=run["id"]).one()
        envelope = {
            **template.envelope,
            "task_id": job,
            "agent_role": "independent_verifier",
            "allowed_tools": ["acceptance.verify"],
            "output_schema": "urn:fpga:contracts:VerificationEvidence:1.0",
        }
        task = TaskRecord(
            id=job,
            run_id=run["id"],
            project_id=pid,
            revision_id=source["revision_id"],
            capability="acceptance.verify",
            state="QUEUED",
            envelope=envelope,
        )
        db.add(task)
        db.flush()
        register_assignment(
            db,
            task,
            {
                "test_id": "public-contract",
                "suite_sha256": "b" * 64,
                "environment_sha256": snapshot["toolchain_sha256"],
                "tool_version": "fixture-validator-1",
                "template_id": "public-contract",
                "seed": 7,
                "mode": mode,
            },
        )
    lease_response = claim(client, wid, snapshot, "verifier-token", ["acceptance.verify"])
    assert lease_response.status_code == 200, lease_response.text
    lease = lease_response.json()
    assert lease["job_id"] == job
    headers = {
        "Authorization": "Bearer verifier-token",
        "X-Worker-ID": wid,
        "X-Task-Attempt": "1",
        "X-Fencing-Token": "1",
    }
    created = initialize(client, job, headers, filename="verification-report.json")
    assert created.status_code == 201, created.text
    report = finish(client, job, created.json(), headers)
    now = iso(utcnow())
    evidence = {
        "schema_version": "1.0",
        "id": str(uuid4()),
        "project_id": pid,
        "revision_id": lease["task"]["revision_id"],
        "task_id": job,
        "test_id": "public-contract",
        "input_sha256": sha(lease["task"]["input_artifacts"]),
        "suite_sha256": "b" * 64,
        "environment_sha256": snapshot["toolchain_sha256"],
        "tool_version": "fixture-validator-1",
        "template_id": "public-contract",
        "seed": 7,
        "status": "PASS",
        "mode": mode,
        "exit_code": 0,
        "started_at": now,
        "finished_at": now,
        "report": report,
        "issuer_id": issuer,
    }
    return pid, run, private, headers, evidence


def submit(client, evidence, headers, key=None):
    return service_post(
        client, "/verification/evidence", evidence, "verifier-token", headers=headers, key=key
    )


def test_signed_evidence_persists_and_projects_validity_without_promoting_run(tmp_path):
    path, keys = tmp_path / "services.json", tmp_path / "keys.json"
    registry(path)
    with open_client(tmp_path, service_principals=path, verifier_public_keys=keys) as client:
        pid, run, private, headers, raw = prepare(client, path, keys)
        signed = sign_evidence(raw, private, "acceptance-1")
        response = submit(client, signed, headers, "signed-once")
        assert response.status_code == 200, response.text
        validate_payload("ActionResult", response.json())
        assert response.json()["resource_id"] == raw["id"]
        assert submit(client, signed, headers, "signed-once").json() == response.json()
        assert submit(client, signed, headers).status_code == 200
        stored = client.get(f"/api/v1/projects/{pid}/evidence/{raw['id']}").json()
        assert stored["payload"] == signed
        tasks = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"]
        for task in tasks:
            validate_payload("TaskRecord", task)
        validity = client.get(f"/api/v1/projects/{pid}/evidence/{raw['id']}/validity")
        assert validity.status_code == 200, validity.text
        validate_payload("EvidenceValidity", validity.json())
        assert validity.json()["effective_status"] == "PASS"
        assert client.get(f"/api/v1/projects/{pid}/runs/{run['id']}").json()["state"] == "BLOCKED"
        assert (
            client.get(f"/api/v1/projects/{pid}/revisions/{raw['revision_id']}").json()["state"]
            != "VERIFIED"
        )
        with client.app.state.db.transaction() as db:
            task = db.get(TaskRecord, raw["task_id"])
            task.envelope = {**task.envelope, "input_artifacts": []}
        validity = client.get(f"/api/v1/projects/{pid}/evidence/{raw['id']}/validity").json()
        assert validity["effective_status"] == "STALE" and validity["signature_valid"]
        assert (
            client.get(f"/api/v1/projects/{pid}/evidence/{raw['id']}").json()["payload"] == signed
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("suite_sha256", "f" * 64),
        ("input_sha256", "e" * 64),
        ("environment_sha256", "d" * 64),
        ("seed", 8),
        ("mode", "hardware"),
        ("issuer_id", str(uuid4())),
    ],
)
def test_signed_but_unbound_claims_are_rejected(tmp_path, field, value):
    path, keys = tmp_path / "services.json", tmp_path / "keys.json"
    registry(path)
    with open_client(tmp_path, service_principals=path, verifier_public_keys=keys) as client:
        _, _, private, headers, raw = prepare(client, path, keys)
        raw[field] = value
        response = submit(client, sign_evidence(raw, private, "acceptance-1"), headers)
        assert response.status_code in (403, 422), response.text
        with client.app.state.db.transaction() as db:
            assert db.get(ResourceRecord, raw["id"]) is None


def test_evidence_conflicts_revoked_keys_and_unauthorized_reads(tmp_path):
    path, keys = tmp_path / "services.json", tmp_path / "keys.json"
    registry(path)
    with open_client(
        tmp_path, service_principals=path, verifier_public_keys=keys, worker_token="legacy"
    ) as client:
        pid, _, private, headers, raw = prepare(client, path, keys)
        signed = sign_evidence(raw, private, "acceptance-1")
        assert submit(client, signed, headers, "original").status_code == 200
        changed = sign_evidence({**raw, "status": "FAIL", "exit_code": 1}, private, "acceptance-1")
        assert submit(client, changed, headers).status_code == 409
        assert service_post(client, "/verification/evidence", signed, "legacy").status_code == 401
        assert client.post("/api/v1/verification/evidence", json=signed).status_code == 401
        other_pid, _, _ = queued_run(
            client, client.get("/api/v1/auth/session").json()["csrf_token"]
        )
        assert (
            client.get(f"/api/v1/projects/{other_pid}/evidence/{raw['id']}/validity").status_code
            == 404
        )
        # 即使签名时间早于撤销，也不能在撤销后新入库或命中旧幂等缓存。
        registration = json.loads(keys.read_text())
        registration["keys"][0]["revoked_at"] = iso(utcnow() + timedelta(seconds=1))
        keys.write_text(json.dumps(registration), encoding="utf-8")
        assert submit(client, signed, headers, "original").status_code == 403
        validity = client.get(f"/api/v1/projects/{pid}/evidence/{raw['id']}/validity").json()
        assert validity["signature_valid"]  # 历史签名仍可核验，不改写原文。


@pytest.mark.parametrize("fault", ["cancel", "expired", "fence", "report", "signature", "mock"])
def test_faults_cannot_produce_independent_pass(tmp_path, fault):
    path, keys = tmp_path / "services.json", tmp_path / "keys.json"
    registry(path)
    with open_client(tmp_path, service_principals=path, verifier_public_keys=keys) as client:
        pid, _, private, headers, raw = prepare(
            client, path, keys, mode="mock" if fault == "mock" else "static"
        )
        if fault in ("cancel", "expired", "fence", "report"):
            with client.app.state.db.transaction() as db:
                task = db.get(TaskRecord, raw["task_id"])
                if fault == "cancel":
                    task.state = "CANCEL_REQUESTED"
                elif fault == "expired":
                    task.lease_expires_at = utcnow() - timedelta(seconds=1)
                elif fault == "fence":
                    task.fencing_token += 1
                else:
                    report = db.get(ResourceRecord, raw["report"]["id"])
                    digest = report.payload["sha256"]
                    file = client.app.state.settings.data_dir / "objects" / digest[:2] / digest
                    file.write_bytes(b"tampered")
        signed = sign_evidence(raw, private, "acceptance-1")
        if fault == "signature":
            signed["status"] = "FAIL"
        result = submit(client, signed, headers)
        if fault == "mock":
            assert result.status_code == 200, result.text
            validity = client.get(f"/api/v1/projects/{pid}/evidence/{raw['id']}/validity").json()
            assert validity["effective_status"] == "INDETERMINATE"
        else:
            assert result.status_code in (403, 409, 422, 503), result.text
