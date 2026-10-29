"""设备 HTTP 闭环：重启、协议幂等、权限及不可变采集。"""

import hashlib
import struct

import pytest

from packages.contracts.validation import validate_payload

from .test_security import open_client, project, setup, write
from .test_workflow import imported


def session(client, csrf, channels=16):
    pid, bundle = imported(client, csrf, channels)
    result = write(client, "post", f"/projects/{pid}/device-sessions", {
        "revision_id": bundle["revision"]["id"], "mode": "mock",
        "capture_artifact_id": None, "board_profile_id": None,
    }, csrf)
    assert result.status_code == 201, result.text
    return pid, result.json()["id"]


def base(pid, sid):
    return f"/projects/{pid}/device-sessions/{sid}"


def command(client, csrf, pid, sid, action, seq, payload=b"", key=None):
    accepted = write(client, "post", base(pid, sid) + "/device-commands", {
        "action": action, "sequence": seq, "payload_hex": payload.hex(),
    }, csrf, key=key)
    assert accepted.status_code == 202, accepted.text
    validate_payload("ActionResult", accepted.json())
    result = client.get("/api/v1" + base(pid, sid) + "/commands/" + accepted.json()["resource_id"])
    assert result.status_code == 200, result.text
    validate_payload("DeviceCommandResult", result.json())
    return result.json()


def configure(client, csrf, pid, sid, seq=1, channels=16):
    return command(client, csrf, pid, sid, "set_config", seq,
                   struct.pack("<BBHHI", 0, 0, 10, (1 << channels) - 1, 16))


def advance(client, csrf, pid, sid, segments, key=None):
    return write(client, "post", base(pid, sid) + "/mock-inputs",
                 {"segments": segments}, csrf, key=key)


@pytest.mark.parametrize("channels", [8, 16])
def test_complete_capture_persists_across_restart(tmp_path, channels):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid, sid = session(client, csrf, channels)
        caps = command(client, csrf, pid, sid, "get_caps", 0)
        assert caps["reply"]["data"]["channels"] == channels
        assert configure(client, csrf, pid, sid, channels=channels)["state"] == "COMPLETED"
        arm = command(client, csrf, pid, sid, "arm", 2)
        cid = arm["reply"]["data"]["capture_id"]
        assert advance(client, csrf, pid, sid, [{"value": 1, "cycles": 80}]).status_code == 200
        cookie = client.cookies.get("fpga_session")
    with open_client(tmp_path) as client:
        client.cookies.set("fpga_session", cookie)
        assert advance(client, csrf, pid, sid, [{"value": (1 << channels) - 1, "cycles": 80}]).status_code == 200
        result = command(client, csrf, pid, sid, "read_capture", 3, struct.pack("<IIH", cid, 0, 16))
        assert result["state"] == "COMPLETED"
        chunk = result["reply"]["data"]
        samples = bytes.fromhex(chunk["samples_hex"])
        width = channels // 8
        assert samples == (1).to_bytes(width, "little") * 8 + ((1 << channels) - 1).to_bytes(width, "little") * 8
        records = client.get("/api/v1" + base(pid, sid) + "/captures").json()
        assert len(records["items"]) == 1
        capture = records["items"][0]
        validate_payload("CaptureRecord", capture)
        assert capture["mode"] == "mock"
        assert capture["sample_count"] == 16
        assert client.get("/api/v1" + base(pid, sid) + "/captures/" + capture["id"]).json() == capture
        for kind in ("metadata", "samples"):
            ref = capture[kind]
            content = client.get(f"/api/v1/projects/{pid}/artifacts/{ref['id']}/content")
            assert hashlib.sha256(content.content).hexdigest() == ref["sha256"]
        assert result["capture_ref"] == capture["samples"]
        command(client, csrf, pid, sid, "arm", 4)
        assert client.get("/api/v1" + base(pid, sid) + "/captures").json() == records


def test_repeated_arm_and_conflict_survive_restart(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid, sid = session(client, csrf)
        configure(client, csrf, pid, sid)
        first = command(client, csrf, pid, sid, "arm", 2, key="arm-one")
        cookie = client.cookies.get("fpga_session")
    with open_client(tmp_path) as client:
        client.cookies.set("fpga_session", cookie)
        assert command(client, csrf, pid, sid, "arm", 2, key="arm-one") == first
        repeated = command(client, csrf, pid, sid, "arm", 2)
        assert repeated["response_frame_hex"] == first["response_frame_hex"]
        conflict = command(client, csrf, pid, sid, "abort", 2)
        assert conflict["error"]["error"]["code"] == "SEQUENCE_CONFLICT"
        assert conflict["state"] == "FAILED"
        status = command(client, csrf, pid, sid, "status", 3)["reply"]["data"]
        assert status["capture_id"] == 1 and status["state"] == "ARMED"


def test_bad_input_abort_closed_and_cross_session_boundaries(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid, sid = session(client, csrf)
        configure(client, csrf, pid, sid)
        arm = command(client, csrf, pid, sid, "arm", 2)
        for segments in ([], [{"value": 65536, "cycles": 1}], [{"value": 1, "cycles": 500001}], [{"value": True, "cycles": 1}]):
            assert advance(client, csrf, pid, sid, segments).status_code == 422
        short = command(client, csrf, pid, sid, "set_config", 3, b"\x00")
        assert short["error"]["error"]["code"] == "BAD_LENGTH"
        command(client, csrf, pid, sid, "abort", 4)
        read = command(client, csrf, pid, sid, "read_capture", 5, struct.pack("<IIH", 1, 0, 16))
        assert read["error"]["error"]["code"] == "CAPTURE_ABORTED"
        assert client.get("/api/v1" + base(pid, sid) + "/captures").json()["items"] == []
        other = project(client, csrf).json()["id"]
        assert client.get("/api/v1" + base(other, sid) + "/commands/" + arm["command_id"]).status_code == 404
        latest = client.get("/api/v1" + base(pid, sid)).json()
        close = write(client, "post", base(pid, sid) + "/commands", {
            "action": "close", "expected_version": latest["version"],
            "parameters": {"target_version_id": None, "artifact_id": None, "approval_id": None,
                           "reason": "done", "preserve_history": True},
        }, csrf)
        assert close.status_code == 202, close.text
        assert advance(client, csrf, pid, sid, [{"value": 0, "cycles": 1}]).status_code == 409
        assert write(client, "post", base(pid, sid) + "/device-commands", {
            "action": "arm", "sequence": 6, "payload_hex": "",
        }, csrf).status_code == 409


def test_input_idempotency_and_revoked_access(tmp_path):
    from sqlalchemy import delete

    from apps.api.models import Member

    with open_client(tmp_path) as client:
        csrf = setup(client)
        pid, sid = session(client, csrf)
        configure(client, csrf, pid, sid)
        command(client, csrf, pid, sid, "arm", 2)
        payload = [{"value": 1, "cycles": 80}]
        one = advance(client, csrf, pid, sid, payload, key="input-one")
        assert one.status_code == 200, one.text
        assert advance(client, csrf, pid, sid, payload, key="input-one").json() == one.json()
        status = command(client, csrf, pid, sid, "status", 3)["reply"]["data"]
        assert status["sample_count"] == 8
        with client.app.state.db.transaction() as db:
            db.execute(delete(Member).where(Member.project_id == pid))
        assert advance(client, csrf, pid, sid, payload, key="input-one").status_code == 404
        assert client.get("/api/v1" + base(pid, sid) + "/captures").status_code == 404
