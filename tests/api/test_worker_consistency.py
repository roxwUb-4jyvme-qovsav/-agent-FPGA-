import hashlib
import json
from uuid import uuid4

from .test_security import open_client, setup
from .test_worker import queued_run, worker_post


def test_successful_worker_result_requires_exit_zero_and_exact_inputs(tmp_path):
    from apps.api.models import utcnow

    with open_client(tmp_path, worker_token="worker-test-token") as client:
        csrf = setup(client)
        _pid, _run, snapshot = queued_run(client, csrf)
        worker = str(uuid4())
        lease = worker_post(
            client,
            "/worker/jobs/claim",
            {
                "worker_id": worker,
                "capabilities": ["contract.validate"],
                "tool_profile_sha256": snapshot["toolchain_sha256"],
            },
        ).json()
        envelope = lease["task"]
        expected = hashlib.sha256(
            json.dumps(
                envelope["input_artifacts"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        now = utcnow().isoformat()
        result = {
            "schema_version": "1.0",
            "call_id": str(uuid4()),
            "task_id": envelope["task_id"],
            "revision_id": envelope["revision_id"],
            "status": "passed",
            "tool_version": "static-validator",
            "input_sha256": expected,
            "started_at": now,
            "finished_at": now,
            "exit_code": 1,
            "artifact_refs": [],
            "log_ref": None,
            "summary": "结构检查结果",
        }

        def complete(value):
            return worker_post(
                client,
                f"/worker/jobs/{envelope['task_id']}/complete",
                {"worker_id": worker, "attempt": 1, "fencing_token": 1, "result": value},
            )

        wrong_exit = complete(result)
        assert wrong_exit.status_code == 422
        assert wrong_exit.json()["error"]["code"] == "RESULT_INCONSISTENT"
        wrong_hash = complete({**result, "exit_code": 0, "input_sha256": "0" * 64})
        assert wrong_hash.status_code == 422
        assert wrong_hash.json()["error"]["code"] == "INPUT_HASH_MISMATCH"
        correct = complete({**result, "exit_code": 0})
        assert correct.status_code == 200, correct.text
        assert correct.json()["state"] == "SUCCEEDED"
