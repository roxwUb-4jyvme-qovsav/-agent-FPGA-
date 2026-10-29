"""Official run.sh adapter: public input directory to solution.v and trace.jsonl."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import signal
import threading

from .config import load_agent_config
from rtl_agent.feedback_agent_v24 import generate_agent_v24
from rtl_agent.local_model import load_model_config, check_model_service
from rtl_agent.official_trace import write_official_trace
from rtl_agent.core import execute
from rtl_agent.runtime_audit import append_event, cancel_processes, durable_json, expire_processes

ROOT = Path(__file__).resolve().parents[1]


def check_baseline_admission(*args, **kwargs):
    # Delay the Release-only import until after test namespace isolation.
    from rtl_agent.serving_contract import check_baseline_admission as check
    return check(*args, **kwargs)


def _model_config() -> dict:
    from rtl_agent.serving_contract import load_generation
    alias = os.environ.get("MODEL_NAME") or os.environ.get("LLM_MODEL")
    if not alias:
        raise ValueError("MODEL_NAME is required and must match the local serving alias")
    if os.environ.get("LLM_MODEL") and os.environ["LLM_MODEL"] != alias:
        raise ValueError("LLM_MODEL and MODEL_NAME must identify the same frozen model")
    base = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    if not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):[0-9]+/v1/?", base):
        raise ValueError("LLM_BASE_URL must be a loopback OpenAI-compatible /v1 endpoint")
    context = int(os.environ.get("AGENT_CONTEXT_TOKENS", "24576"))
    if context <= 16384:
        raise ValueError("Serving context must exceed the official baseline's 16384-token output request")
    data = {"model": alias, "base_url": base, "temperature": 0.0, "top_p": 1.0,
            "max_tokens": int(os.environ.get("AGENT_MAX_OUTPUT_TOKENS", "4096")),
            "timeout_s": float(os.environ.get("AGENT_MODEL_TIMEOUT_S", "240")),
            "seed": 20260917, "context_tokens": context,
            "prompt_mode": "public_spec_only", "generation": load_generation()}
    # Keep the existing strict local-service validation without a hard-coded host.
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "model.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return load_model_config(path)


def _vivado_bin() -> str | None:
    configured = os.environ.get("AGENT_VIVADO_BIN")
    if configured:
        return str(Path(configured).resolve())
    root = os.environ.get("XILINX_VIVADO")
    if root:
        return str((Path(root) / "bin").resolve())
    return None


def _vivado_version(scratch: Path, bin_dir: str | None, deadline: float = float("inf")) -> str:
    exe = shutil.which("vivado", path=bin_dir)
    if not exe:
        return "UNAVAILABLE"
    try:
        log = scratch / "vivado_version.log"
        output = execute([exe, "-version"], scratch, log, max(0.01, min(12, deadline - time.monotonic())))
        match = re.search(r"Vivado\s+v?(\d{4}\.\d+)", log.read_text(errors="replace"), re.I)
        return match.group(1) if match else "UNKNOWN"
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"


def _retain_eda_evidence(local_root: Path, destination: Path, deadline: float) -> dict:
    """Supervise a cancellable worker; never enumerate scratch in the owner."""
    from rtl_agent.bounded_io import job
    manifest = {"status": "INCOMPLETE", "phase": "RETENTION_STARTED",
                "reason": "RETENTION_NOT_COMMITTED", "files": {}, "omitted": [],
                "unknown_scope": str(local_root), "affected_path": str(local_root)}
    # The entry wrote its request-level manifest before scratch was created.
    # Per-work initialization is inside the supervised worker, not unbounded
    # parent I/O. An already expired call returns the earlier manifest scope.
    raw, error = job("agent.retain", {"root": str(local_root), "destination": str(destination),
                                     "absolute": deadline}, deadline)
    if raw is not None and len(raw) <= 65536:
        return json.loads(raw)
    manifest.update(reason=error or "RETENTION_PROTOCOL_ERROR", worker_terminated=True,
                    missing_scope="all paths not confirmed in the durable progress manifest")
    return manifest


def run(input_dir: Path, out_dir: Path) -> dict:
    started = time.monotonic()
    absolute = min(started + float(os.environ.get("AGENT_DEADLINE_S", "360")),
                   float(os.environ.get("AGENT_DEADLINE_MONOTONIC", "inf")))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "solution.v").write_text("", encoding="utf-8")
    append_event(out_dir / "trace.jsonl", {"tool": "request", "phase": "preflight", "deadline": absolute})
    # The only task input is public prompt.txt. Never enumerate adjacent files.
    prompt_file = input_dir / "prompt.txt"
    if not prompt_file.is_file():
        raise ValueError("prompt.txt is required")
    model = _model_config()
    append_event(out_dir / "trace.jsonl", {"tool": "model_service_check", "phase": "start"})
    health = check_model_service(dict(model, timeout_s=min(model["timeout_s"], max(.01, absolute-time.monotonic()))))
    (out_dir / "model_service.json").write_bytes((json.dumps(health, indent=2)+"\n").encode())
    append_event(out_dir / "trace.jsonl", {"tool": "model_service_check", "phase": "end", "status": health["status"], "sec": health["elapsed_s"]})
    if health["status"] != "READY" or health["available_models"] != [model["model"]]:
        raise ValueError("Agent and official baseline require the same single served model")
    admission = check_baseline_admission(model, prompt_file.read_text(encoding="utf-8"),
        (input_dir / "interface.txt").read_text(encoding="utf-8") if (input_dir / "interface.txt").is_file() else "",
        out_dir / "baseline_tokenizer", min(8, max(.01, absolute-time.monotonic())))
    durable_json(out_dir / "baseline_admission.json", admission)
    agent = load_agent_config(ROOT / "configs" / "agent_v24.json")
    deadline_s = float(os.environ.get("AGENT_DEADLINE_S", "360"))
    agent["max_wall_time_s"] = min(agent["max_wall_time_s"], max(0.01, absolute - time.monotonic() - 1))
    durable_json(out_dir / "eda_retention_request.json", {
        "status": "INCOMPLETE", "phase": "RETENTION_STARTED", "unknown_scope": "request scratch",
        "reason": "RETENTION_NOT_COMMITTED", "detail_source": "per-work eda_evidence_manifest.json",
        "provisional": True, "resolution": "per-work manifest takes precedence; fallback only if worker never initialized"})
    local_base = Path(os.environ.get("EDA_TMP", str(Path(tempfile.gettempdir()) / "eda")))
    local_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rtl-agent-", dir=local_base) as local:
        scratch = Path(local)
        existing_work = {p.name for p in out_dir.glob("agent_v24_*") if p.is_dir()}
        bin_dir = _vivado_bin()
        version = _vivado_version(scratch, bin_dir, absolute - 1)
        append_event(out_dir / "trace.jsonl", {"tool": "tool_versions", "vivado_version": version})
        if time.monotonic() >= absolute - 1:
            raise TimeoutError("WALLCLOCK_TIMEOUT")
        required_version = os.environ.get("AGENT_REQUIRE_VIVADO_VERSION")
        if required_version and version != required_version:
            raise RuntimeError(f"Vivado {required_version} required; detected {version}")
        # RTL judging normally sends an empty interface. A nonempty field is
        # still public input and may be used, but no other task file is read.
        interface_file = input_dir / "interface.txt"
        interface = interface_file.read_text(encoding="utf-8") if interface_file.is_file() else ""
        if interface.strip():
            spec = scratch / "public_prompt.txt"
            spec.write_text(prompt_file.read_text(encoding="utf-8") +
                            "\n\nExplicit public interface:\n" + interface, encoding="utf-8")
        else:
            spec = prompt_file
        agent["max_wall_time_s"] = min(agent["max_wall_time_s"], max(0.01, absolute - time.monotonic() - 1))
        result = None
        try:
            result = generate_agent_v24(spec, model, agent, out_dir,
                                        vivado_bin=bin_dir, eda_root=scratch / "candidate_eda")
        finally:
            created = sorted((p for p in out_dir.glob("agent_v24_*")
                              if p.is_dir() and p.name not in existing_work), key=lambda p: p.name)
            work_for_evidence = (Path(result["work_dir"]) if result else
                                 created[-1] if created else out_dir / "partial_agent")
            archive = _retain_eda_evidence(scratch / "candidate_eda", work_for_evidence / "eda",
                                           absolute - .25)
            append_event(out_dir / "trace.jsonl", {"tool": "evidence_archive", "phase": "end",
                                               "status": archive["status"], "reason": archive["reason"],
                                               "manifest": str(work_for_evidence / "eda_evidence_manifest.json")})
        if archive["status"] != "COMPLETE":
            if archive["reason"] == "PROCESS_INTERRUPTED":
                raise InterruptedError("PROCESS_INTERRUPTED")
            raise TimeoutError("WALLCLOCK_TIMEOUT") if archive["reason"] == "WALLCLOCK_TIMEOUT" else RuntimeError("EVIDENCE_ARCHIVE_INCOMPLETE")
        work = Path(result["work_dir"])
        final = result.get("final_candidate")
        if result.get("status") == "GENERATED" and final and Path(final).is_file():
            (out_dir / "solution.v").write_bytes(Path(final).read_bytes())
        write_official_trace(work, out_dir / "trace.jsonl", result, model["model"], version)
        summary = {"agent_version": "2.4", "vivado_version": version,
                   "model": model["model"], "model_context": model["context_tokens"],
                   "stop_reason": result.get("stop_reason"), "status": result.get("status"),
                   "solution_sha256": hashlib.sha256((out_dir / "solution.v").read_bytes()).hexdigest()}
        (out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output)
    started = time.monotonic()
    old_signals = {}
    old_alarm = None
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_signals[sig] = signal.signal(sig, cancel_processes)
        if hasattr(signal, "SIGALRM"):
            old_alarm = signal.signal(signal.SIGALRM, expire_processes)
            end = min(started + float(os.environ.get("AGENT_DEADLINE_S", "360")),
                      float(os.environ.get("AGENT_DEADLINE_MONOTONIC", "inf")))
            signal.setitimer(signal.ITIMER_REAL, max(.001, end - time.monotonic()))
    try:
        run(Path(args.input), output)
    except Exception as exc:
        output.mkdir(parents=True, exist_ok=True)
        (output / "solution.v").write_text("", encoding="utf-8")
        with (output / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"tool": "agent_stop", "status": "ERROR",
                                     "reason": "PROCESS_INTERRUPTED" if isinstance(exc, InterruptedError) else
                                     "WALLCLOCK_TIMEOUT" if isinstance(exc, TimeoutError) else type(exc).__name__, "detail": str(exc)[:300],
                                     "sec": round(time.monotonic() - started, 3)},
                                    ensure_ascii=False) + "\n")
        print(f"agent failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        if old_alarm is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_alarm)
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
