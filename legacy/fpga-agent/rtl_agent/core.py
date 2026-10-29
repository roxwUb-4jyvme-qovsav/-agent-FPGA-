"""Local smoke evaluator, not an official scorer or an untrusted-code sandbox."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import uuid

PART = "xczu3eg-sbva484-1-e"
TOOLS = ("vivado", "xvlog", "xelab", "xsim")
STAGES = ("analyze", "elaborate", "simulate", "synthesize")
ERROR = re.compile(r"(?im)^\s*(?:ERROR\s*:|FATAL(?:_ERROR)?\s*:|RTL_AGENT_FAIL\b)")
INFRA = re.compile(r"(?im)(license checkout failed|failed to check out.*license|could not acquire.*license|no valid license|valid license.*not found|Failed to load tclapp initializer|cannot access rtd files|Failed to open .*global_name\.straps\.rtd|unrecoverable error has occured|^\s*RTL_AGENT_INFRA:)")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def tool_paths(bin_dir: str | None = None) -> dict[str, str | None]:
    search = str(Path(bin_dir).resolve()) if bin_dir else None
    return {name: shutil.which(name, path=search) for name in TOOLS}


def load_task(path: Path, require_rtl: bool = True) -> dict:
    path = path.resolve()
    task = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("task_id", "top_module", "tb_top"):
        if not isinstance(task.get(key), str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", task[key]):
            raise ValueError(f"Invalid {key}: expected a simple identifier")
    if not isinstance(task.get("spec"), str) or not task["spec"].strip():
        raise ValueError("spec must be a non-empty string")
    # Reference testbench is evaluator-owned, not an Agent input.
    for key in ("rtl_files", "testbench_files"):
        if key == "rtl_files" and not require_rtl:
            task[key] = []  # Generation needs only the spec and evaluator contract.
            continue
        if not isinstance(task.get(key), list) or not task[key]:
            raise ValueError(f"{key} must be a non-empty list")
        resolved = []
        for item in task[key]:
            if not isinstance(item, str):
                raise ValueError(f"{key} paths must be strings")
            source = (path.parent / item).resolve()
            if not source.is_file() or source.suffix.lower() not in (".sv", ".v"):
                raise ValueError(f"Missing/unsupported RTL source: {source}")
            resolved.append(str(source))
        task[key] = resolved
    if set(task["rtl_files"]) & set(task["testbench_files"]):
        raise ValueError("DUT and testbench sources must be separate")
    clock = task.get("clock_port")
    if clock is not None and (not isinstance(clock, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clock)):
        raise ValueError("Invalid clock_port")
    timeout = task.setdefault("timeout_s", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_s must be finite and positive")
    overrides = task.setdefault("stage_timeouts_s", {})
    if not isinstance(overrides, dict) or set(overrides) - set(STAGES):
        raise ValueError("stage_timeouts_s must map known stage names to positive seconds")
    for value in overrides.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Stage timeouts must be finite and positive")
    task["manifest"] = str(path)
    return task


def stop_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        # Snapshot and stop descendants first, then wait for each one before
        # TemporaryDirectory cleanup.
        try:
            import psutil
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            for child in reversed(children):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            try:
                parent.kill()
            except psutil.NoSuchProcess:
                pass
            _, alive = psutil.wait_procs(children + [parent], timeout=8)
            if alive:
                raise OSError("Timed-out Windows process descendants remain alive")
        except ImportError:
            # This release target runs in Linux. Keep the module importable for
            # Windows-side packaging checks without depending on OS commands.
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    else:
        from .runtime_audit import terminate_tree
        terminate_tree(proc, grace=.1)
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def child_environment(command: list[str] | None = None) -> dict[str, str]:
    """Restore architecture metadata omitted by some Windows launch hosts."""
    env = os.environ.copy()
    if os.name == "nt" and not env.get("PROCESSOR_ARCHITECTURE"):
        import ctypes
        info = ctypes.create_string_buffer(64)  # larger than native SYSTEM_INFO
        ctypes.windll.kernel32.GetNativeSystemInfo(info)
        architecture = int.from_bytes(info.raw[:2], "little")
        name = {0: "x86", 9: "AMD64", 12: "ARM64"}.get(architecture)
        if name:
            env["PROCESSOR_ARCHITECTURE"] = name
    # A stale per-user Tcl Store catalog can make Vivado fall back incorrectly
    # even when the installation store is intact. Pin vendor processes to the
    # store belonging to the selected Vivado installation.
    if command and command[0]:
        executable = Path(command[0]).resolve()
        if executable.stem.lower() in TOOLS:
            store = executable.parent.parent / "data" / "XilinxTclStore"
            if store.is_dir():
                env["XILINX_TCLAPP_REPO"] = str(store)
    return env


def execute(command: list[str], cwd: Path, log: Path, timeout: float) -> dict:
    from .runtime_audit import ACTIVE, PROCESSES, emit
    from . import eda_evidence
    journal = ACTIVE.get()
    tool = Path(command[0]).stem.lower()
    tool = "synth_design" if tool == "vivado" else tool
    if journal and time.monotonic() >= journal.deadline:
        return {"command": command, "returncode": None, "timed_out": True, "elapsed_s": 0.0,
                "launched": False}
    if journal:
        timeout = min(timeout, max(0.01, journal.deadline - time.monotonic()))
    emit(tool, "start", usage_known=False)
    start = time.monotonic()
    result = {"command": command, "log": str(log), "returncode": None, "timed_out": False}
    # Windows may dispatch .bat through cmd; disallow its expansion/metacharacters.
    if os.name == "nt" and any(any(c in arg for c in '%!&|<>^\r\n') for arg in command):
        return {**result, "launch_error": "Unsupported shell metacharacter in command path", "elapsed_s": 0.0}
    capture = eda_evidence.begin(command, cwd, log)
    try:
        with log.open("wb") as stream:
            proc = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, env=child_environment(command),
                                    start_new_session=(os.name != "nt"))
            result["pid"] = proc.pid
            eda_evidence.launched(capture, proc)
            PROCESSES[proc.pid] = proc
            emit(tool, "launched", pid=proc.pid)
            if journal:
                journal.processes[proc.pid] = proc
            try:
                result["returncode"] = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                try:
                    stop_tree(proc)
                except (OSError, subprocess.SubprocessError) as exc:
                    result["cleanup_error"] = str(exc)
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=10)
                result["returncode"] = proc.returncode
            finally:
                PROCESSES.pop(proc.pid, None)
                if journal:
                    journal.processes.pop(proc.pid, None)
                if proc.poll() is None:
                    stop_tree(proc)
    except OSError as exc:
        result["launch_error"] = str(exc)
        result["launch_exception_type"] = type(exc).__name__
    result["elapsed_s"] = round(time.monotonic() - start, 3)
    if capture is not None:
        result["execution_evidence"] = eda_evidence.finish(capture, result)
    emit(tool, "end", rc=result.get("returncode"), sec=result["elapsed_s"],
         timed_out=result.get("timed_out"), launch_error=result.get("launch_error"))
    return result


def classify(stage: str, process: dict, transcript: str, work: Path, task_id: str) -> tuple[str, str]:
    # Vivado echoes Tcl source as '# ...'; an unexecuted error branch is not a diagnostic.
    transcript = "\n".join(line for line in transcript.splitlines() if not line.lstrip().startswith("#"))
    if process.get("launch_error"):
        return "INFRA_ERROR", process["launch_error"]
    if process.get("timed_out"):
        return "TIMEOUT", "Wall-clock deadline exceeded"
    if not transcript.strip() or "\x00" in transcript or "\ufffd" in transcript:
        return "INFRA_ERROR", "Missing or undecodable tool log; outcome cannot be trusted"
    if INFRA.search(transcript):
        return "INFRA_ERROR", "Tool environment, device, or license failure"
    if process.get("returncode") != 0 or ERROR.search(transcript):
        return "FAIL", "Nonzero exit or explicit error diagnostic"
    if stage == "simulate":
        verdict = work / "tb_verdict.txt"
        wanted = f"RTL_AGENT_PASS {task_id}"
        if not verdict.is_file() or verdict.read_text(encoding="utf-8", errors="replace").strip() != wanted:
            return "FAIL", "Trusted smoke testbench did not write its completion verdict"
        if not re.search(r"(?m)^INFO: \[Common 17-206\] Exiting xsim\b", transcript):
            return "INFRA_ERROR", "Testbench verdict exists but xsim exit record is missing"
    if stage == "synthesize":
        netlist = work / "netlist.v"
        checkpoint = work / "design.dcp"
        if not all(p.is_file() and p.stat().st_size > 0 for p in (netlist, checkpoint)):
            return "FAIL", "Missing synthesis artifacts"
        if not re.search(r"(?m)^RTL_AGENT_SYNTH_DONE\s*$", transcript):
            return "FAIL", "Synthesis completion marker absent"
    return "PASS", "Stage checks passed"


def prepare(task: dict, work: Path) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    hashes = {}
    for key, prefix in (("rtl_files", "dut"), ("testbench_files", "tb")):
        sources[key] = []
        for i, original in enumerate(task[key]):
            source = Path(original)
            name = f"{prefix}_{i}{source.suffix.lower()}"
            shutil.copyfile(source, work / name)
            sources[key].append(name)
            hashes[name] = {"source": original, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    write_json(work / "sources.json", hashes)
    (work / "simulate.tcl").write_text("run all\nquit\n", encoding="utf-8")
    # Windows vendor batch wrappers split unquoted '=' while forwarding args.
    (work / "simulate.args").write_text(f"-testplusarg TASK_ID={task['task_id']}\n", encoding="utf-8")
    # All inserted Tcl identifiers are validated; filenames are generated locally.
    if task.get("clock_port"):
        constraint = f"create_clock -name task_clk -period 5.000 [get_ports {{{task['clock_port']}}}]\n"
    else:
        constraint = "# Combinational smoke design: virtual clock; not a complete I/O timing model.\ncreate_clock -name virtual_clk -period 5.000\n"
    (work / "timing.xdc").write_text(constraint, encoding="utf-8")
    reads = "\n".join(f"read_verilog {'-sv ' if x.endswith('.sv') else ''}{{{x}}}" for x in sources["rtl_files"])
    script = f'''if {{[info exists ::env(XILINX_TCLAPP_REPO)]}} {{
    lappend auto_path [file join $::env(XILINX_TCLAPP_REPO) support appinit]
    foreach app_dir [glob -nocomplain -types d [file join $::env(XILINX_TCLAPP_REPO) tclapp * *]] {{
        lappend auto_path $app_dir
    }}
    package require ::tclapp::support::appinit 1.2
}}
if {{[llength [get_parts -quiet {{{PART}}}]] == 0}} {{
    puts "RTL_AGENT_INFRA: target part is not installed"
    exit 2
}}
if {{[catch {{
    {reads}
    read_xdc timing.xdc
    synth_design -top {task['top_module']} -part {PART}
    write_verilog -force netlist.v
    write_checkpoint -force design.dcp
    report_utilization -file utilization.rpt
}} detail]}} {{
    puts "ERROR: $detail"
    exit 1
}}
puts "RTL_AGENT_SYNTH_DONE"
exit 0
'''
    (work / "synth.tcl").write_text(script, encoding="utf-8")
    return sources


def evaluate(task_path: Path, output: Path, bin_dir: str | None = None, dry_run: bool = False) -> dict:
    task = load_task(task_path)
    work = output.resolve() / (task["task_id"] + "_" + uuid.uuid4().hex[:12])
    work.mkdir(parents=True, exist_ok=False)
    sources = prepare(task, work)
    write_json(work / "task_resolved.json", task)
    tools = tool_paths(bin_dir)
    exe = {k: v or k for k, v in tools.items()}
    commands = {
        "analyze": [exe["xvlog"], "--sv", *sources["rtl_files"], *sources["testbench_files"]],
        "elaborate": [exe["xelab"], task["tb_top"], "-s", "smoke_snapshot", "--timescale", "1ns/1ps"],
        "simulate": [exe["xsim"], "smoke_snapshot", "-tclbatch", "simulate.tcl", "-onerror", "quit", "-f", "simulate.args"],
        "synthesize": [exe["vivado"], "-mode", "batch", "-source", "synth.tcl", "-nojournal", "-log", "vivado.log"],
    }
    result = {"schema_version": 1, "task_id": task["task_id"], "mode": "dry_run" if dry_run else "real",
              "part": PART, "clock_period_ns": 5.0, "work_dir": str(work),
              "status": "NOT_RUN", "stages": {s: {"status": "NOT_RUN", "command": commands[s]} for s in STAGES}}
    start = time.monotonic()
    if dry_run:
        result["reason"] = "Commands prepared only; no EDA validation performed"
    elif any(value is None for value in tools.values()):
        result["status"] = "INFRA_ERROR"
        result["reason"] = "Missing tools: " + ", ".join(k for k, v in tools.items() if v is None)
    else:
        result["status"] = "PASS"
        for stage in STAGES:
            log = work / f"{stage}.stdout.log"
            timeout = task["stage_timeouts_s"].get(stage, task["timeout_s"])
            process = execute(commands[stage], work, log, timeout)
            process["timeout_s"] = timeout
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            # Also inspect the tool log, not just console forwarding.
            for name in {"analyze": ["xvlog.log"], "elaborate": ["xelab.log"],
                         "simulate": ["xsim.log"], "synthesize": ["vivado.log"]}[stage]:
                p = work / name
                if p.exists():
                    text += "\n" + p.read_text(encoding="utf-8", errors="replace")
            status, reason = classify(stage, process, text, work, task["task_id"])
            result["stages"][stage] = {**process, "status": status, "reason": reason}
            if status != "PASS":
                result["status"] = status
                break
    result["elapsed_s"] = round(time.monotonic() - start, 3)
    write_json(work / "result.json", result)
    return result
