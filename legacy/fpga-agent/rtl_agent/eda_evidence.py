"""Best-effort EDA receipts. Never reinterpret a tool's outcome or read evaluator assets."""
from __future__ import annotations

import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

TOOLS = {"vivado", "xvlog", "xelab", "xsim"}
MAX_BYTES = 4 * 1024 * 1024
SAFE_ENV = ("PROCESSOR_ARCHITECTURE", "XILINX_VIVADO", "XILINX_TCLAPP_REPO")


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def redact(value):
    value = str(value)
    value = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1<REDACTED>", value)
    value = re.sub(r"(?i)((?:token|password|secret|api[_-]?key|license)\s*[=:]\s*)[^\s;]+", r"\1<REDACTED>", value)
    value = re.sub(r"\b(?:sk-|ghp_)[A-Za-z0-9_-]{20,}\b|\brc-[0-9a-fA-F]{48}\b", "<REDACTED>", value)
    return value[:2048]


def safe_command(command):
    answer, hide = [], False
    for arg in command:
        answer.append("<REDACTED>" if hide else redact(arg))
        hide = bool(re.fullmatch(r"(?i)--?(?:token|password|secret|api[_-]?key|license)", str(arg)))
    return answer


def resources(work):
    result = {"utc": utc(), "disk_free_bytes": None, "available_memory_bytes": None,
              "available_commit_bytes": None, "source": "NOT_PROVIDED"}
    try:
        result["disk_free_bytes"] = shutil.disk_usage(work).free
    except OSError:
        pass
    try:
        if os.name == "nt":
            class Memory(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("total", "available", "commit", "commit_free", "virtual", "virtual_free", "extended")]
            info = Memory(); info.length = ctypes.sizeof(info)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(info)):
                result.update(available_memory_bytes=info.available,
                              available_commit_bytes=info.commit_free, source="GlobalMemoryStatusEx")
        else:
            values = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, rest = line.split(":", 1)
                values[key] = int(rest.strip().split()[0]) * 1024
            result.update(available_memory_bytes=values.get("MemAvailable"), source="proc_meminfo")
            # CommitLimit - Committed_AS is not equivalent to Windows available virtual memory.
    except (OSError, ValueError, AttributeError):
        pass
    return result


def process_identity(proc):
    """Use the launched handle where possible; never look up an unverified fixed PID."""
    pid = getattr(proc, "pid", None)
    result = {"pid": pid if type(pid) is int and pid > 0 else None,
              "parent_pid": os.getpid() if type(pid) is int and pid > 0 else None,
              "parent_source": "Popen caller" if type(pid) is int and pid > 0 else "NOT_PROVIDED",
              "start_time_utc": None, "identity_verified": False,
              "start_time_source": "NOT_PROVIDED", "sampled_rss_bytes": None}
    handle = getattr(proc, "_handle", None)
    if os.name == "nt" and isinstance(handle, int) and not isinstance(handle, bool):
        try:
            creation, exited, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
            if ctypes.windll.kernel32.GetProcessTimes(ctypes.c_void_p(handle), ctypes.byref(creation),
                    ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                result.update(start_time_utc=datetime.datetime.fromtimestamp(
                    (creation.value - 116444736000000000) / 10000000,
                    datetime.timezone.utc).isoformat(), identity_verified=True, start_time_source="launched_process_handle")
        except (OSError, ValueError, AttributeError, OverflowError):
            pass
    elif os.name != "nt" and result["pid"]:
        try:
            # Verify caller parent and a live launched child before binding /proc data.
            if proc.poll() is None:
                stat = Path("/proc") / str(pid) / "stat"
                fields = stat.read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == os.getpid():
                    result.update(identity_verified=True, start_time_source="verified_proc_parent_and_start_ticks",
                                  start_ticks=int(fields[19]))
        except (OSError, ValueError, AttributeError):
            pass
    if os.name == "nt" and isinstance(handle, int) and not isinstance(handle, bool):
        try:
            class Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                    (n, ctypes.c_size_t) for n in ("peak_rss", "rss", "peak_pool_paged", "pool_paged", "peak_pool_nonpaged", "pool_nonpaged", "pagefile", "peak_pagefile")]
            counters = Counters(); counters.cb = ctypes.sizeof(counters)
            if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle), ctypes.byref(counters), counters.cb):
                result.update(sampled_rss_bytes=counters.rss, kernel_peak_rss_bytes=counters.peak_rss)
        except (OSError, ValueError, AttributeError):
            pass
    return result


