"""真实 PostgreSQL 多进程 API 与事件事务测试；每次使用独立临时 schema。"""

import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import make_url

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.models import ResourceRecord, TaskRecord, utcnow
from apps.api.services.common import event
from tests.api.test_events import latest, messages, tail
from tests.api.test_security import setup
from tests.api.test_worker import queued_run, worker_post


@pytest.fixture
def postgres_url():
    configured = os.getenv("FPGA_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("NOT_RUN: FPGA_TEST_POSTGRES_URL 未登记")
    url = make_url(configured)
    if url.get_backend_name() != "postgresql" or not url.database or not url.database.startswith("fpga_test_"):
        raise ValueError("并发测试仅允许专用 fpga_test_ 数据库")
    import psycopg
    from psycopg import sql

    schema = "t_" + uuid4().hex
    connection = {"host": url.host, "port": url.port, "user": url.username,
                  "password": url.password, "dbname": url.database, "autocommit": True}
    with psycopg.connect(**connection) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield url.update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(**connection) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def client_for(url: str, directory: str):
    return TestClient(create_app(Settings(database_url=url, data_dir=Path(directory),
                                         allowed_origins=("http://testserver",), worker_token="worker-test-token")))


def process_claim(url: str, directory: str, profile: str, rounds: int):
    with client_for(url, directory) as client:
        results = []
        for _ in range(rounds):
            response = worker_post(client, "/worker/jobs/claim", {
                "worker_id": str(uuid4()), "capabilities": ["contract.validate"], "tool_profile_sha256": profile,
            })
            if response.status_code != 200:
                return {"status": response.status_code}
            if response.json()["job_id"]:
                result = response.json()
                results.append((result["job_id"], result["task"]["attempt"], result["task"]["fencing_token"]))
        return {"status": 200, "claims": results}


def test_postgres_processes_claim_one_job_exclusively(postgres_url, tmp_path):
    with client_for(postgres_url, str(tmp_path)) as client:
        csrf = setup(client)
        _, _, snapshot = queued_run(client, csrf)
        with ProcessPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(process_claim, postgres_url, str(tmp_path), snapshot["toolchain_sha256"], 1) for _ in range(6)]
            results = [job.result(timeout=30) for job in jobs]
        assert all(row["status"] == 200 for row in results), results
        leases = [lease for row in results for lease in row["claims"]]
        assert len(leases) == 1


def test_postgres_multi_project_expiry_and_reclaim_have_no_duplicate_fences(postgres_url, tmp_path):
    from datetime import timedelta

    with client_for(postgres_url, str(tmp_path)) as client:
        csrf = setup(client)
        for _ in range(8):
            _, _, snapshot = queued_run(client, csrf)
        profile = snapshot["toolchain_sha256"]
        process_claim(postgres_url, str(tmp_path), profile, 4)
        with client.app.state.db.transaction() as db:
            for task in db.scalars(select(TaskRecord).where(TaskRecord.state == "LEASED")):
                task.lease_expires_at = utcnow() - timedelta(seconds=1)
        with ProcessPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(process_claim, postgres_url, str(tmp_path), profile, 4) for _ in range(4)]
            results = [job.result(timeout=40) for job in jobs]
        assert all(row["status"] == 200 for row in results), results
        leases = [lease for row in results for lease in row["claims"]]
        assert len(leases) == len(set(leases))
        assert any(attempt == 2 and fence == 2 for _, attempt, fence in leases)


def test_postgres_static_worker_download_upload_and_complete(postgres_url, tmp_path):
    from workers.static_worker import StaticWorker

    with client_for(postgres_url, str(tmp_path)) as client:
        csrf = setup(client)
        pid, run, _ = queued_run(client, csrf)
        assert StaticWorker(client, "worker-test-token", worker_id=str(uuid4())).run_once()
        task = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/tasks").json()["items"][0]
        assert task["state"] == "SUCCEEDED" and task["result"]["log_ref"]
        assert client.get(f"/api/v1/projects/{pid}/runs/{run['id']}").json()["state"] == "BLOCKED"


def test_postgres_event_commit_order_is_not_skipped_by_cursor(postgres_url, tmp_path):
    import threading

    with client_for(postgres_url, str(tmp_path)) as client:
        csrf = setup(client)
        pid, _, _ = queued_run(client, csrf)
        cursor = latest(client, pid)
        inserted, release = threading.Event(), threading.Event()

        def append(name, hold=False):
            with client.app.state.db.transaction() as db:
                record = db.get(ResourceRecord, pid)
                row = event(db, "test", name, record, pid, {"request_id": str(uuid4())})
                if hold:
                    inserted.set()
                    assert release.wait(5)
                return row.id

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(append, "test.first", True)
            assert inserted.wait(5)
            second = pool.submit(append, "test.second")
            try:
                time.sleep(0.15)
                assert not second.done(), "第二个事件不得在前一项目事务提交前取得新序号"
                assert messages(tail(client, pid, cursor).text) == []
            finally:
                release.set()
            assert first.result(timeout=5) < second.result(timeout=5)
        assert [row["type"] for row in messages(tail(client, pid, cursor).text)] == ["test.first", "test.second"]
