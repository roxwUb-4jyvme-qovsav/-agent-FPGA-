"""常驻 Worker 走真实 API、工件下载/上传/完成，不执行 HDL 或外部模型。"""

import hashlib
import threading
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from apps.api.models import ResourceRecord, TaskRecord, utcnow

from .test_security import open_client, setup
from .test_worker import queued_run, worker_post


def test_static_worker_runs_two_jobs_and_preserves_blocked_engineering_status(tmp_path):
    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, _ = queued_run(client, csrf)
        pid2, run2, _ = queued_run(client, csrf)
        worker = StaticWorker(client, "worker-test-token", worker_id=str(uuid4()))
        assert worker.run_once() is True
        assert worker.run_once() is True
        assert worker.run_once() is False
        for project, running in ((pid, run), (pid2, run2)):
            state = client.get(f"/api/v1/projects/{project}/runs/{running['id']}").json()
            assert state["state"] == "BLOCKED"
            task = client.get(f"/api/v1/projects/{project}/runs/{running['id']}/tasks").json()["items"][0]
            assert task["state"] == "SUCCEEDED"
            assert task["result"]["status"] == "passed"
            assert task["result"]["log_ref"]
            assert task["result"]["artifact_refs"]
            ref = task["result"]["artifact_refs"][0]
            report = client.get(f"/api/v1/projects/{project}/artifacts/{ref['id']}/content")
            assert hashlib.sha256(report.content).hexdigest() == ref["sha256"]
            assert report.json()["verification_status"] == "NOT_RUN"
            assert report.json()["static_validation_status"] == "passed"


def test_expired_static_attempt_is_reconciled_and_old_fence_cannot_write(tmp_path):
    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        _, run, snapshot = queued_run(client, csrf)
        body = {"worker_id": str(uuid4()), "capabilities": ["contract.validate"],
                "tool_profile_sha256": snapshot["toolchain_sha256"]}
        first = worker_post(client, "/worker/jobs/claim", body).json()
        with client.app.state.db.transaction() as db:
            db.get(TaskRecord, first["job_id"]).lease_expires_at = utcnow() - timedelta(seconds=1)
        second = worker_post(client, "/worker/jobs/claim", {**body, "worker_id": str(uuid4())}).json()
        assert second["job_id"] == first["job_id"]
        assert second["task"]["attempt"] == 2
        assert second["task"]["fencing_token"] == 2
        stale = worker_post(client, f"/worker/jobs/{first['job_id']}/heartbeat", {
            "worker_id": body["worker_id"], "attempt": 1, "fencing_token": 1, "progress_message": "late",
        })
        assert stale.status_code == 409


def test_invalid_contract_produces_failure_report_without_running_source(tmp_path):
    from apps.api.services.artifacts import store_json
    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, _ = queued_run(client, csrf)
        # 构造受损的冻结输入夹具，保留正常 SHA256；Worker 必须验证 Schema 和作用域。
        with client.app.state.db.transaction() as db:
            task = db.scalar(select(TaskRecord).where(TaskRecord.run_id == run["id"]))
            bad = store_json(db, client.app.state.settings, pid, {"payload": "__import__('os')"}, "system_contract")
            task.envelope = {**task.envelope, "input_artifacts": [bad.payload]}
        worker = StaticWorker(client, "worker-test-token", worker_id=str(uuid4()))
        assert worker.run_once()
        task = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"][0]
        assert task["state"] == "FAILED"
        assert task["result"]["status"] == "failed"
        assert task["result"]["artifact_refs"]
        assert client.get(f"/api/v1/projects/{pid}/runs/{run['id']}").json()["state"] == "FAILED"


def test_daemon_stop_signal_exits_without_new_claims(tmp_path):
    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        setup(client)
        stop = threading.Event()
        stop.set()
        worker = StaticWorker(client, "worker-test-token", worker_id=str(uuid4()))
        worker.run_forever(stop, poll_seconds=0.01)
        with client.app.state.db.transaction() as db:
            assert not db.scalars(select(ResourceRecord).where(ResourceRecord.kind == "artifacts")).all()


def test_cancellation_during_report_upload_keeps_only_log_and_acknowledges(tmp_path):
    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, _ = queued_run(client, csrf)

        class CancelDuringUpload(StaticWorker):
            def upload(self, task, headers, filename, content):
                if filename == "contract-validation.json":
                    with client.app.state.db.transaction() as db:
                        db.get(TaskRecord, task["task_id"]).state = "CANCEL_REQUESTED"
                return super().upload(task, headers, filename, content)

        assert CancelDuringUpload(client, "worker-test-token", worker_id=str(uuid4())).run_once()
        task = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"][0]
        assert task["state"] == "CANCELLED"
        assert task["result"]["artifact_refs"] == []
        assert task["result"]["log_ref"]


