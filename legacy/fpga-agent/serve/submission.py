"""Official async transport; the existing solver remains the execution authority.

Results and nonce tombstones live for this service lifetime. Capacity is bounded:
we reject new work instead of evicting a nonce and accidentally running it twice.
No filesystem, model or EDA work is performed by admission or polling handlers.
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
import uuid

from . import serve_api as api


class AdmissionError(Exception):
    def __init__(self, code, reason):
        self.code, self.reason = code, reason
        super().__init__(reason)


def validate(value):
    if not isinstance(value, dict):
        raise AdmissionError(400, "JSON request must be an object")
    for name in ("task_id", "prompt"):
        if not isinstance(value.get(name), str) or not value[name]:
            raise AdmissionError(400, name + " must be a nonempty string")
    if len(value["task_id"].encode("utf-8")) > 4096 or len(value["prompt"].encode("utf-8")) > 2_000_000:
        raise AdmissionError(400, "Input exceeds limit")
    if value.get("mode", "agent") not in ("agent", "baseline"):
        raise AdmissionError(400, "mode must be agent or baseline")
    for name in ("interface", "nonce"):
        if not isinstance(value.get(name, ""), str):
            raise AdmissionError(400, name + " must be a string")
    if len(value.get("nonce", "").encode("utf-8")) > 4096:
        raise AdmissionError(400, "nonce exceeds limit")
    deadline = value.get("deadline_s", 900)
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or not 5 <= deadline <= 3600:
        raise AdmissionError(400, "deadline_s must be between 5 and 3600")
    return {"task_id": value["task_id"], "prompt": value["prompt"],
            "interface": value.get("interface", ""), "nonce": value.get("nonce", ""),
            "mode": value.get("mode", "agent"), "deadline_s": deadline}


def _not_run(job, reason):
    trace = json.dumps({"tool": "request", "phase": "stop", "reason": reason,
                        "model_calls": 0, "execution_started": False}) + "\n"
    return api._WireResponse({"task_id": job["task_id"], "job_id": job["job_id"], "status": "done",
        "request_status": reason, "execution_result": reason, "authority_confirmation": "NOT_CREATED",
        "evidence_id": None, "request_status_source": "async_admission_memory",
        "elapsed_s": round(time.monotonic() - job["submitted"], 3), "execution_started": False},
        json.dumps({"solution": "", "trace": trace, "known_usage": None,
                    "trace_incomplete": False, "missing_scope": None}).encode())


class Jobs:
    def __init__(self, solver=None, max_jobs=4096, max_pending=4,
                 max_result_bytes=128 * 1024 * 1024 + 8192, max_resident_bytes=600 * 1024 * 1024):
        self.solver = solver or api.solve_request
        self.max_jobs, self.max_pending = max_jobs, max_pending
        self.max_result_bytes, self.max_resident_bytes = max_result_bytes, max_resident_bytes
        self.jobs, self.nonces = {}, {}
        self.lock = threading.RLock()
        self.queue = queue.Queue()
        self.closed = threading.Event()
        self.pending = 0
        self.resident = 0
        self.worker = threading.Thread(target=self._work, name="rtl-solver", daemon=True)
        self.timer = threading.Thread(target=self._expire, name="rtl-queue-deadlines", daemon=True)
        self.worker.start()
        self.timer.start()

    def submit(self, value, started):
        request = validate(value)
        with self.lock:
            nonce = request["nonce"]
            if nonce and nonce in self.nonces:
                # A retry never changes the original payload, clock or authority.
                return self.public(self.jobs[self.nonces[nonce]], result=False)
            if (self.closed.is_set() or len(self.jobs) >= self.max_jobs or self.pending >= self.max_pending or
                    self.resident + self.max_result_bytes > self.max_resident_bytes):
                raise AdmissionError(503, "Service admission capacity reached")
            identifier = uuid.uuid4().hex
            item = {"job_id": identifier, "task_id": request["task_id"], "status": "queued",
                    "submitted": started, "absolute": started + request["deadline_s"], "request": request,
                    "response": None, "poll_delivery": "NOT_DELIVERED", "reservation": self.max_result_bytes}
            self.jobs[identifier] = item
            if nonce:
                self.nonces[nonce] = identifier
            self.pending += 1
            self.resident += self.max_result_bytes
            self.queue.put(identifier)
            return self.public(item, result=False)

    @staticmethod
    def public(item, result=True):
        if result and item["status"] == "done":
            return item["response"]
        return {"job_id": item["job_id"], "task_id": item["task_id"], "status": item["status"]}

    def get(self, identifier):
        with self.lock:
            if identifier not in self.jobs:
                raise AdmissionError(404, "unknown job_id")
            return self.public(self.jobs[identifier])

    def delivered(self, identifier, state):
        # Delivery observations do not overwrite the persisted request authority.
        with self.lock:
            self.jobs[identifier]["poll_delivery"] = state

    def _finish(self, item, response):
        if len(response.wire) > self.max_result_bytes:
            raise ValueError("Response exceeds admission reservation")
        self.resident += len(response.wire) - item["reservation"]
        item.update(status="done", response=response, request=None, reservation=len(response.wire))
        self.pending -= 1

    def _expire(self):
        while not self.closed.wait(.05):
            with self.lock:
                for item in self.jobs.values():
                    if item["status"] == "queued" and time.monotonic() >= item["absolute"]:
                        self._finish(item, _not_run(item, "WALLCLOCK_TIMEOUT"))

    def _work(self):
        while True:
            identifier = self.queue.get()
            if identifier is None:
                return
            with self.lock:
                item = self.jobs[identifier]
                if item["status"] == "done":
                    continue
                if self.closed.is_set() or time.monotonic() >= item["absolute"]:
                    self._finish(item, _not_run(item, "PROCESS_INTERRUPTED" if self.closed.is_set() else "WALLCLOCK_TIMEOUT"))
                    continue
                item["status"] = "running"
            context = api._RequestContext()
            response = None
            try:
                try:
                    response = self.solver(item["request"], request_started=item["submitted"], authority_context=context)
                except Exception as exc:
                    response = api._request_error(item["request"], context, type(exc).__name__, item["absolute"])
                # Never decode the potentially large payload in the parent. Only
                # the tiny transport metadata changes; trace and usage stay exact.
                meta = dict(response)
                meta.update(job_id=identifier, status="done")
                result = api._WireResponse(meta, response.payload)
                with self.lock:
                    self._finish(item, result)
            except Exception as exc:
                # Keep the worker alive; original artifacts and authority remain.
                fallback = api._request_error(item["request"], context, "ASYNC_" + type(exc).__name__, item["absolute"])
                meta = dict(fallback)
                meta.update(job_id=identifier, status="done", finalization_incomplete=True)
                with self.lock:
                    self._finish(item, api._WireResponse(meta, fallback.payload))
            finally:
                if response is not None and hasattr(response, "delivery"):
                    response.delivery.close()
                    del response.delivery
                context.close()

    def close(self, timeout=2):
        self.closed.set()
        with self.lock:
            for item in self.jobs.values():
                if item["status"] == "queued":
                    self._finish(item, _not_run(item, "PROCESS_INTERRUPTED"))
        self.queue.put(None)
        self.worker.join(timeout)
        self.timer.join(timeout)


class Handler(api.Handler):
    """Only this handler is used by the production main entry point."""
    def _authorized(self):
        import hmac
        started = time.monotonic()
        try:
            expected = 'Bearer ' + api._token()
        except RuntimeError:
            self._error(503, 'Submission token not configured', started)
            return False
        if not hmac.compare_digest(self.headers.get('Authorization', ''), expected):
            self._error(401, 'bad token', started)
            return False
        return True

    def do_GET(self):
        started = time.monotonic()
        sending = False
        if not self._authorized():
            return
        try:
            if self.path == "/v1/health":
                value = api.shared_model_ready(started + 5)
            elif self.path.startswith("/v1/jobs/"):
                identifier = self.path[len("/v1/jobs/"):]
                value = self.server.jobs.get(identifier)
            else:
                raise AdmissionError(404, "unknown path")
            sending = True
            self._send(200, value, deadline=started + 5)
            if self.path.startswith("/v1/jobs/"):
                self.server.jobs.delivered(identifier, "SENT")
        except AdmissionError as exc:
            self._error(exc.code, exc.reason, started)
        except Exception as exc:
            if not sending:
                self._error(503, 'Health unavailable', started)
            if self.path.startswith("/v1/jobs/") and 'identifier' in locals() and identifier in self.server.jobs.jobs:
                self.server.jobs.delivered(identifier, "FAILED_" + type(exc).__name__)
            self.close_connection = True

    def _error(self, code, reason, started):
        try:
            self._send(code, {"status": "REQUEST_ERROR", "task_id": "", "reason": reason,
                              "authority_confirmation": "NOT_CREATED", "request_status": None,
                              "evidence_id": None}, deadline=started + 5)
        except Exception:
            self.close_connection = True

    def do_POST(self):
        started = time.monotonic()
        if not self._authorized():
            return
        if self.path != "/v1/solve":
            self._error(404, "unknown path", started)
            return
        accepted = False
        try:
            if self.headers.get("Transfer-Encoding"):
                raise AdmissionError(400, "Transfer-Encoding is not supported")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_100_000:
                raise AdmissionError(400, "Invalid Content-Length")
            self.connection.settimeout(max(.001, started + 5 - time.monotonic()))
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise AdmissionError(400, "Incomplete JSON body")
            value = json.loads(raw)
            response = self.server.jobs.submit(value, started)
            accepted = True
            self._send(202, response, deadline=started + 5)
        except AdmissionError as exc:
            self._error(exc.code, exc.reason, started)
        except (ValueError, UnicodeError):
            if not accepted:
                self._error(400, "Invalid JSON request", started)
            self.close_connection = True
        except Exception:
            # Once accepted, failed delivery must not create/execute another job.
            if not accepted:
                self._error(503, "Admission unavailable", started)
            self.close_connection = True
