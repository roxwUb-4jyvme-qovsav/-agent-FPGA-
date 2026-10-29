"""从 HTTP 读取已提交 outbox；SSE 游标、ACL 和事务回滚的行为回归。"""

import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, select

from apps.api.models import EventRecord, Member, ResourceRecord, utcnow
from packages.contracts.validation import validate_payload

from .test_acl_regressions import add_developer, collection_body, switch
from .test_security import open_client, project, setup, write


def messages(text):
    result = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        data = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
        if data:
            result.append(json.loads("\n".join(data)))
    return result


def tail(client, pid, cursor=None):
    headers = {"Last-Event-ID": str(cursor)} if cursor is not None else {}
    return client.get(f"/api/v1/projects/{pid}/events?follow=false", headers=headers)


def latest(client, pid):
    with client.app.state.db.transaction() as db:
        return max(db.scalars(select(EventRecord.id).where(EventRecord.project_id == pid)).all())


def test_committed_events_are_typed_ordered_and_keep_original_versions(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        original = project(client, csrf).json()
        pid = original["id"]
        cursor = latest(client, pid)
        first = write(client, "patch", f"/projects/{pid}", {"name": "v2"}, csrf, original["etag"])
        second = write(client, "patch", f"/projects/{pid}", {"name": "v3"}, csrf, first.headers["etag"])
        assert second.status_code == 200
        stream = tail(client, pid, cursor)
        assert stream.status_code == 200, stream.text
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = messages(stream.text)
        assert [row["resource_version"] for row in events] == [2, 3]
        assert [row["request_id"] for row in events] == [first.headers["x-request-id"], second.headers["x-request-id"]]
        assert [row["id"] for row in events] == sorted({row["id"] for row in events})
        for row in events:
            validate_payload("Event", row)
            assert f"id: {row['id']}\n" in stream.text
        assert "v2" not in stream.text and "v3" not in stream.text
        assert messages(tail(client, pid, events[-1]["id"]).text) == []


def test_invalid_foreign_future_and_expired_cursor(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid = project(client, csrf).json()["id"]
        cursor = latest(client, pid)
        other = project(client, csrf).json()["id"]
        for supplied in ("-1", "x", "9" * 100, str(latest(client, other)), "9007199254740991"):
            result = tail(client, pid, supplied)
            assert result.status_code == 400, result.text
        with client.app.state.db.transaction() as db:
            db.get(EventRecord, cursor).created_at = utcnow() - timedelta(days=8)
        expired = tail(client, pid, cursor)
        assert expired.status_code == 410, expired.text
        assert expired.json()["error"]["code"] == "EVENT_CURSOR_EXPIRED"
        # 不携带旧游标重新连接时以当前高水位订阅，并要求客户端刷新快照。
        assert tail(client, pid).status_code == 200


def test_event_stream_filters_private_resources_and_rechecks_membership(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid = project(client, csrf).json()["id"]
        owner = client.get("/api/v1/auth/session").json()["user_id"]
        _, membership = add_developer(client, csrf, pid)
        cursor = latest(client, pid)
        private = write(client, "post", f"/projects/{pid}/collections", collection_body(owner), csrf).json()
        guest_csrf = switch(client, csrf, "guest", "guest-private-password")
        stream = tail(client, pid, cursor)
        assert stream.status_code == 200, stream.text
        assert private["id"] not in stream.text
        assert not messages(stream.text)
        with client.app.state.db.transaction() as db:
            db.execute(delete(Member).where(Member.id == membership["id"]))
        assert tail(client, pid, cursor).status_code == 404
        assert write(client, "post", f"/projects/{pid}/collections", collection_body(owner), guest_csrf).status_code == 404


def test_outbox_rollback_and_legacy_events_do_not_fabricate_stream_metadata(tmp_path):
    from apps.api.services.common import event

    with open_client(tmp_path) as client:
        csrf = setup(client)
        original = project(client, csrf).json()
        pid = original["id"]
        cursor = latest(client, pid)
        try:
            with client.app.state.db.transaction() as db:
                record = db.get(ResourceRecord, pid)
                event(db, "test", "test.rollback", record, pid, {"request_id": str(uuid4())})
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        assert latest(client, pid) == cursor
        with client.app.state.db.transaction() as db:
            db.add(EventRecord(project_id=pid, actor_id="legacy", type="legacy", resource_id=pid, payload={}))
        result = tail(client, pid, cursor)
        assert result.status_code == 200, result.text
        assert not messages(result.text)


def test_stream_unauthenticated_request_is_rejected_before_headers(tmp_path):
    with open_client(tmp_path) as client:
        assert tail(client, str(uuid4())).status_code == 401


def test_replay_survives_application_restart(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        original = project(client, csrf).json()
        pid = original["id"]
        cursor = latest(client, pid)
        changed = write(client, "patch", f"/projects/{pid}", {"name": "saved"}, csrf, original["etag"])
        request_id = changed.headers["x-request-id"]
        cookie = client.cookies.get("fpga_session")
    with open_client(tmp_path) as restarted:
        restarted.cookies.set("fpga_session", cookie)
        events = messages(tail(restarted, pid, cursor).text)
        assert len(events) == 1
        assert events[0]["request_id"] == request_id
        assert events[0]["resource_version"] == 2


def test_worker_event_references_run_and_keeps_private_progress_out_of_stream(tmp_path):
    from .test_worker import queued_run, worker_post

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        pid, run, snapshot = queued_run(client, csrf)
        cursor = latest(client, pid)
        worker_id = str(uuid4())
        claimed = worker_post(client, "/worker/jobs/claim", {
            "worker_id": worker_id, "capabilities": ["contract.validate"],
            "tool_profile_sha256": snapshot["toolchain_sha256"],
        }).json()
        heartbeat = worker_post(client, f"/worker/jobs/{claimed['job_id']}/heartbeat", {
            "worker_id": worker_id, "attempt": 1, "fencing_token": 1,
            "progress_message": "PRIVATE PROMPT CONTENT",
        })
        assert heartbeat.status_code == 200
        result = tail(client, pid, cursor)
        assert "PRIVATE PROMPT CONTENT" not in result.text
        values = messages(result.text)
        assert [row["type"] for row in values] == ["task.leased", "task.heartbeat"]
        assert all(row["resource_id"] == run["id"] for row in values)
        assert values[-1]["request_id"] == heartbeat.headers["x-request-id"]


def test_claim_prelocks_all_event_projects_before_multi_project_expiry(tmp_path, monkeypatch):
    """模拟 PostgreSQL 项目锁的顺序约束；不把它计为真实数据库并发验收。"""
    from apps.api.models import TaskRecord
    from apps.api.routers import jobs
    from apps.api.services import common

    from .test_worker import queued_run, worker_post

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        first_pid, _, first_snapshot = queued_run(client, csrf)
        second_pid, _, _ = queued_run(client, csrf)
        low, high = sorted((first_pid, second_pid))
        with client.app.state.db.transaction() as db:
            for task in db.scalars(select(TaskRecord).where(TaskRecord.project_id == high)):
                task.state = "LEASED"
                task.lease_expires_at = utcnow() - timedelta(seconds=1)
                task.attempts = [{"state": "LEASED"}]
        acquired = []

        def locks(_db, scopes):
            for scope in sorted(set(scopes)):
                if scope in acquired:
                    continue
                assert not acquired or scope > acquired[-1], "新增项目锁发生反序"
                acquired.append(scope)

        monkeypatch.setattr(common, "lock_event_projects", locks)
        monkeypatch.setattr(jobs, "lock_event_projects", locks)
        result = worker_post(client, "/worker/jobs/claim", {
            "worker_id": str(uuid4()), "capabilities": ["contract.validate"],
            "tool_profile_sha256": first_snapshot["toolchain_sha256"],
        })
        assert result.status_code == 200, result.text
        assert acquired == [low, high]


def test_live_stream_receives_commit_heartbeat_and_stops_after_revocation(tmp_path, monkeypatch):
    """真实 TCP 流，避免 TestClient 对无限响应缓冲造成假通过。"""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    from apps.api.services import events

    monkeypatch.setattr(events, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.1)
    with open_client(tmp_path) as client:
        csrf = setup(client)
        original = project(client, csrf).json()
        pid = original["id"]
        # 闲置超过保留期后，新的无游标订阅仍须保持在线并接收后续提交。
        with client.app.state.db.transaction() as db:
            for row in db.scalars(select(EventRecord).where(EventRecord.project_id == pid)):
                row.created_at = utcnow() - timedelta(days=8)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(client.app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as remote:
                remote.cookies.set("fpga_session", client.cookies.get("fpga_session"))
                with remote.stream("GET", f"/api/v1/projects/{pid}/events") as response:
                    assert response.status_code == 200
                    lines = response.iter_lines()
                    assert next(lines) == ": connected"
                    changed = write(client, "patch", f"/projects/{pid}", {"name": "live"}, csrf, original["etag"])
                    assert changed.status_code == 200
                    seen = []
                    for line in lines:
                        seen.append(line)
                        if line.startswith("data: "):
                            value = json.loads(line[6:])
                            assert value["request_id"] == changed.headers["x-request-id"]
                            break
                    assert any(line.startswith("data: ") for line in seen)
                    assert any(line == ": heartbeat" for line in lines)
                    with client.app.state.db.transaction() as db:
                        db.execute(delete(Member).where(Member.project_id == pid))
                    remaining = list(lines)
                    assert "event: access_denied" in remaining
                    assert not any('"schema_version"' in line for line in remaining)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
            assert not thread.is_alive()