def test_resident_loop_accepts_later_jobs_and_stops_cleanly(tmp_path):
    import time

    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        stop = threading.Event()
        worker = StaticWorker(client, "worker-test-token", worker_id=str(uuid4()))
        thread = threading.Thread(target=worker.run_forever, args=(stop,), kwargs={"poll_seconds": 0.02})
        thread.start()
        try:
            pid, run, _ = queued_run(client, csrf)
            deadline = time.monotonic() + 5
            state = None
            while time.monotonic() < deadline:
                items = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"]
                state = items[0]["state"]
                if state == "SUCCEEDED":
                    break
                time.sleep(0.02)
            assert state == "SUCCEEDED"
        finally:
            stop.set()
            thread.join(timeout=5)
        assert not thread.is_alive()


def test_cancel_at_final_submit_and_cancelled_worker_crash_are_reconciled(tmp_path):
    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, _ = queued_run(client, csrf)

        class CancelAtComplete(StaticWorker):
            def request(self, method, path, **kwargs):
                if path.endswith("/complete") and "/uploads/" not in path and kwargs.get("json", {}).get("result", {}).get("status") == "passed":
                    with client.app.state.db.transaction() as db:
                        db.get(TaskRecord, kwargs["json"]["result"]["task_id"]).state = "CANCEL_REQUESTED"
                return super().request(method, path, **kwargs)

        assert CancelAtComplete(client, "worker-test-token", worker_id=str(uuid4())).run_once()
        task = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"][0]
        assert task["state"] == "CANCELLED"
        _, other, snapshot = queued_run(client, csrf)
        claimed = worker_post(client, "/worker/jobs/claim", {"worker_id": str(uuid4()), "capabilities": ["contract.validate"], "tool_profile_sha256": snapshot["toolchain_sha256"]}).json()
        with client.app.state.db.transaction() as db:
            stored = db.get(TaskRecord, claimed["job_id"])
            stored.state = "CANCEL_REQUESTED"
            stored.lease_expires_at = utcnow() - timedelta(seconds=1)
        assert StaticWorker(client, "worker-test-token", worker_id=str(uuid4())).run_once()
        with client.app.state.db.transaction() as db:
            assert db.get(TaskRecord, claimed["job_id"]).state == "CANCELLED"


def test_cancel_between_checkpoint_and_input_download_is_acknowledged(tmp_path):
    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, _ = queued_run(client, csrf)

        class CancelOnDownload:
            request = client.request

            def stream(self, method, path, **kwargs):
                if "/artifacts/" in path:
                    with client.app.state.db.transaction() as db:
                        db.scalar(select(TaskRecord).where(TaskRecord.run_id == run["id"])).state = "CANCEL_REQUESTED"
                return client.stream(method, path, **kwargs)

        assert StaticWorker(CancelOnDownload(), "worker-test-token", worker_id=str(uuid4())).run_once()
        assert client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"][0]["state"] == "CANCELLED"


def test_oversized_frozen_input_gets_terminal_failure_report(tmp_path):
    from apps.api.services.artifacts import store_artifact
    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, _ = queued_run(client, csrf)
        with client.app.state.db.transaction() as db:
            task = db.scalar(select(TaskRecord).where(TaskRecord.run_id == run["id"]))
            oversized = store_artifact(db, client.app.state.settings, pid, b"x" * (4 * 1024 * 1024 + 1), "requirement")
            task.envelope = {**task.envelope, "input_artifacts": [oversized.payload]}
        assert StaticWorker(client, "worker-test-token", worker_id=str(uuid4())).run_once()
        task = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"][0]
        assert task["state"] == "FAILED" and len(task["attempts"]) == 1
        report = task["result"]["artifact_refs"][0]
        body = client.get(f"/api/v1/projects/{pid}/artifacts/{report['id']}/content").json()
        assert any(issue["code"] == "INPUT_TOO_LARGE" for issue in body["issues"])


def test_elapsed_deadline_is_timeout_instead_of_success(tmp_path):
    from workers.static_worker import StaticWorker

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, _ = queued_run(client, csrf)
        with client.app.state.db.transaction() as db:
            task = db.scalar(select(TaskRecord).where(TaskRecord.run_id == run["id"]))
            task.envelope = {**task.envelope, "deadline": (utcnow() - timedelta(seconds=1)).isoformat()}
        assert StaticWorker(client, "worker-test-token", worker_id=str(uuid4())).run_once()
        task = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"][0]
        assert task["state"] == "FAILED" and task["result"]["status"] == "timeout"
        assert task["result"]["artifact_refs"] == [] and task["result"]["log_ref"]


def test_nonstatic_cancelled_process_keeps_its_reconciliation_state(tmp_path):
    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        _, _, snapshot = queued_run(client, csrf)
        request = {"worker_id": str(uuid4()), "capabilities": ["contract.validate"],
                   "tool_profile_sha256": snapshot["toolchain_sha256"]}
        lease = worker_post(client, "/worker/jobs/claim", request).json()
        with client.app.state.db.transaction() as db:
            task = db.get(TaskRecord, lease["job_id"])
            task.capability = "rtl.simulate"
            task.state = "CANCEL_REQUESTED"
            task.lease_expires_at = utcnow() - timedelta(seconds=1)
        assert worker_post(client, "/worker/jobs/claim", request).json()["job_id"] is None
        with client.app.state.db.transaction() as db:
            assert db.get(TaskRecord, lease["job_id"]).state == "CANCEL_REQUESTED"
