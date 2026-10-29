"""Loopback-only RTL submission API using Python's standard library."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import hashlib
import shutil
import sys

from agent.main import _model_config, check_baseline_admission
from rtl_agent.local_model import check_model_service, tokenize_chat, RequestTimeout
from rtl_agent.gpu_measurement import probe_vram
from .deployment_profile import validate_ready_memory, measured_memory
from rtl_agent.runtime_audit import append_event, durable_json, terminate_tree, PROCESSES, cancel_processes

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Semaphore(1)


class _WireResponse(dict):
    """HTTP consumes pre-encoded bytes. Optional Python inspection is lazy.

    Inspection decoding is outside solve_request/Handler's critical path; the
    submission HTTP adapter never calls it or re-encodes the large response.
    """
    def __init__(self, value, payload):
        super().__init__(value)
        self.payload = payload
        self.wire = json.dumps(value, ensure_ascii=False).encode()[:-1] + b"," + payload[1:]
        self.inspected = False

    def __getitem__(self, key):
        if key not in self and not self.inspected:
            self.update(json.JSONDecoder().decode(self.payload.decode()))
            self.inspected = True
        return super().__getitem__(key)

    def get(self, key, default=None):
        try: return self[key]
        except KeyError: return default


def _token() -> str:
    token = os.environ.get("FPGACHINA_TOKEN", "")
    if not token:
        raise RuntimeError("FPGACHINA_TOKEN must be set")
    return token


def _read(path: Path) -> str:
    try: return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError): return ""


def _vram_gb() -> float:
    measurement = probe_vram()
    return measurement.get("used_gib", 0.0) if measurement["status"] == "MEASURED" else 0.0


def _authority_frame(raw):
    if raw is None or len(raw) > 256:
        return None
    parts = raw.strip().split(b" ")
    if len(parts) != 5 or parts[0] != b"AUTH" or parts[2] not in (b"0", b"1") or parts[3] not in (b"0", b"1"):
        return None
    if not re.fullmatch(rb"[A-Za-z_]+", parts[1]+parts[4]):
        return None
    return parts[1].decode("ascii"), parts[2] == b"1", parts[3] == b"1", parts[4].decode("ascii")


def _bounded_finalization(evidence, task_id, candidate_status, started, absolute, deadline_s, cache=None):
    from rtl_agent.bounded_io import job
    fallback_payload = cache.raw() if cache else b'{"trace":"","known_usage":null,"trace_incomplete":true,"missing_scope":"cache_unavailable"}'
    status, incomplete, reason, confirmation = "WALLCLOCK_TIMEOUT", True, "FINALIZATION_NOT_COMMITTED", "CONFIRMED"
    archive_incomplete = True
    payload = fallback_payload
    prepared = None
    if time.monotonic() < absolute - .9:
        prepared, error = job("serve.finalize", {"evidence": str(evidence), "task_id": task_id,
            "status": candidate_status, "started": started, "absolute": absolute-.9,
            "deadline_s": deadline_s, "cache": str(cache.path) if cache else None}, absolute-.9)
        fallback_payload = cache.raw() if cache else fallback_payload
        if prepared is not None and prepared[:11] in (b"PREPARED 0\n", b"PREPARED 1\n") and len(prepared) <= 128*1024*1024:
            proposed = prepared[11:]
            if proposed.startswith(b"{") and proposed.endswith(b"}"):
                status, incomplete, reason, payload = candidate_status, False, "NONE", proposed
                archive_incomplete = prepared[9:10] == b"1"
            else:
                prepared = None
        else:
            prepared = None
        if prepared is None:
            status = "WALLCLOCK_TIMEOUT" if error == "WALLCLOCK_TIMEOUT" else "FINALIZATION_ERROR"
            reason = error or "FINALIZATION_PROTOCOL_ERROR"
            payload = fallback_payload
    else:
        error = "WALLCLOCK_TIMEOUT"
    # The owner chooses a decision only after the prepare worker has exited.
    # All authority I/O is cancellable. On lost ACK, resolve in another bounded
    # worker; if that cannot confirm, return UNKNOWN (no opposite terminal claim).
    if time.monotonic() < absolute-.35 and not (prepared is None and error == "WALLCLOCK_TIMEOUT"):
        value = {"task_id": task_id, "status": status, "execution_result": candidate_status,
                 "phase": "COMMITTED", "provisional": False, "finalization_incomplete": incomplete,
                 "archive_incomplete": archive_incomplete, "reason": None if reason == "NONE" else reason,
                 "deadline_s": deadline_s, "sha256": {}, "decision_owner": "request_parent"}
        raw, commit_error = job("serve.authority", {"action": "commit", "evidence": str(evidence),
                               "absolute": absolute-.35, "value": value}, absolute-.35)
        frame = _authority_frame(raw)
        if frame is None:
            raw, _ = job("serve.authority", {"action": "resolve", "evidence": str(evidence)}, absolute-.15)
            frame = _authority_frame(raw)
            if frame and frame[0] == "WALLCLOCK_TIMEOUT" and commit_error and commit_error.startswith("WORKER_ERROR") and time.monotonic() < absolute-.15:
                value.update(status="FINALIZATION_ERROR", finalization_incomplete=True, archive_incomplete=True,
                             reason=commit_error.removeprefix("WORKER_ERROR_") if commit_error.startswith("WORKER_ERROR_") else commit_error)
                raw, _ = job("serve.authority", {"action": "commit", "evidence": str(evidence),
                    "absolute": absolute-.15, "value": value}, absolute-.15)
                frame = _authority_frame(raw)
        if frame:
            status, incomplete, archive_incomplete, reason = frame
            if incomplete:
                payload = cache.raw() if cache else fallback_payload
        else:
            status, incomplete, reason, confirmation = "FINALIZATION_INCOMPLETE", True, "AUTHORITY_CONFIRMATION_UNKNOWN", "UNKNOWN"
            archive_incomplete = True
            payload = cache.raw() if cache else fallback_payload
    elif prepared is not None:
        status, incomplete, reason = "WALLCLOCK_TIMEOUT", True, "FINALIZATION_NOT_COMMITTED"
        archive_incomplete = True
        payload = cache.raw() if cache else fallback_payload
    if incomplete:
        # Cache contains only confirmed durable events. Final trace completion is
        # unknown on timeout even if the bounded cache has not overflowed.
        payload = re.sub(rb'"trace_incomplete":\s*(?:true|false)', b'"trace_incomplete":true', payload)
        payload = re.sub(rb'"missing_scope":\s*(?:null|"[^"\\]*")',
                         b'"missing_scope":"cache overflow; unreported writes; events outside last confirmed bounded snapshot"', payload)
        payload = re.sub(rb'"cost_complete":\s*(?:true|false)', b'"cost_complete":false', payload)
        payload = payload[:-1] + b',"solution":""}'
    response = _WireResponse({"task_id": task_id, "evidence_id": evidence.name, "status": status,
        "request_status": None if confirmation == "UNKNOWN" else status,
        "request_status_source": "audit_index.json", "authority_confirmation": confirmation,
        "execution_result": candidate_status, "finalization_incomplete": incomplete,
        "known_usage_semantics": "lower bound; missing or unknown calls are not zero-cost claims",
        "solution_available": not incomplete and not payload.startswith(b'{"solution": ""'),
        "archive_incomplete": archive_incomplete, "reason": reason,
        "elapsed_s": round(time.monotonic()-started, 3)}, payload)
    response.evidence = evidence
    return response


def shared_model_ready(deadline: float | None = None) -> dict:
    """Both modes inherit one alias, one URL, and one serving context."""
    config = _model_config()
    def remaining():
        left = min(8, deadline - time.monotonic()) if deadline is not None else 8
        if left <= 0:
            raise TimeoutError("WALLCLOCK_TIMEOUT")
        return left
    config["timeout_s"] = remaining()
    if os.environ.get("LLM_MODEL") and os.environ["LLM_MODEL"] != config["model"]:
        raise ValueError("LLM_MODEL and MODEL_NAME differ")
    health = check_model_service(config)
    if health["status"] != "READY":
        raise RuntimeError("Configured model alias is not served")
    if health["available_models"] != [config["model"]]:
        raise RuntimeError("Official baseline and Agent require one unambiguous served model")
    tokenized = tokenize_chat(config, [{"role": "user", "content": "health"}], timeout_s=remaining())
    if tokenized["max_model_len"] != config["context_tokens"]:
        raise RuntimeError("Serving context differs from Agent and baseline declaration")
    measurement = (measured_memory(remaining()) if os.environ.get("AGENT_REQUIRE_VRAM") == "1"
                   else probe_vram(remaining()))
    vram = measurement.get("used_gib", 0.0)
    if os.environ.get("AGENT_REQUIRE_VRAM") == "1":
        validate_ready_memory(measurement)
    return {"ready": True, "track": "rtl", "model": config["model"],
            "vram_gb": vram, "vram_measurement": measurement,
            "vram_unit": "GiB (bytes / 2**30)",
            "context_tokens": config["context_tokens"]}


class _RequestContext:
    """Per-request authority/delivery ownership, independent of response creation."""
    def __init__(self):
        self.evidence = None
        self.authority_attempted = False
        self.delivery = None
        self.payload = b'{"trace":"","known_usage":null,"cost_complete":false,"trace_incomplete":true,"missing_scope":"initialization interrupted","solution":""}'

    def close(self):
        if self.delivery is not None:
            try:
                self.delivery.close()
            finally:
                self.delivery = None


def _request_error(request, context, reason, absolute):
    """Resolve persisted authority in a supervised worker; never invent an outcome."""
    from rtl_agent.bounded_io import job
    task_id = request.get("task_id", "") if isinstance(request, dict) else ""
    if not isinstance(task_id, str) or len(task_id.encode("utf-8")) > 4096:
        task_id = ""
    confirmation, status = "NOT_CREATED", None
    if context.authority_attempted:
        confirmation = "UNKNOWN"
        raw, _ = job("serve.authority", {"action": "resolve", "evidence": str(context.evidence)}, absolute)
        frame = _authority_frame(raw)
        if frame:
            confirmation, status = "CONFIRMED", frame[0]
    payload = re.sub(rb'"trace_incomplete":\s*(?:true|false)', b'"trace_incomplete":true', context.payload)
    payload = re.sub(rb'"cost_complete":\s*(?:true|false)', b'"cost_complete":false', payload)
    if b'"solution"' not in payload:
        payload = payload[:-1] + b',"solution":""}'
    value = _WireResponse({"task_id": task_id, "status": "REQUEST_ERROR", "request_status": status,
        "authority_confirmation": confirmation, "request_status_source": "audit_index.json" if context.authority_attempted else None,
        "evidence_id": context.evidence.name if context.evidence is not None else None,
        "execution_result": None, "solution_available": False, "finalization_incomplete": True,
        "archive_incomplete": True, "reason": reason[:200]}, payload)
    if context.evidence is not None:
        value.evidence = context.evidence
    if context.delivery is not None:
        value.delivery, context.delivery = context.delivery, None
    return value


def solve_request(request: dict, runner=None, *, request_started: float | None = None, authority_context=None) -> dict:
    started = time.monotonic() if request_started is None else request_started
    for key in ("task_id", "prompt"):
        if not isinstance(request.get(key), str) or not request[key]:
            raise ValueError(f"{key} must be a nonempty string")
    if len(request["task_id"].encode("utf-8")) > 4096:
        raise ValueError("task_id exceeds bounded response metadata limit")
    if len(request["prompt"].encode("utf-8")) > 2_000_000:
        raise ValueError("Prompt exceeds input limit")
    mode = request.get("mode", "agent")
    if mode not in ("agent", "baseline"):
        raise ValueError("mode must be agent or baseline")
    interface = request.get("interface", "")
    if not isinstance(interface, str):
        raise ValueError("interface must be a string")
    deadline = request.get("deadline_s", 900)
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not 5 <= deadline <= 3600:
        raise ValueError("deadline_s must be between 5 and 3600 seconds")
    absolute = started + float(deadline)
    script = ROOT / ("run.sh" if mode == "agent" else "run_baseline.sh")
    scratch = Path(os.environ.get("AGENT_TMP", "/tmp"))
    scratch.mkdir(parents=True, exist_ok=True)
    evidence_base = Path(os.environ.get("AGENT_EVIDENCE_DIR", str(scratch / "rtl-evidence")))
    evidence = evidence_base / uuid.uuid4().hex
    input_dir, output_dir = evidence / "input", evidence / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()
    from rtl_agent.trace_cache import TraceCache, CACHES, DeliveryState
    context = authority_context or _RequestContext()
    context.evidence = evidence
    cache = None
    cache_key = str((output_dir / "trace.jsonl").resolve())
    try:
        cache = TraceCache(evidence / "trace.cache", create=True)
        CACHES[cache_key] = cache
        context.delivery = DeliveryState(evidence / "http_delivery.state")
        # Write-ahead fallback also covers a request that consumes its entire budget
        # in preflight/queueing and cannot launch a finalizer at all.
        context.authority_attempted = True
        durable_json(evidence / "audit_index.json", {
            "task_id": request["task_id"], "status": "WALLCLOCK_TIMEOUT",
            "phase": "RECEIVED", "provisional": True, "finalization_incomplete": True,
            "archive_incomplete": True, "reason": "FINALIZATION_NOT_COMMITTED",
            "deadline_s": deadline, "sha256": {}})
        durable_json(output_dir / "request_ledger.json", {
            "request_status_source": "../audit_index.json", "deadline_s": deadline})
        durable_json(evidence / "http_delivery.json", {
            "state": "PENDING", "request_status_source": "audit_index.json",
            "state_source": "http_delivery.state", "register_codes": {"P": "PENDING", "S": "SENT", "T": "DELIVERY_TIMEOUT", "F": "DELIVERY_FAILED"},
            "register_durability": "OS_COHERENT_NOT_FSYNC_CONFIRMED",
            "deadline_monotonic": absolute, "unconfirmed_after_deadline": "DELIVERY_UNKNOWN"})
        (input_dir / "prompt.txt").write_text(request["prompt"], encoding="utf-8")
        (input_dir / "interface.txt").write_text(interface, encoding="utf-8")
        append_event(output_dir / "trace.jsonl", {"tool": "request", "phase": "received", "mode": mode,
                                                "request_status_source": "../audit_index.json"})
        acquired = False
        status = "COMPLETED"
        try:
            if runner is None:
                durable_json(evidence / "health.json", shared_model_ready(absolute))
                admission = check_baseline_admission(_model_config(), request["prompt"], interface,
                    evidence / "baseline_tokenizer", max(.01, min(8, absolute-time.monotonic()-1)))
                durable_json(evidence / "baseline_admission.json", admission)
            acquired = LOCK.acquire(timeout=max(0, absolute - time.monotonic() - 1))
            if not acquired:
                raise TimeoutError("WALLCLOCK_TIMEOUT")
            remaining = absolute - time.monotonic() - 1
            if remaining <= 0:
                raise TimeoutError("WALLCLOCK_TIMEOUT")
            env = dict(os.environ)
            env["AGENT_DEADLINE_S"] = str(remaining)
            env["AGENT_DEADLINE_MONOTONIC"] = str(absolute - 1)
            env["TRACK"] = "rtl"
            env["AGENT_TRACE_CACHE"] = str(cache.path)
            env["AGENT_TRACE_FILE"] = str(output_dir / "trace.jsonl")
            command = ["bash", str(script), str(input_dir), str(output_dir)]
            if runner is not None:
                runner(command, cwd=ROOT, env=env, timeout=remaining)
            else:
                proc = subprocess.Popen(command, cwd=ROOT, env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=(os.name != "nt"))
                from .task_process import TaskProcess
                PROCESSES[proc.pid] = proc
                scope = None
                try:
                    scope = TaskProcess(proc)
                    proc.wait(timeout=remaining)
                    append_event(output_dir / "trace.jsonl", {"tool": "task_process", "phase": "exit", "rc": proc.returncode})
                    if proc.returncode != 0:
                        raise RuntimeError("TASK_PROCESS_EXIT_" + str(proc.returncode))
                except subprocess.TimeoutExpired:
                    terminate_tree(proc, grace=max(0.01, min(0.5, absolute - time.monotonic())))
                    raise TimeoutError("WALLCLOCK_TIMEOUT")
                finally:
                    try:
                        if scope is None:
                            terminate_tree(proc, grace=.05)
                            cleanup = {"remaining": [proc.pid] if proc.poll() is None else [],
                                       "reason": "TASK_OBSERVER_INITIALIZATION_FAILED"}
                        else:
                            cleanup = scope.close(absolute - .5)
                        append_event(output_dir / "trace.jsonl", {"tool": "task_cleanup", **cleanup})
                        if cleanup["remaining"]:
                            raise RuntimeError("TASK_CLEANUP_INCOMPLETE")
                    finally:
                        PROCESSES.pop(proc.pid, None)
            if time.monotonic() >= absolute:
                raise TimeoutError("WALLCLOCK_TIMEOUT")
        except (TimeoutError, RequestTimeout):
            status = "WALLCLOCK_TIMEOUT"
            append_event(output_dir / "trace.jsonl", {"tool": "agent_stop", "status": "TIMEOUT", "reason": status})
        except Exception as exc:
            status = type(exc).__name__
            append_event(output_dir / "trace.jsonl", {"tool": "agent_stop", "status": "ERROR", "reason": status})
        finally:
            if acquired:
                LOCK.release()
        response = _bounded_finalization(evidence, request["task_id"], status, started, absolute, deadline, cache)
        if request_started is not None:
            response.delivery, context.delivery = context.delivery, None
        return response
    finally:
        CACHES.pop(cache_key, None)
        try:
            if cache is not None:
                try:
                    context.payload = cache.raw()
                finally:
                    cache.close()
        finally:
            if authority_context is None:
                context.close()


class Handler(BaseHTTPRequestHandler):
    def _send_budget(self, deadline):
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("WALLCLOCK_TIMEOUT during HTTP delivery")
            self.connection.settimeout(remaining)

    def _send(self, code: int, value: dict, deadline: float | None = None) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("WALLCLOCK_TIMEOUT before response encoding")
        raw = value.wire if isinstance(value, _WireResponse) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_budget(deadline)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self._send_budget(deadline)
            self.end_headers()  # buffered headers become a socket sendall here
            for offset in range(0, len(raw), 64 * 1024):
                self._send_budget(deadline)
                self.wfile.write(raw[offset:offset + 64 * 1024])
            self._send_budget(deadline)
        except Exception:
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            raise

    def _authorized(self) -> bool:
        try:
            expected = "Bearer " + _token()
        except RuntimeError:
            self._send(500, {"detail": "FPGACHINA_TOKEN not configured"})
            return False
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self._send(401, {"detail": "bad token"})
            return False
        return True

    def do_GET(self) -> None:
        if self.path != "/v1/health":
            self._send(404, {"detail": "unknown path"})
        elif self._authorized():
            try:
                self._send(200, shared_model_ready())
            except Exception as exc:
                self._send(503, {"ready": False, "track": "rtl", "detail": type(exc).__name__})

    def do_POST(self) -> None:
        request_started = time.monotonic()
        response = None
        request = {}
        context = _RequestContext()
        absolute = request_started + 5
        if self.path != "/v1/solve":
            self._send(404, {"detail": "unknown path"})
            return
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_100_000:
                raise ValueError("Invalid Content-Length")
            self.connection.settimeout(5)
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("JSON request must be an object")
            claimed = request.get("deadline_s", 900)
            if type(claimed) in (int, float) and 5 <= claimed <= 3600:
                absolute = request_started + float(claimed)
            response = solve_request(request, request_started=request_started, authority_context=context)
            absolute = request_started + float(request.get("deadline_s", 900))
            self._send(200, response, deadline=absolute)
            self._delivery(response, "SENT", absolute)
        except (ValueError, json.JSONDecodeError) as exc:
            if response is not None:
                self._delivery(response, "FAILED_" + type(exc).__name__, absolute)
                self.close_connection = True
            else:
                try:
                    response = _request_error(request, context, str(exc), absolute)
                    self._send(400, response, deadline=absolute)
                    self._delivery(response, "SENT", absolute)
                except Exception as send_error:
                    if response is not None:
                        self._delivery(response, "FAILED_" + type(send_error).__name__, absolute)
                    self.close_connection = True
        except Exception as exc:
            if response is not None:
                # Headers/body may already be partly sent. Never send a second
                # contradictory HTTP outcome or change the request authority.
                self._delivery(response, "FAILED_" + type(exc).__name__, absolute)
                self.close_connection = True
            else:
                try:
                    response = _request_error(request, context, type(exc).__name__, absolute)
                    self._send(503, response, deadline=absolute)
                    self._delivery(response, "SENT", absolute)
                except Exception as send_error:
                    if response is not None:
                        self._delivery(response, "FAILED_" + type(send_error).__name__, absolute)
                    self.close_connection = True
        finally:
            context.close()

    def _delivery(self, response, state, absolute):
        from rtl_agent.bounded_io import job
        if hasattr(response, "delivery"):
            delivery = response.delivery
            del response.delivery
            try:
                delivery.mark("S" if state == "SENT" else "T" if "timeout" in state.casefold() else "F")
            finally:
                delivery.close()
        if hasattr(response, "evidence") and time.monotonic() < absolute:
            job("serve.authority", {"action": "delivery", "evidence": str(response.evidence),
                "value": {"state": state, "request_status_source": "audit_index.json",
                          "state_source": "http_delivery.state", "register_durability": "OS_COHERENT_NOT_FSYNC_CONFIRMED",
                          "request_status": response["request_status"]}}, absolute)


def main() -> None:
    from .submission import Handler as SubmissionHandler, Jobs
    from .task_process import become_subreaper
    _token()
    port = int(os.environ.get("AGENT_HTTP_PORT", "7860"))
    if not 1024 <= port <= 65535:
        raise ValueError("AGENT_HTTP_PORT outside allowed range")
    jobs = Jobs()
    server = ThreadingHTTPServer(("127.0.0.1", port), SubmissionHandler)
    server.daemon_threads = True
    server.jobs = jobs
    subreaper = become_subreaper()
    if os.name == "posix" and not subreaper:
        server.server_close()
        jobs.close()
        raise RuntimeError("Linux child subreaper could not be enabled")
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
        for proc in list(PROCESSES.values()):
            terminate_tree(proc, grace=.05)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"RTL submission API listening on 127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        jobs.close()
        server.server_close()


if __name__ == "__main__":
    main()
