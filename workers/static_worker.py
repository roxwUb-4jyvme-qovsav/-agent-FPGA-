"""零预算常驻 Worker：只解析冻结 JSON，不编译或执行生成代码。"""

import argparse
import hashlib
import json
import logging
import os
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from packages.contracts.validation import ContractError, validate_payload


class HTTPClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...
    def stream(self, method: str, url: str, **kwargs: Any) -> Any: ...


LOG = logging.getLogger("fpga.static_worker")
VERSION = "contract-validator-v1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InputTooLarge(ValueError):
    pass


class StaticWorker:
    def __init__(self, client: HTTPClient, token: str, *, worker_id: str, heartbeat_seconds: float = 15) -> None:
        if not token or heartbeat_seconds <= 0:
            raise ValueError("Worker 配置无效")
        self.client, self.token, self.worker_id = client, token, worker_id
        self.heartbeat_seconds = heartbeat_seconds

    def request(self, method: str, path: str, *, headers: dict[str, str] | None = None, key: str | None = None, **kwargs: Any) -> httpx.Response:
        credentials = {"Authorization": "Bearer " + self.token, **(headers or {})}
        if method == "POST":
            credentials["Idempotency-Key"] = key or str(uuid4())
        # 对不明确的写请求只重发同一幂等键和原请求体，不创建第二个副作用。
        for attempt in range(2):
            try:
                response = self.client.request(method, path, headers=credentials, **kwargs)
                response.raise_for_status()
                return response
            except httpx.RequestError:
                if attempt:
                    raise

    def run_forever(self, stop: threading.Event, *, poll_seconds: float = 2) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds 必须为正数")
        while not stop.is_set():
            try:
                if self.run_once(stop):
                    continue
            except httpx.HTTPStatusError as exc:
                LOG.warning("Worker API 返回状态 %s", exc.response.status_code)
                if exc.response.status_code in (401, 403):
                    return
            except (httpx.RequestError, ValueError, ContractError):
                LOG.warning("Worker 运行中断，保留租约现场，等待安全重领")
            stop.wait(poll_seconds)

    def run_once(self, stop: threading.Event | None = None) -> bool:
        stop = stop or threading.Event()
        profile = hashlib.sha256(canonical({"tool": "contract-validator", "mode": "static"})).hexdigest()
        lease = self.request("POST", "/api/v1/worker/jobs/claim", json={
            "worker_id": self.worker_id, "capabilities": ["contract.validate"],
            "tool_profile_sha256": profile,
        }).json()
        if not lease["job_id"]:
            return False
        task = lease["task"]
        validate_payload("TaskEnvelope", task)
        if task["allowed_tools"] != ["contract.validate"] or task["max_cost_cny"] != "0":
            raise ValueError("拒绝非静态或非零预算任务")
        headers = {"X-Worker-ID": self.worker_id, "X-Task-Attempt": str(task["attempt"]),
                   "X-Fencing-Token": str(task["fencing_token"])}
        guard = LeaseGuard(self, task, headers, stop)
        with guard:
            started = now()
            try:
                self.execute_task(task, headers, guard, started)
            except httpx.HTTPStatusError as exc:
                code = exc.response.json().get("error", {}).get("code") if exc.response.status_code == 409 else None
                if code not in ("CANCELLATION_PENDING", "TASK_DEADLINE_EXCEEDED"):
                    raise
                status = "cancelled" if code == "CANCELLATION_PENDING" else "timeout"
                log = self.upload(task, headers, "worker.log", canonical({"status": status, "tool": VERSION}) + b"\n")
                self.finish(task, status, started, [], log)
        return True

    def execute_task(self, task: dict[str, Any], headers: dict[str, str], guard: "LeaseGuard", started: str) -> None:
        status, issues = "passed", []
        documents = {}
        for ref in task["input_artifacts"]:
            if not guard.check():
                break
            try:
                content = self.download(task, headers, ref)
            except InputTooLarge:
                issues.append({"artifact_id": ref["id"], "code": "INPUT_TOO_LARGE"})
                continue
            if hashlib.sha256(content).hexdigest() != ref["sha256"]:
                raise ValueError("输入工件哈希不匹配")
            schema = {"requirement": "RequirementSpec", "system_contract": "SystemContract", "acceptance": "AcceptanceSpec"}.get(ref["kind"])
            try:
                if not schema or ref["kind"] in documents:
                    raise ValueError("输入类型缺失、重复或未登记")
                document = json.loads(content)
                validate_payload(schema, document)
                if document["project_id"] != task["project_id"] or document["revision_id"] != task["revision_id"]:
                    raise ValueError("输入项目或版本不匹配")
                documents[ref["kind"]] = (document, ref["sha256"])
            except (ValueError, ContractError, RecursionError) as exc:
                issues.append({"artifact_id": ref["id"], "code": type(exc).__name__})
        if set(documents) != {"requirement", "system_contract", "acceptance"}:
            issues.append({"code": "INPUT_SET_INCOMPLETE"})
        else:
            contract = documents["system_contract"][0]
            if (contract["requirement_sha256"] != documents["requirement"][1]
                    or contract["acceptance_sha256"] != documents["acceptance"][1]
                    or contract["sample_bytes"] * 8 != contract["channels"]):
                issues.append({"code": "CONTRACT_INCONSISTENT"})
        if issues:
            status = "failed"
        status = guard.interrupted_status() or status
        report = {"static_validation_status": status, "verification_status": "NOT_RUN",
                  "tool_version": VERSION, "task_id": task["task_id"], "issues": issues}
        refs = []
        if status in ("passed", "failed"):
            refs.append(self.upload(task, headers, "contract-validation.json", canonical(report)))
        log = self.upload(task, headers, "worker.log", canonical({"status": status, "tool": VERSION}) + b"\n")
        guard.check(allow_cancel=True)
        if guard.interrupted_status():
            status, refs = guard.interrupted_status(), []
        self.finish(task, status, started, refs, log)

    def finish(self, task: dict[str, Any], status: str, started: str, refs: list, log: dict) -> None:
        result = {"schema_version": "1.0", "call_id": str(uuid4()), "task_id": task["task_id"],
                  "revision_id": task["revision_id"], "status": status, "tool_version": VERSION,
                  "input_sha256": hashlib.sha256(canonical(task["input_artifacts"])).hexdigest(),
                  "started_at": started, "finished_at": now(), "exit_code": 0 if status == "passed" else 1,
                  "artifact_refs": refs, "log_ref": log, "summary": "静态契约检查；工程独立验证未执行"}
        validate_payload("ToolResult", result)
        self.request("POST", f"/api/v1/worker/jobs/{task['task_id']}/complete", key=f"complete:{task['task_id']}:{task['attempt']}:{task['fencing_token']}",
                     json={"worker_id": self.worker_id, "attempt": task["attempt"], "fencing_token": task["fencing_token"], "result": result})

    def download(self, task: dict[str, Any], headers: dict[str, str], ref: dict[str, Any]) -> bytes:
        path = f"/api/v1/worker/jobs/{task['task_id']}/artifacts/{ref['id']}/content"
        credentials = {"Authorization": "Bearer " + self.token, **headers}
        with self.client.stream("GET", path, headers=credentials) as response:
            if response.status_code == 413:
                raise InputTooLarge()
            if response.is_error:
                error = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    if len(error) + len(chunk) > 65536:
                        raise ValueError("Worker 错误响应超过上限")
                    error.extend(chunk)
                httpx.Response(response.status_code, content=bytes(error), request=response.request).raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(content) + len(chunk) > 4 * 1024 * 1024:
                    raise InputTooLarge()
                content.extend(chunk)
        return bytes(content)

    def upload(self, task: dict[str, Any], headers: dict[str, str], filename: str, content: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        path = f"/api/v1/worker/jobs/{task['task_id']}/uploads"
        key = f"output:{task['task_id']}:{task['fencing_token']}:{filename}:{digest}"
        ticket = self.request("POST", path, headers=headers, key=key, json={
            "filename": filename, "content_type": "application/json" if filename.endswith(".json") else "text/plain",
            "bytes": len(content), "sha256": digest,
        }).json()
        self.request("PUT", ticket["put_path"], headers=headers, content=content)
        return self.request("POST", path + f"/{ticket['upload_id']}/complete", headers=headers,
                            key=key + ":complete", json={"sha256": digest}).json()


class LeaseGuard:
    def __init__(self, worker: StaticWorker, task: dict[str, Any], headers: dict[str, str], stop: threading.Event) -> None:
        self.worker, self.task, self.headers, self.stop = worker, task, headers, stop
        self.finished, self.cancelled, self.lost = threading.Event(), threading.Event(), threading.Event()
        self.timed_out = threading.Event()
        self.thread = threading.Thread(target=self.maintain, daemon=True)

    def heartbeat(self) -> None:
        task = self.task
        if self.interrupted_status() == "timeout":
            return
        try:
            self.worker.request("POST", f"/api/v1/worker/jobs/{task['task_id']}/heartbeat", json={
                "worker_id": self.worker.worker_id, "attempt": task["attempt"],
                "fencing_token": task["fencing_token"], "progress_message": "static contract validation",
            })
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409 and exc.response.json().get("error", {}).get("code") == "TASK_DEADLINE_EXCEEDED":
                self.timed_out.set()
                return
            raise

    def check(self, *, allow_cancel: bool = False) -> bool:
        if self.lost.is_set():
            raise ValueError("Worker 租约已丢失")
        state = self.worker.request("GET", f"/api/v1/worker/jobs/{self.task['task_id']}/cancel-state", headers=self.headers).json()
        if state["cancel_requested"] or self.stop.is_set():
            self.cancelled.set()
        if self.interrupted_status() and not allow_cancel:
            # 静态任务没有子进程；停止解析后提交 cancelled，不声称工程成功。
            return False
        return True

    def interrupted_status(self) -> str | None:
        if self.cancelled.is_set() or self.stop.is_set():
            return "cancelled"
        deadline = datetime.fromisoformat(self.task["deadline"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= deadline:
            self.timed_out.set()
        return "timeout" if self.timed_out.is_set() else None

    def maintain(self) -> None:
        while not self.finished.wait(self.worker.heartbeat_seconds):
            try:
                self.heartbeat()
                self.check(allow_cancel=True)
            except (httpx.HTTPError, ValueError):
                self.lost.set()
                return

    def __enter__(self) -> "LeaseGuard":
        self.heartbeat()
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.finished.set()
        self.thread.join()


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--state-dir", type=Path, default=Path(".local/static-worker"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    address = urlsplit(args.api_url)
    if (address.scheme not in ("http", "https") or address.hostname not in ("127.0.0.1", "localhost", "::1")
            or address.username or address.password or address.query or address.fragment or address.path not in ("", "/")):
        parser.error("默认静态 Worker 仅接受回环 API 根地址")
    token = os.getenv("FPGA_WORKER_TOKEN")
    if not token:
        parser.error("请通过环境变量或本机 .env 配置 FPGA_WORKER_TOKEN")
    args.state_dir.mkdir(parents=True, exist_ok=True)
    identity = args.state_dir / "worker-id"
    try:
        with identity.open("x", encoding="utf-8") as handle:
            handle.write(str(uuid4()))
    except FileExistsError:
        pass
    worker_id = identity.read_text(encoding="utf-8").strip()
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    logging.basicConfig(level=logging.INFO)
    with httpx.Client(base_url=args.api_url, timeout=10, follow_redirects=False, trust_env=False) as client:
        worker = StaticWorker(client, token, worker_id=worker_id)
        if args.once:
            worker.run_once(stop)
        else:
            worker.run_forever(stop)


if __name__ == "__main__":
    main()
