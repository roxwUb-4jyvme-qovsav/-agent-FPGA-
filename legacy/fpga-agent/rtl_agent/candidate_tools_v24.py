"""DUT-only, staged Vivado checks for v2.4; no evaluator inputs accepted."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import time

from .core import PART, classify, execute, tool_paths, write_json
from .eda_evidence import annotate


def _tool_result(stage: str, command: list[str], work: Path, deadline: float,
                 timeout_s: float, top: str) -> dict:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"status": "TIMEOUT", "reason": "Agent deadline reached", "elapsed_s": 0.0}
    log_path = work / f"{stage}.stdout.log"
    process = execute(command, work, log_path, min(timeout_s, remaining))
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    vendor_log = work / ("vivado.log" if stage == "synthesize" else f"{stage}.log")
    if vendor_log.is_file() and vendor_log != log_path:
        text += "\n" + vendor_log.read_text(encoding="utf-8", errors="replace")
    status, reason = classify(stage, process, text, work, top)
    diagnostic_saved = True
    try:
        (work / f"{stage}.diagnostic.log").write_bytes(text.encode())
    except OSError:
        diagnostic_saved = False
    receipt = process.get("execution_evidence")
    annotate(receipt, status, reason)
    return {"status": status, "reason": reason, "execution_evidence": receipt,
            "diagnostic_saved": diagnostic_saved, "elapsed_s": process.get("elapsed_s", 0.0),
            "returncode": process.get("returncode"), "timed_out": process.get("timed_out"),
            "log_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "log_artifact": f"{stage}.diagnostic.log",
            "diagnostics": "\n".join(line[:500] for line in text.splitlines()
                                      if re.search(r"(?i)(ERROR|FATAL|CRITICAL WARNING)", line)
                                      and not line.lstrip().startswith("#"))[:4000]}


def check_compile(candidate: Path, work: Path, deadline: float,
                  vivado_bin: str | None = None, top: str = "TopModule") -> dict:
    """xvlog then DUT-only xelab; top declaration is the sole elaboration root."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
        raise ValueError("Invalid top module identifier")
    work.mkdir(parents=True, exist_ok=True)
    local = work / "candidate.sv"
    shutil.copyfile(candidate, local)
    paths = tool_paths(vivado_bin)
    result = {"status": "NOT_RUN", "stages": {}, "input_scope": "candidate_only",
              "candidate_sha256": hashlib.sha256(local.read_bytes()).hexdigest(),
              "top": top}
    if not paths["xvlog"] or not paths["xelab"]:
        result.update(status="INFRA_ERROR", reason="Missing xvlog or xelab")
    else:
        commands = [("analyze", [paths["xvlog"], "--sv", local.name]),
                    ("elaborate", [paths["xelab"], top, "-s", "rtl_agent_dut", "-timescale", "1ps/1ps"])]
        for stage, command in commands:
            record = _tool_result(stage, command, work, deadline, 60.0, top)
            result["stages"][stage] = record
            if record["status"] != "PASS":
                result.update(status=record["status"], reason=record["reason"], failed_stage=stage)
                break
        else:
            result.update(status="PASS", reason="Candidate-only xvlog/xelab passed")
    write_json(work / "compile_result.json", result)
    return result


def check_synthesis(candidate: Path, work: Path, deadline: float,
                    vivado_bin: str | None = None, top: str = "TopModule") -> dict:
    work.mkdir(parents=True, exist_ok=True)
    local = work / "candidate.sv"
    shutil.copyfile(candidate, local)
    paths = tool_paths(vivado_bin)
    result = {"status": "NOT_RUN", "input_scope": "candidate_only", "part": PART,
              "candidate_sha256": hashlib.sha256(local.read_bytes()).hexdigest()}
    if not paths["vivado"]:
        result.update(status="INFRA_ERROR", reason="Missing vivado")
    else:
        script = ("if {[info exists ::env(XILINX_TCLAPP_REPO)]} {\n"
                  "  lappend auto_path [file join $::env(XILINX_TCLAPP_REPO) support appinit]\n"
                  "  foreach app_dir [glob -nocomplain -types d [file join $::env(XILINX_TCLAPP_REPO) tclapp * *]] {\n"
                  "    lappend auto_path $app_dir\n"
                  "  }\n"
                  "  package require ::tclapp::support::appinit 1.2\n"
                  "}\n"
                  f"read_verilog -sv {local.name}\n"
                  f"synth_design -top {top} -part {PART}\n"
                  "write_verilog -force netlist.v\n"
                  "write_checkpoint -force design.dcp\n"
                  "puts RTL_AGENT_SYNTH_DONE\nexit 0\n")
        (work / "synth.tcl").write_text(script, encoding="utf-8")
        record = _tool_result("synthesize", [paths["vivado"], "-mode", "batch", "-source",
                                              "synth.tcl", "-journal", "vivado.jou", "-log", "vivado.log"],
                              work, deadline, 120.0, top)
        result.update(record)
    write_json(work / "synth_result.json", result)
    return result
