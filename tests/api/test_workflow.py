import hashlib
from datetime import timedelta

from .test_security import open_client, project, setup, write


def imported(client, csrf, channels=8):
    pid = project(client, csrf).json()["id"]
    result = write(
        client, "post", f"/projects/{pid}/examples/analyzer-{channels}ch/import", {}, csrf
    )
    assert result.status_code == 201, result.text
    return pid, result.json()


def freeze(client, csrf, pid, bundle):
    from apps.api.models import utcnow

    rev = bundle["revision"]
    approval = write(
        client,
        "post",
        f"/projects/{pid}/approvals",
        {
            "action": "freeze",
            "target_id": rev["id"],
            "target_sha256": bundle["target_sha256"],
            "expires_at": (utcnow() + timedelta(minutes=20)).isoformat(),
            "reason": "确认虚拟参数",
        },
        csrf,
    )
    assert approval.status_code == 201, approval.text
    decision = write(
        client,
        "post",
        f"/projects/{pid}/approvals/{approval.json()['id']}/decision",
        {"expected_version": 1, "decision": "approve", "reason": "人工复核"},
        csrf,
    )
    assert decision.status_code == 200, decision.text
    latest = client.get(f"/api/v1/projects/{pid}/revisions/{rev['id']}").json()
    result = write(
        client,
        "post",
        f"/projects/{pid}/revisions/{rev['id']}/commands",
        {
            "action": "freeze",
            "expected_version": latest["version"],
            "parameters": {
                "target_version_id": None,
                "artifact_id": None,
                "approval_id": approval.json()["id"],
                "reason": "冻结测试基线",
                "preserve_history": True,
            },
        },
        csrf,
    )
    assert result.status_code == 202, result.text
    return rev["id"]


def test_example_import_produces_immutable_artifacts_and_draft(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid, bundle = imported(client, csrf)
        assert bundle["revision"]["state"] == "DRAFT"
        for ref in bundle["artifacts"]:
            content = client.get(f"/api/v1/projects/{pid}/artifacts/{ref['id']}/content")
            assert hashlib.sha256(content.content).hexdigest() == ref["sha256"]
        # 工件没有通用写接口，客户端无法把未验证输入改成 PASS。
        assert write(client, "post", f"/projects/{pid}/evidence", {}, csrf).status_code == 405


def test_run_requires_frozen_revision_and_does_not_claim_verification(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid, bundle = imported(client, csrf)
        request = {"revision_id": bundle["revision"]["id"], "mode": "simulate", "max_cost_cny": "0"}
        assert write(client, "post", f"/projects/{pid}/runs", request, csrf).status_code == 409
        rid = freeze(client, csrf, pid, bundle)
        run = write(client, "post", f"/projects/{pid}/runs", request, csrf)
        assert run.status_code == 201, run.text
        task_list = client.get(f"/api/v1/projects/{pid}/runs/{run.json()['id']}/tasks").json()
        assert task_list["items"][0]["state"] == "QUEUED"
        assert client.get(f"/api/v1/projects/{pid}/revisions/{rid}").json()["state"] != "VERIFIED"


def test_upload_size_and_hash_are_checked_before_promotion(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid = project(client, csrf).json()["id"]
        body = b"contract-data"
        request = {
            "filename": "../unsafe.txt",
            "content_type": "text/plain",
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        ticket = write(client, "post", f"/projects/{pid}/uploads", request, csrf)
        assert ticket.status_code == 201, ticket.text
        path = ticket.json()["put_path"]
        content = client.put(
            path, content=b"wrong", headers={"Origin": "http://testserver", "X-CSRF-Token": csrf}
        )
        assert content.status_code == 422
        content = client.put(
            path, content=body, headers={"Origin": "http://testserver", "X-CSRF-Token": csrf}
        )
        assert content.status_code == 204, content.text
        completed = write(
            client,
            "post",
            f"/projects/{pid}/uploads/{ticket.json()['upload_id']}/complete",
            {"sha256": request["sha256"]},
            csrf,
        )
        assert completed.status_code == 200, completed.text
        assert (
            client.get(f"/api/v1/projects/{pid}/artifacts/{completed.json()['id']}/content").content
            == body
        )
        assert not (tmp_path / "unsafe.txt").exists()
