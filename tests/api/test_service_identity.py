"""注册的服务不能借用别人的身份、项目、能力或租约。"""

import hashlib
import json
from uuid import uuid4

from apps.api.models import TaskRecord

from .test_security import open_client, setup
from .test_worker import queued_run


def service(worker_id, token, project_ids, *, role="worker", issuer_id=None):
    return {
        "worker_id": worker_id,
        "role": role,
        "enabled": True,
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "project_ids": project_ids,
        "capabilities": ["acceptance.verify" if role == "verifier" else "contract.validate"],
        "issuer_id": issuer_id,
    }


def registry(path, *principals):
    path.write_text(
        json.dumps({"schema_version": "1.0", "principals": principals}), encoding="utf-8"
    )


def service_post(client, path, body, token, *, key=None, headers=None):
    return client.post(
        "/api/v1" + path,
        json=body,
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": key or str(uuid4()),
            **(headers or {}),
        },
    )


def claim(client, worker_id, snapshot, token, capabilities=None, *, key=None):
    return service_post(
        client,
        "/worker/jobs/claim",
        {
            "worker_id": worker_id,
            "capabilities": capabilities or ["contract.validate"],
            "tool_profile_sha256": snapshot["toolchain_sha256"],
        },
        token,
        key=key,
    )


def test_registered_worker_is_bound_to_identity_project_and_capabilities(tmp_path):
    path = tmp_path / "services.json"
    registry(path)
    wid, other = str(uuid4()), str(uuid4())
    with open_client(tmp_path, service_principals=path, worker_token="legacy") as client:
        csrf = setup(client)
        pid, _, snapshot = queued_run(client, csrf)
        other_pid, _, _ = queued_run(client, csrf)
        registry(path, service(wid, "scoped-token", [pid]))
        assert claim(client, other, snapshot, "scoped-token").status_code == 403
        assert (
            claim(client, wid, snapshot, "scoped-token", ["acceptance.verify"]).status_code == 403
        )
        assert claim(client, wid, snapshot, "legacy").status_code == 401
        lease = claim(client, wid, snapshot, "scoped-token").json()
        assert lease["task"]["project_id"] == pid
        assert claim(client, wid, snapshot, "scoped-token").json()["job_id"] is None
        with client.app.state.db.transaction() as db:
            task = db.get(TaskRecord, lease["job_id"])
            assert task.project_id != other_pid


def test_registered_worker_cannot_borrow_lease_and_revocation_applies_to_cached_claim(tmp_path):
    path = tmp_path / "services.json"
    registry(path)
    wid, other = str(uuid4()), str(uuid4())
    with open_client(tmp_path, service_principals=path) as client:
        csrf = setup(client)
        pid, _, snapshot = queued_run(client, csrf)
        first, second = service(wid, "first-token", [pid]), service(other, "second-token", [pid])
        registry(path, first, second)
        lease = claim(client, wid, snapshot, "first-token", key="claim-once").json()
        headers = {
            "Authorization": "Bearer second-token",
            "X-Worker-ID": wid,
            "X-Task-Attempt": "1",
            "X-Fencing-Token": "1",
        }
        ref = lease["task"]["input_artifacts"][0]
        assert (
            client.get(
                f"/api/v1/worker/jobs/{lease['job_id']}/artifacts/{ref['id']}/content",
                headers=headers,
            ).status_code
            == 403
        )
        heartbeat = {
            "worker_id": wid,
            "attempt": 1,
            "fencing_token": 1,
            "progress_message": "check",
        }
        assert (
            service_post(
                client, f"/worker/jobs/{lease['job_id']}/heartbeat", heartbeat, "second-token"
            ).status_code
            == 403
        )
        first["project_ids"] = [str(uuid4())]
        registry(path, first, second)
        assert claim(client, wid, snapshot, "first-token", key="claim-once").status_code == 403
        first["enabled"] = False
        registry(path, first, second)
        assert claim(client, wid, snapshot, "first-token", key="claim-once").status_code == 401
        path.write_text("invalid-json", encoding="utf-8")
        assert claim(client, other, snapshot, "second-token").status_code == 503
