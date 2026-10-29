from tests.api.test_management import command, upload

from .test_security import ORIGIN, open_client, project, setup, write


def add_developer(client, csrf, pid):
    created = write(
        client,
        "post",
        "/admin/users",
        {"username": "guest", "global_role": "user", "enabled": True},
        csrf,
    ).json()
    assert (
        write(
            client,
            "post",
            f"/system/users/{created['id']}/password",
            {"password": "guest-private-password"},
            csrf,
        ).status_code
        == 204
    )
    member = write(
        client,
        "post",
        f"/projects/{pid}/members",
        {"user_id": created["id"], "role": "developer"},
        csrf,
    ).json()
    return created["id"], member


def switch(client, csrf, username, password):
    client.delete("/api/v1/auth/session", headers={**ORIGIN, "X-CSRF-Token": csrf})
    response = client.post(
        "/api/v1/auth/session", json={"username": username, "password": password}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def collection_body(uid):
    return {
        "name": "私有资料",
        "description": "",
        "embedding_profile_version_id": None,
        "embedding_dimension": None,
        "acl_member_ids": [uid],
        "external_connection_id": None,
    }


def test_idempotency_replay_rechecks_current_project_role(tmp_path):
    with open_client(tmp_path) as client:
        owner_csrf = setup(client)
        pid = project(client, owner_csrf).json()["id"]
        guest, membership = add_developer(client, owner_csrf, pid)
        guest_csrf = switch(client, owner_csrf, "guest", "guest-private-password")
        path = f"/projects/{pid}/collections"
        body = collection_body(guest)
        assert write(client, "post", path, body, guest_csrf, key="original").status_code == 201
        owner_csrf = switch(client, guest_csrf, "owner", "my-long-local-password")
        removed = write(
            client,
            "delete",
            f"/projects/{pid}/members/{membership['id']}",
            None,
            owner_csrf,
            membership["etag"],
        )
        assert removed.status_code == 204, removed.text
        guest_csrf = switch(client, owner_csrf, "guest", "guest-private-password")
        assert write(client, "post", path, body, guest_csrf, key="new").status_code == 404
        replay = write(client, "post", path, body, guest_csrf, key="original")
        assert replay.status_code == 404


def test_private_document_acl_inherits_to_artifact_listing_and_bytes(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid = project(client, csrf).json()["id"]
        owner = client.get("/api/v1/auth/session").json()["user_id"]
        add_developer(client, csrf, pid)
        collection = write(
            client, "post", f"/projects/{pid}/collections", collection_body(owner), csrf
        ).json()
        ticket = upload(client, pid, csrf, b"PRIVATE DOCUMENT CONTENT\n")
        document = write(
            client,
            "post",
            f"/projects/{pid}/documents",
            {
                "upload_id": ticket,
                "collection_id": collection["id"],
                "scope": "project_knowledge",
                "license": "internal",
                "external_send_allowed": False,
            },
            csrf,
        ).json()
        path = f"/projects/{pid}/documents/{document['id']}"
        assert command(client, path, "parse", 1, csrf).status_code == 202
        current = client.get("/api/v1" + path).json()
        assert command(client, path, "publish", current["version"], csrf).status_code == 202
        artifacts = client.get(f"/api/v1/projects/{pid}/artifacts").json()["items"]
        assert artifacts
        guest_csrf = switch(client, csrf, "guest", "guest-private-password")
        listed = client.get(f"/api/v1/projects/{pid}/artifacts").json()["items"]
        assert not listed
        for record in artifacts:
            response = client.get(f"/api/v1/projects/{pid}/artifacts/{record['id']}/content")
            assert response.status_code == 404
        search = write(
            client,
            "post",
            f"/projects/{pid}/knowledge/search",
            {
                "query": "PRIVATE",
                "collection_ids": [collection["id"]],
                "document_version_ids": [],
                "top_k": 5,
            },
            guest_csrf,
        )
        assert search.json()["hits"] == []
