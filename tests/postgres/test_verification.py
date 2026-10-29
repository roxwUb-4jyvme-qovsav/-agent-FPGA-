"""独立验收在真实 PostgreSQL 上的事务与服务事件测试。"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from acceptance_service.signing import sign_evidence
from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.models import EventRecord, ResourceRecord, TaskRecord, utcnow
from tests.api.test_security import setup
from tests.api.test_service_identity import claim, registry, service, service_post
from tests.api.test_verification import prepare, submit
from tests.api.test_worker import queued_run
from tests.postgres import test_concurrency as concurrency

postgres_url = concurrency.postgres_url


def test_postgres_registered_verifier_duplicate_submissions_are_atomic(postgres_url, tmp_path):
    path, keys = tmp_path / "services.json", tmp_path / "keys.json"
    registry(path)
    settings = Settings(
        database_url=postgres_url,
        data_dir=tmp_path / "data",
        allowed_origins=("http://testserver",),
        service_principals=path,
        verifier_public_keys=keys,
    )
    with TestClient(create_app(settings)) as client:
        _, _, private, headers, raw = prepare(client, path, keys)
        signed = sign_evidence(raw, private, "acceptance-1")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, client, signed, headers) for _ in range(2)]
            responses = [f.result(timeout=15) for f in futures]
        assert [r.status_code for r in responses] == [200, 200]
        assert responses[0].json() == responses[1].json()
        with client.app.state.db.transaction() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(ResourceRecord)
                    .where(ResourceRecord.id == raw["id"])
                )
                == 1
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(EventRecord)
                    .where(
                        EventRecord.type == "verification.evidence_ingested",
                        EventRecord.resource_id == raw["id"],
                    )
                )
                == 1
            )


def test_postgres_cancellation_can_lock_task_while_ingestion_waits_for_run(postgres_url, tmp_path):
    path, keys = tmp_path / "services.json", tmp_path / "keys.json"
    registry(path)
    settings = Settings(
        database_url=postgres_url,
        data_dir=tmp_path / "data",
        allowed_origins=("http://testserver",),
        service_principals=path,
        verifier_public_keys=keys,
    )
    with TestClient(create_app(settings)) as client, ThreadPoolExecutor(max_workers=1) as pool:
        _, run, private, headers, raw = prepare(client, path, keys)
        signed = sign_evidence(raw, private, "acceptance-1")
        with client.app.state.db.transaction() as cancelling:
            running = cancelling.scalar(
                select(ResourceRecord).where(ResourceRecord.id == run["id"]).with_for_update()
            )
            pending = pool.submit(submit, client, signed, headers)
            # 等待真实 PostgreSQL 锁等待出现，避免靠 sleep 猜测请求进度。
            for _ in range(200):
                with client.app.state.db.transaction() as observer:
                    waiting = observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'"
                        )
                    )
                if waiting:
                    break
                time.sleep(0.01)
            assert waiting, "ingestion did not reach the run lock"
            task = cancelling.scalar(
                select(TaskRecord)
                .where(TaskRecord.id == raw["task_id"])
                .with_for_update(nowait=True)
            )
            task.state = running.state = "CANCEL_REQUESTED"
        response = pending.result(timeout=10)
        assert response.status_code == 409, response.text
        with client.app.state.db.transaction() as db:
            assert db.get(ResourceRecord, raw["id"]) is None


def test_postgres_verifier_diagnostic_completion_locks_run_before_task(postgres_url, tmp_path):
    path, keys = tmp_path / "services.json", tmp_path / "keys.json"
    registry(path)
    settings = Settings(
        database_url=postgres_url,
        data_dir=tmp_path / "data",
        allowed_origins=("http://testserver",),
        service_principals=path,
        verifier_public_keys=keys,
    )
    with TestClient(create_app(settings)) as client, ThreadPoolExecutor(max_workers=1) as pool:
        _, run, _, headers, raw = prepare(client, path, keys)
        result = {
            name: raw[name]
            for name in (
                "schema_version",
                "task_id",
                "revision_id",
                "tool_version",
                "input_sha256",
                "started_at",
                "finished_at",
            )
        }
        result.update(
            call_id=raw["id"],
            status="failed",
            exit_code=1,
            artifact_refs=[raw["report"]],
            log_ref=None,
            summary="失败诊断",
        )
        body = {
            "worker_id": headers["X-Worker-ID"],
            "attempt": 1,
            "fencing_token": 1,
            "result": result,
        }
        with client.app.state.db.transaction() as blocking:
            blocking.scalar(
                select(ResourceRecord).where(ResourceRecord.id == run["id"]).with_for_update()
            )
            pending = pool.submit(
                service_post,
                client,
                f"/worker/jobs/{raw['task_id']}/complete",
                body,
                "verifier-token",
            )
            for _ in range(200):
                with client.app.state.db.transaction() as observer:
                    waiting = observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'"
                        )
                    )
                if waiting:
                    break
                time.sleep(0.01)
            assert waiting
            blocking.scalar(
                select(TaskRecord)
                .where(TaskRecord.id == raw["task_id"])
                .with_for_update(nowait=True)
            )
        response = pending.result(timeout=10)
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("endpoint", ["heartbeat", "complete"])
def test_postgres_previous_principal_cannot_borrow_reassigned_lease(
    postgres_url, tmp_path, endpoint
):
    path = tmp_path / "services.json"
    registry(path)
    settings = Settings(
        database_url=postgres_url,
        data_dir=tmp_path / "data",
        allowed_origins=("http://testserver",),
        service_principals=path,
    )
    with TestClient(create_app(settings)) as client, ThreadPoolExecutor(max_workers=1) as pool:
        csrf = setup(client)
        pid, _, snapshot = queued_run(client, csrf)
        first, second = str(uuid4()), str(uuid4())
        registry(path, service(first, "first-token", [pid]), service(second, "second-token", [pid]))
        lease = claim(client, first, snapshot, "first-token").json()
        job = lease["job_id"]
        body = {"worker_id": second, "attempt": 2, "fencing_token": 2}
        if endpoint == "heartbeat":
            body["progress_message"] = "borrow another lease"
        else:
            from apps.api.services.common import iso, sha

            now = iso(utcnow())
            body["result"] = {
                "schema_version": "1.0",
                "call_id": str(uuid4()),
                "task_id": job,
                "revision_id": lease["task"]["revision_id"],
                "status": "failed",
                "tool_version": "static-1",
                "input_sha256": sha(lease["task"]["input_artifacts"]),
                "started_at": now,
                "finished_at": now,
                "exit_code": 1,
                "artifact_refs": [],
                "log_ref": None,
                "summary": "borrowed failure",
            }
        with client.app.state.db.transaction() as reclaim:
            task = reclaim.scalar(select(TaskRecord).where(TaskRecord.id == job).with_for_update())
            task.worker_id, task.attempt, task.fencing_token = second, 2, 2
            task.lease_expires_at = utcnow() + timedelta(seconds=60)
            task.envelope = {**task.envelope, "attempt": 2, "fencing_token": 2}
            reclaim.flush()
            pending = pool.submit(
                service_post, client, f"/worker/jobs/{job}/{endpoint}", body, "first-token"
            )
            for _ in range(200):
                with client.app.state.db.transaction() as observer:
                    waiting = observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'"
                        )
                    )
                if waiting:
                    break
                time.sleep(0.01)
            assert waiting
        response = pending.result(timeout=10)
        assert response.status_code == 403, response.text
        with client.app.state.db.transaction() as db:
            assert db.get(TaskRecord, job).state == "LEASED"
