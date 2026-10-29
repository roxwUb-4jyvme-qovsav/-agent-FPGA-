"""Durable call journal and cancellation for the submission adapter only."""
from __future__ import annotations

from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time

ACTIVE = ContextVar("submission_audit", default=None)
PROCESSES = {}


def durable_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    os.replace(tmp, path)


def append_event(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    from .trace_cache import confirmed_event
    confirmed_event(path, value)


def emit(tool: str, phase: str, **fields) -> None:
    journal = ACTIVE.get()
    if journal:
        journal.event(tool, phase, **fields)


def account_response(response: dict) -> None:
    journal = ACTIVE.get()
    if journal:
        journal.account(response)


def terminate_tree(proc, grace: float = 1.0) -> None:
    """Collect descendants before termination, including separate EDA sessions."""
    if os.name == "nt":
        from .core import stop_tree
        stop_tree(proc)
        return
    parents = {}
    birth = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                parents[int(entry.name)] = int(stat[1])
                birth[int(entry.name)] = stat[19]
            except (OSError, ValueError, IndexError):
                pass
    descendants = {proc.pid}
    while True:
        expanded = descendants | {pid for pid, parent in parents.items() if parent in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in sorted(descendants, reverse=True):
            try:
                current = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
                if birth.get(pid) != current[19]:
                    continue
                os.kill(pid, sig)
            except (ProcessLookupError, FileNotFoundError, IndexError):
                pass
        if sig == signal.SIGTERM:
            try:
                proc.wait(timeout=max(0.01, grace))
            except Exception:
                pass
    try:
        proc.wait(timeout=1)
    except Exception:
        pass


class Journal:
    def __init__(self, work: Path, output: Path, ledger: dict, totals: dict,
                 costs: dict, model: str, deadline: float):
        self.work, self.output = work, output
        self.ledger, self.totals, self.costs = ledger, totals, costs
        self.deadline = deadline
        self.round = None
        self.accounted = False
        self.processes = {}
        self.calls = 0
        self.started_calls = {}
        self.call_ids = {}
        self.ledger["phase_wallclock_s"] = {}
        self.event("agent_meta", "start", model=model, agent_version="2.4",
                   public_spec_only=True, accounting="write_ahead")

    def snapshot(self):
        self.ledger["actual_eda_tool_calls"] = sum(self.costs.get(k, 0) for k in
                                                  ("xvlog", "xelab", "xsim", "synth_design"))
        durable_json(self.work / "budget_ledger.json", self.ledger)

    def event(self, tool, phase, **fields):
        key = (self.round, tool)
        if phase == "start":
            self.started_calls[key] = time.monotonic()
            self.calls += 1
            self.call_ids[key] = self.calls
        elif phase in ("end", "response") and key in self.started_calls:
            elapsed = time.monotonic() - self.started_calls.pop(key)
            self.ledger["phase_wallclock_s"][tool] = self.ledger["phase_wallclock_s"].get(tool, 0) + elapsed
            fields.setdefault("sec", elapsed)
        if phase == "start":
            if tool == "llm":
                self.accounted = False
                self.ledger["unknown_usage_calls"] = self.ledger.get("unknown_usage_calls", 0) + 1
            if tool in ("xvlog", "xelab", "xsim", "synth_design"):
                self.ledger["eda_launch_attempts"] = self.ledger.get("eda_launch_attempts", 0) + 1
        if phase == "launched" and tool in ("xvlog", "xelab", "xsim", "synth_design"):
            self.costs[tool] += 1
        record = {"ts": time.time(), "tool": tool, "phase": phase,
                  "round": self.round, "call_id": self.call_ids.get(key), **fields}
        append_event(self.output / "trace.jsonl", record)
        append_event(self.work / "trajectory.jsonl", {"event": "call", **record})
        self.snapshot()

    def account(self, response):
        if self.accounted:
            return
        usage = response.get("usage")
        valid = (isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                                for k in self.totals) and
                 usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
        if valid:
            for key in self.totals:
                self.totals[key] += usage[key]
            self.accounted = True
            self.ledger["unknown_usage_calls"] = max(0, self.ledger.get("unknown_usage_calls", 0) - 1)
        self.event("llm", "response", tokens_in=usage.get("prompt_tokens") if valid else None,
                   tokens_out=usage.get("completion_tokens") if valid else None,
                   usage_known=valid, response_sha256=hashlib.sha256(
                       json.dumps(response, sort_keys=True).encode()).hexdigest())

    def cancel(self, signum, frame):
        self.ledger["stop_reason"] = "PROCESS_INTERRUPTED"
        self.event("error", "signal", signal=signum, reason="PROCESS_INTERRUPTED")
        for proc in list(self.processes.values()):
            terminate_tree(proc)
        raise InterruptedError("Submission interrupted by signal")


def cancel_processes(signum, frame):
    for proc in list(PROCESSES.values()):
        terminate_tree(proc)
    raise InterruptedError("PROCESS_INTERRUPTED")


def expire_processes(signum, frame):
    journal = ACTIVE.get()
    if journal:
        journal.ledger["stop_reason"] = "WALLCLOCK_TIMEOUT"
        journal.event("error", "timeout", reason="WALLCLOCK_TIMEOUT")
    for proc in list(PROCESSES.values()):
        terminate_tree(proc, grace=.1)
    raise TimeoutError("WALLCLOCK_TIMEOUT")


def install_signal_handlers(journal):
    old = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            old[sig] = signal.signal(sig, journal.cancel)
    return old