class Capture:
    def __init__(self, command, work, log):
        self.work, self.log = Path(work).resolve(), Path(log).resolve()
        self.folder = self.work / "eda_execution" / (Path(command[0]).stem.lower() + "_" + uuid.uuid4().hex)
        self.owned = False
        self.data = {"schema_version": 1, "scope": "BEST_EFFORT_EXECUTION_EVIDENCE",
                     "capture_state": "STARTED", "command": safe_command(command), "cwd": redact(self.work), "requested_utc": utc(),
                     "stage": {"vivado": "synthesize", "xvlog": "analyze", "xelab": "elaborate", "xsim": "simulate"}.get(Path(command[0]).stem.lower()),
                     "tool_version": None, "part": None, "candidate": None,
                     "process": {"pid": None, "parent_pid": None, "start_time_utc": None, "exit_code": None},
                     "vendor_reported_helpers": [], "files": [], "missing": [], "errors": [],
                     "environment": {key: redact(os.environ[key]) for key in SAFE_ENV if key in os.environ},
                     "license_env_present": {key: bool(os.environ.get(key)) for key in ("LM_LICENSE_FILE", "XILINXD_LICENSE_FILE")},
                     "streams": {"stdout_stderr": "MERGED_ORIGINAL", "combined_path": redact(self.log),
                                 "stderr_separate": "NOT_PROVIDED", "reason": "Preserve existing merged bytes and diagnostic ordering"},
                     "resources_before": resources(self.work)}
        if not self.work.is_dir():
            raise FileNotFoundError("Execution work directory absent")
        if (self.work / "eda_execution").is_symlink():
            raise ValueError("Linked evidence directory refused")
        self.folder.mkdir(parents=True, exist_ok=False); self.owned = True
        for name in ("candidate.sv", "dut_0.sv", "synth.tcl", "timing.xdc"):
            p = self.work / name
            if p.is_file():
                self.snapshot(p, "before")
                if name in ("candidate.sv", "dut_0.sv") and p.stat().st_size <= MAX_BYTES:
                    captured = next((f for f in self.data["files"] if f["phase"] == "before" and Path(f["source"]).name == name), None)
                    self.data["candidate"] = {"path": redact(p), "sha256": captured["sha256"] if captured else None}
                if name == "synth.tcl" and p.stat().st_size <= MAX_BYTES:
                    match = re.search(r"synth_design\s+[^\n]*-part\s+([^\s;}]+)", (self.folder / "before" / name).read_text(errors="replace"))
                    self.data["part"] = match.group(1) if match else None
        self.persist()

    def persist(self):
        if not self.owned:
            return
        temporary = self.folder / "metadata.json.partial"
        temporary.write_bytes((json.dumps(self.data, indent=2) + "\n").encode())
        os.replace(temporary, self.folder / "metadata.json")

    def snapshot(self, source, phase):
        source = Path(source)
        try:
            if source.is_symlink() or source.stat().st_size > MAX_BYTES:
                raise ValueError("LINK_OR_SIZE_LIMIT")
            source.resolve().relative_to(self.work)
            data = source.read_bytes()
            if len(data) > MAX_BYTES:
                raise ValueError("SIZE_LIMIT")
            target = self.folder / phase / source.name
            target.parent.mkdir(exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            self.data["files"].append({"source": redact(source), "saved_path": redact(target),
                                       "relative_saved_path": target.relative_to(self.work).as_posix(),
                                       "phase": phase, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        except (OSError, ValueError) as exc:
            self.data["missing"].append({"file": redact(source), "phase": phase, "reason": type(exc).__name__})

    def launched(self, proc):
        self.proc = proc
        self.data["process"] = process_identity(proc)
        try:
            self.persist()
        except OSError as exc:
            self.data["errors"].append(type(exc).__name__)

    def finish(self, outcome):
        self.data.update(capture_state="FINISHED", finished_utc=utc(), elapsed_s=outcome.get("elapsed_s"),
                         resources_after=resources(self.work), timed_out=outcome.get("timed_out"),
                         launch_error_type=outcome.get("launch_exception_type"))
        if hasattr(self, "proc"):
            self.data["process_after"] = process_identity(self.proc)
        code = outcome.get("returncode")
        self.data["process"]["exit_code"] = code if type(code) is int else None
        observed = []
        paths = [self.log] + [self.work / n for n in ("vivado.log", "vivado.jou", "xvlog.log", "xelab.log", "xsim.log")]
        for source in dict.fromkeys(paths):
            if source.is_file():
                self.snapshot(source, "after")
                if source.stat().st_size <= MAX_BYTES:
                    observed.append(source.read_text(errors="replace"))
            else:
                self.data["missing"].append({"file": redact(source), "phase": "after", "reason": "NOT_PROVIDED"})
        text = "\n".join(observed)
        version = re.search(r"(?i)(?:Vivado|xvlog|xelab|xsim)\s+v?(\d{4}\.\d+)", text)
        self.data["tool_version"] = version.group(1) if version else None
        self.data["raw_error_lines"] = [redact(line) for line in text.splitlines()
                                        if re.match(r"(?i)^\s*(?:ERROR:|FATAL|INTERNAL ERROR)", line)][:40]
        self.data["vendor_reported_helpers"] = [{"pid": int(pid), "parent_pid": None,
            "start_time_utc": None, "exit_code": None, "identity_verified": False,
            "source": "vendor log only; no PID lookup or reused-PID attribution"}
            for pid in sorted(set(re.findall(r"Helper process launched with PID (\d+)", text)))]
        self.persist()
        return {"status": "PARTIAL" if self.data["missing"] or self.data["errors"] else "SAVED",
                "metadata": str(self.folder / "metadata.json"),
                "metadata_relative_to_work": (self.folder / "metadata.json").relative_to(self.work).as_posix(),
                "path_scope": "original_scratch; durable saved paths are in eda_evidence_manifest.file_details",
                "scope": "best_effort_not_a_verdict"}


def begin(command, work, log):
    if not command or Path(command[0]).stem.lower() not in TOOLS:
        return None
    try:
        return Capture(command, work, log)
    except Exception as exc:
        # Failure is reported in the primary execution result, never thrown over a tool outcome.
        return {"status": "INCOMPLETE", "metadata": None, "reason": "CAPTURE_INITIALIZATION_FAILED", "error_type": type(exc).__name__}


def launched(capture, proc):
    if isinstance(capture, Capture):
        try:
            capture.launched(proc)
        except Exception:
            capture.data["errors"].append("PROCESS_INFORMATION_NOT_PROVIDED")


def finish(capture, outcome):
    if isinstance(capture, Capture):
        try:
            return capture.finish(outcome)
        except Exception as exc:
            return {"status": "INCOMPLETE", "metadata": str(capture.folder / "metadata.json"),
                    "reason": "CAPTURE_FINALIZATION_FAILED", "error_type": type(exc).__name__}
    return capture


def annotate(receipt, status, reason):
    """Attach the existing classifier's decision; never feed capture status to the scheduler."""
    if not isinstance(receipt, dict) or not receipt.get("metadata"):
        return
    try:
        p = Path(receipt["metadata"])
        if p.is_symlink() or p.stat().st_size > MAX_BYTES:
            return
        data = json.loads(p.read_text())
        data["eda_classification"] = {"status": status, "reason": redact(reason)}
        staged = p.with_suffix(".json.partial")
        staged.write_bytes((json.dumps(data, indent=2) + "\n").encode())
        os.replace(staged, p)
    except Exception:
        receipt["annotation"] = "NOT_PROVIDED"