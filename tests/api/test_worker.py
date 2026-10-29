import uuid
from datetime import timedelta

from .test_security import open_client, setup, write
from .test_workflow import freeze, imported


def queued_run(client, csrf):
    pid, bundle = imported(client, csrf)
    rid = freeze(client, csrf, pid, bundle)
    run = write(
        client,
        "post",
        f"/projects/{pid}/runs",
        {"revision_id": rid, "mode": "simulate", "max_cost_cny": "0"},
        csrf,
    ).json()
    snapshot = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/snapshot").json()
    return pid, run, snapshot


def worker_post(client, path, body, key=None):
    return client.post(
        "/api/v1" + path,
        json=body,
        headers={
            "Authorization": "Bearer worker-test-token",
            "Idempotency-Key": key or str(uuid.uuid4()),
        },
    )


def test_worker_claim_is_exclusive_and_wrong_token_cannot_heartbeat(tmp_path):
    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, snapshot = queued_run(client, csrf)
        worker_id = str(uuid.uuid4())
        body = {
            "worker_id": worker_id,
            "capabilities": ["contract.validate"],
            "tool_profile_sha256": snapshot["toolchain_sha256"],
        }
        denied = client.post("/api/v1/worker/jobs/claim", json=body)
        assert denied.status_code == 401
        claimed = worker_post(client, "/worker/jobs/claim", body)
        assert claimed.status_code == 200, claimed.text
        lease = claimed.json()
        assert lease["task"]["attempt"] == 1
        assert worker_post(client, "/worker/jobs/claim", body).json()["job_id"] is None
        wrong = worker_post(
            client,
            f"/worker/jobs/{lease['job_id']}/heartbeat",
            {"worker_id": worker_id, "attempt": 1, "fencing_token": 99, "progress_message": "late"},
        )
        assert wrong.status_code == 409
        assert wrong.json()["error"]["code"] == "STALE_LEASE"


def test_expired_result_is_rejected_without_promoting_run(tmp_path):
    from apps.api.models import TaskRecord, utcnow

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, snapshot = queued_run(client, csrf)
        worker_id = str(uuid.uuid4())
        claimed = worker_post(
            client,
            "/worker/jobs/claim",
            {
                "worker_id": worker_id,
                "capabilities": ["contract.validate"],
                "tool_profile_sha256": snapshot["toolchain_sha256"],
            },
        ).json()
        task = claimed["task"]
        with client.app.state.db.transaction() as db:
            db.get(TaskRecord, claimed["job_id"]).lease_expires_at = utcnow() - timedelta(seconds=1)
        result = {
            "schema_version": "1.0",
            "call_id": str(uuid.uuid4()),
            "task_id": task["task_id"],
            "revision_id": task["revision_id"],
            "status": "passed",
            "tool_version": "test-static",
            "input_sha256": snapshot["acceptance_sha256"],
            "started_at": utcnow().isoformat(),
            "finished_at": utcnow().isoformat(),
            "exit_code": 0,
            "artifact_refs": [],
            "log_ref": None,
            "summary": "仅结构检查",
        }
        response = worker_post(
            client,
            f"/worker/jobs/{task['task_id']}/complete",
            {"worker_id": worker_id, "attempt": 1, "fencing_token": 1, "result": result},
        )
        assert response.status_code == 409, response.text
        assert client.get(f"/api/v1/projects/{pid}/runs/{run['id']}").json()["state"] != "SUCCEEDED"


def test_cancel_requires_worker_confirmation_for_leased_process(tmp_path):
    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, snapshot = queued_run(client, csrf)
        lease = worker_post(
            client,
            "/worker/jobs/claim",
            {
                "worker_id": str(uuid.uuid4()),
                "capabilities": ["contract.validate"],
                "tool_profile_sha256": snapshot["toolchain_sha256"],
            },
        ).json()
        latest = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}").json()
        cancelled = write(
            client,
            "post",
            f"/projects/{pid}/runs/{run['id']}/commands",
            {
                "action": "cancel",
                "expected_version": latest["version"],
                "parameters": {
                    "target_version_id": None,
                    "artifact_id": None,
                    "approval_id": None,
                    "reason": "用户取消",
                    "preserve_history": True,
                },
            },
            csrf,
        )
        assert cancelled.json()["state"] == "CANCEL_REQUESTED"
        status = client.get(
            f"/api/v1/worker/jobs/{lease['job_id']}/cancel-state",
            headers={"Authorization": "Bearer worker-test-token"},
        )
        assert status.json()["cancel_requested"] is True
