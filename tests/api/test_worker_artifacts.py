"""服务身份只能在当前租约内读取冻结输入、上传当前 attempt 的有限输出。"""

import hashlib
from datetime import timedelta
from uuid import uuid4

from apps.api.models import TaskRecord, UploadRecord, utcnow

from .test_security import open_client, setup
from .test_worker import queued_run, worker_post


def claim_job(client, csrf):
    pid, run, snapshot = queued_run(client, csrf)
    worker_id = str(uuid4())
    claim = worker_post(client, "/worker/jobs/claim", {
        "worker_id": worker_id, "capabilities": ["contract.validate"],
        "tool_profile_sha256": snapshot["toolchain_sha256"],
    })
    assert claim.status_code == 200, claim.text
    lease = claim.json()
    headers = {"Authorization": "Bearer worker-test-token", "X-Worker-ID": worker_id,
               "X-Task-Attempt": str(lease["task"]["attempt"]),
               "X-Fencing-Token": str(lease["task"]["fencing_token"])}
    return pid, run, lease, headers


def initialize(client, job_id, headers, content=b'{"checked":true}', filename="contract-validation.json", key=None):
    digest = hashlib.sha256(content).hexdigest()
    return client.post(f"/api/v1/worker/jobs/{job_id}/uploads", json={
        "filename": filename, "content_type": "application/json" if filename.endswith(".json") else "text/plain",
        "bytes": len(content), "sha256": digest,
    }, headers={**headers, "Idempotency-Key": key or str(uuid4())})


def finish(client, job_id, ticket, headers, content=b'{"checked":true}', key=None):
    put = client.put(ticket["put_path"], content=content, headers=headers)
    assert put.status_code == 204, put.text
    response = client.post(f"/api/v1/worker/jobs/{job_id}/uploads/{ticket['upload_id']}/complete",
                           json={"sha256": hashlib.sha256(content).hexdigest()},
                           headers={**headers, "Idempotency-Key": key or str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def test_task_inputs_and_uploads_are_bound_to_lease_and_fence(tmp_path):
    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        _pid, _run, lease, headers = claim_job(client, csrf)
        task = lease["task"]
        for ref in task["input_artifacts"]:
            result = client.get(f"/api/v1/worker/jobs/{task['task_id']}/artifacts/{ref['id']}/content", headers=headers)
            assert result.status_code == 200, result.text
            assert hashlib.sha256(result.content).hexdigest() == ref["sha256"]
        created = initialize(client, task["task_id"], headers, key="ticket-one")
        assert created.status_code == 201, created.text
        ticket = created.json()
        ref = finish(client, task["task_id"], ticket, headers)
        assert ref["kind"] == "tool.report"
        with client.app.state.db.transaction() as db:
            stored = db.get(UploadRecord, ticket["upload_id"])
            assert stored.job_id == task["task_id"] and stored.fencing_token == 1
            db.get(TaskRecord, task["task_id"]).fencing_token += 1
        # 旧票据的幂等响应也必须重验当前租约，不能通过缓存绕过 fence。
        assert initialize(client, task["task_id"], headers, key="ticket-one").status_code == 409
        assert client.put(ticket["put_path"], content=b'{"checked":true}', headers=headers).status_code == 409


def test_unrelated_input_missing_lease_and_human_credentials_are_denied(tmp_path):
    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        _pid, _run, lease, headers = claim_job(client, csrf)
        other_pid, other_run, other_snapshot = queued_run(client, csrf)
        unrelated = other_snapshot["input_artifacts"][0]["id"]
        path = f"/api/v1/worker/jobs/{lease['job_id']}/artifacts/{unrelated}/content"
        assert client.get(path, headers=headers).status_code == 404
        assert client.get(path).status_code == 401
        assert initialize(client, lease["job_id"], {"Authorization": "Bearer worker-test-token"}).status_code == 400
        for filename in ("../contract-validation.json", "candidate.py", "reserved-evidence.json"):
            assert initialize(client, lease["job_id"], headers, filename=filename).status_code == 415
        with client.app.state.db.transaction() as db:
            db.get(TaskRecord, lease["job_id"]).lease_expires_at = utcnow() - timedelta(seconds=1)
        assert initialize(client, lease["job_id"], headers).status_code == 409


def test_upload_hash_expiry_and_attempt_quota(tmp_path):
    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        _, _, lease, headers = claim_job(client, csrf)
        job = lease["job_id"]
        one = initialize(client, job, headers)
        assert one.status_code == 201, one.text
        ticket = one.json()
        assert client.put(ticket["put_path"], content=b"wrong", headers=headers).status_code == 422
        for _ in range(9):
            assert initialize(client, job, headers).status_code == 201
        assert initialize(client, job, headers).status_code == 413
        with client.app.state.db.transaction() as db:
            db.get(UploadRecord, ticket["upload_id"]).expires_at = utcnow() - timedelta(seconds=1)
        assert client.put(ticket["put_path"], content=b'{"checked":true}', headers=headers).status_code == 410


def test_deadline_blocks_input_success_and_renewal_but_allows_diagnostic_log(tmp_path):
    from apps.api.services.common import sha

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        _, _, lease, headers = claim_job(client, csrf)
        task = lease["task"]
        with client.app.state.db.transaction() as db:
            stored = db.get(TaskRecord, task["task_id"])
            stored.envelope = {**stored.envelope, "deadline": (utcnow() - timedelta(seconds=1)).isoformat()}
        assert initialize(client, task["task_id"], headers).status_code == 409
        assert initialize(client, task["task_id"], headers, filename="worker.log").status_code == 201
        denied = client.get(f"/api/v1/worker/jobs/{task['task_id']}/artifacts/{task['input_artifacts'][0]['id']}/content", headers=headers)
        assert denied.status_code == 409
        heartbeat = worker_post(client, f"/worker/jobs/{task['task_id']}/heartbeat", {
            "worker_id": headers["X-Worker-ID"], "attempt": 1, "fencing_token": 1, "progress_message": "late",
        })
        assert heartbeat.status_code == 409
        assert heartbeat.json()["error"]["code"] == "TASK_DEADLINE_EXCEEDED"
        result = {"schema_version": "1.0", "call_id": str(uuid4()), "task_id": task["task_id"],
                  "revision_id": task["revision_id"], "status": "passed", "tool_version": "test",
                  "input_sha256": sha(task["input_artifacts"]), "started_at": utcnow().isoformat(),
                  "finished_at": utcnow().isoformat(), "exit_code": 0, "artifact_refs": [], "log_ref": None, "summary": "late"}
        completed = worker_post(client, f"/worker/jobs/{task['task_id']}/complete", {
            "worker_id": headers["X-Worker-ID"], "attempt": 1, "fencing_token": 1, "result": result,
        })
        assert completed.status_code == 409
