"""Conservative, public-spec-only behavioral probes for Agent v2.4.

Supports complete single-bit truth tables and a narrow explicit public grammar.
Anything else abstains. These probes are diagnostic evidence, not an oracle.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
import re
import time

from .core import execute, tool_paths, write_json
from .interface_contract import extract_public_contract
from .public_semantic_probes_v24 import derive_public_probe, render_public_probe, parse_public_probe_log

SEQUENTIAL = re.compile(r"\b(clock|clk|flip[- ]?flop|register|sequential|posedge|negedge|latch|reset)\b", re.I)
TABLE_LINE = re.compile(r"^\s*\|\s*([01](?:\s*\|\s*[01])+)\s*\|\s*$")
ERROR = re.compile(r"(?im)^\s*(?:ERROR\s*:|FATAL\s*:|CRITICAL ERROR\s*:)")
LICENSE = re.compile(r"(?i)(license checkout failed|failed to check out.*license|no valid license|license.*not found)")
AUTHORITY_CONFLICT = re.compile(
    r"(?i)\b(incorrect|wrong|erroneous|obsolete|outdated|withdrawn|revoked|ignore|"
    r"disregard|supersed\w*|revis\w*|corrected|correction|instead|example|"
    r"illustrative|not\s+(?:the|a|valid|correct|required)|xor|exclusive.or)\b|"
    r"错误|撤销|作废|忽略|修订|更正|示例|替代|异或")


def derive_truth_table(spec: str) -> dict:
    contract = extract_public_contract(spec)
    base = {"status": "INDETERMINATE", "source": "public_spec_only",
            "requirement_source": "", "reason": "No unambiguous complete 1-bit combinational truth table"}
    if SEQUENTIAL.search(spec):
        base["reason"] = "Sequential semantics require an independent specification model"
        return base
    # A table is usable only when its authority is clear across the WHOLE
    # specification. Do not select a convenient table from conflicting prose.
    if AUTHORITY_CONFLICT.search(spec) or len(re.findall(r"(?i)truth\s+table", spec)) != 1:
        base["reason"] = "Public table authority is ambiguous, revised, illustrative or conflicting"
        return base
    ports = contract["ports"]
    if not contract["ports_complete"] or not ports or any(p["width"] != 1 or p["signed"] for p in ports):
        return base
    inputs = [p["name"] for p in ports if p["direction"] == "input"]
    outputs = [p["name"] for p in ports if p["direction"] == "output"]
    if not 1 <= len(inputs) <= 4 or len(outputs) != 1 or len(ports) != len(inputs) + 1:
        return base
    lines = spec.splitlines()
    for start, line in enumerate(lines):
        if "truth table" not in line.lower():
            continue
        if not re.fullmatch(r"(?i)\s*(?:(?:public|required|complete)\s+)?truth table\s*:?\s*", line):
            base["reason"] = "Table heading authority cannot be established"
            return base
        if start + 2 >= len(lines):
            continue
        header = [s.strip() for s in lines[start + 1].strip().strip("|").split("|")]
        if header != inputs + outputs:
            continue
        separator = lines[start + 2].strip()
        if not re.fullmatch(r"\|?[\s|:-]+\|?", separator):
            continue
        rows = []
        for raw in lines[start + 3:]:
            match = TABLE_LINE.fullmatch(raw)
            if not match:
                break
            bits = [x.strip() for x in raw.strip().strip("|").split("|")]
            if len(bits) != len(header):
                break
            rows.append(bits)
        combinations = {tuple(bits[:-1]) for bits in rows}
        expected = set(itertools.product("01", repeat=len(inputs)))
        if len(rows) != 2 ** len(inputs) or combinations != expected:
            continue
        source = "\n".join(lines[start:start + 3 + len(rows)])
        table_lines = {i for i, raw in enumerate(lines) if TABLE_LINE.fullmatch(raw)}
        if table_lines != set(range(start + 3, start + 3 + len(rows))):
            base["reason"] = "Multiple or conflicting public tables"
            return base
        # Extra behavioral prose cannot be interpreted reliably by this narrow
        # truth-table parser. Accept only interface declarations and table data.
        outside = lines[:start] + lines[start + 3 + len(rows):]
        for raw in outside:
            sentence = raw.strip()
            if not sentence or re.fullmatch(r"- (?:input|output) [A-Za-z_][A-Za-z0-9_]*", sentence):
                continue
            if re.fullmatch(r"(?i)implement (?:a )?module(?: named)? [A-Za-z_][A-Za-z0-9_]*(?: with the following interface)?[.:]?", sentence):
                continue
            if re.fullmatch(r"(?i)All input and output ports are one bit unless otherwise specified\.?", sentence):
                continue
            base["reason"] = "Additional public prose has no independently established behavioral interpretation"
            return base
        return {"status": "READY", "source": "public_spec_only",
                "requirement_source": source, "requirement_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "inputs": inputs, "output": outputs[0], "rows": rows,
                "reason": "Complete explicit truth table with all input combinations"}
    return base


def render_testbench(plan: dict, top: str = "TopModule", reserved_names=None) -> str:
    if plan.get("status") != "READY" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top):
        raise ValueError("A complete public truth table and simple top name are required")
    inputs, output = plan["inputs"], plan["output"]
    used = set(inputs + [output, top]) | set(reserved_names or ())
    def private(preferred):
        name = preferred
        while name in used:
            name += "_private"
        used.add(name)
        return name
    tb_top = private("rtl_agent_selftest")
    dut = private("dut")
    counter = private("mismatches")
    decls = [f"  reg {name};" for name in inputs] + [f"  wire {output};"]
    connections = ", ".join(f".{name}({name})" for name in inputs + [output])
    checks = []
    for index, bits in enumerate(plan["rows"]):
        assigns = " ".join(f"{name} = 1'b{bit};" for name, bit in zip(inputs, bits[:-1]))
        checks.append(f"    {assigns} #10; if ({output} !== 1'b{bits[-1]}) begin {counter} = {counter} + 1; $display(\"RTL_AGENT_MISMATCH row={index}\"); end")
    return (f"`timescale 1ps/1ps\nmodule {tb_top};\n" + "\n".join(decls) +
            f"\n  {top} {dut}({connections});\n  integer {counter} = 0;\n  initial begin\n" +
            "\n".join(checks) +
            f"\n    if ({counter} == 0) $display(\"RTL_AGENT_SELFTEST_PASS\");\n"
            f"    else $display(\"RTL_AGENT_SELFTEST_FAIL %0d\", {counter});\n"
            "    $finish;\n  end\nendmodule\n")


def run_selftest(spec: str, candidate: Path, work: Path, deadline: float,
                 vivado_bin: str | None = None, top: str = "TopModule") -> dict:
    work.mkdir(parents=True, exist_ok=True)
    plan = derive_truth_table(spec)
    if plan['status'] != 'READY':
        public_plan = derive_public_probe(spec)
        if public_plan['status'] == 'READY':
            plan = public_plan
    write_json(work / "public_requirement.json", plan)
    result = {"status": "INDETERMINATE", "reason": plan["reason"],
              "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
              "requirement_sha256": plan.get("requirement_sha256"),
              "testbench_sha256": None, "stages": {}, "source": "public_spec_only"}
    if plan["status"] != "READY":
        write_json(work / "selftest_result.json", result)
        return result
    candidate_names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", candidate.read_text(encoding="utf-8", errors="replace")))
    public_probe = None
    if plan.get('kind'):
        public_probe = render_public_probe(plan, result['candidate_sha256'], top, candidate_names)
        tb, tb_top = public_probe['testbench'], public_probe['top']
        result['public_probe_kind'] = plan['kind']
        result['public_plan_sha256'] = plan['plan_sha256']
    else:
        tb = render_testbench(plan, top, candidate_names)
        tb_top = re.search(r"(?m)^module (\w+);", tb).group(1)
    result["testbench_top"] = tb_top
    with (work / "selftest.sv").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(tb)
    result["testbench_sha256"] = hashlib.sha256(tb.encode()).hexdigest()
    (work / "candidate.sv").write_bytes(candidate.read_bytes())
    paths = tool_paths(vivado_bin)
    if any(not paths[name] for name in ("xvlog", "xelab", "xsim")):
        result.update(status="SELFTEST_SUSPECT", reason="Vivado xsim toolchain unavailable")
        write_json(work / "selftest_result.json", result)
        return result
    commands = [("xvlog", [paths["xvlog"], "--sv", "candidate.sv", "selftest.sv"]),
                ("xelab", [paths["xelab"], tb_top, "-s", "rtl_agent_selftest_sim", "-timescale", "1ps/1ps"]),
                ("xsim", [paths["xsim"], "rtl_agent_selftest_sim", "-runall"])]
    for stage, command in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.update(status="INDETERMINATE", reason="Wall-clock budget exhausted")
            break
        process = execute(command, work, work / f"{stage}.log", min(remaining, 90.0))
        log = (work / f"{stage}.log").read_text(encoding="utf-8", errors="replace")
        result["stages"][stage] = {"returncode": process.get("returncode"),
                                     "elapsed_s": process.get("elapsed_s"),
                                     "timed_out": process.get("timed_out"),
                                     "log_sha256": hashlib.sha256((work / f"{stage}.log").read_bytes()).hexdigest(),
                                     "log_artifact": f"{stage}.log"}
        if process.get("timed_out"):
            result.update(status="INDETERMINATE", reason=f"{stage} timed out")
            break
        if process.get("launch_error") or LICENSE.search(log):
            result.update(status="SELFTEST_SUSPECT", reason=f"{stage} infrastructure or license failure")
            break
        if process.get("returncode") != 0 or ERROR.search(log):
            result.update(status="SELFTEST_SUSPECT", reason=f"Generated testbench or {stage} failed; candidate fault unproven")
            break
        if stage == "xsim":
            if public_probe:
                try:
                    observation = parse_public_probe_log(plan, public_probe['nonce'], log,
                                                         candidate_text=candidate.read_bytes().decode('utf-8',errors='replace'))
                except Exception:
                    observation = {'status':'SELFTEST_SUSPECT', 'reason':'Public probe observation could not be validated'}
                result.update(observation)
            elif "RTL_AGENT_SELFTEST_FAIL" in log:
                result.update(status="RTL_SUSPECT", reason="Candidate disagreed with public truth-table probe; not an oracle")
            elif "RTL_AGENT_SELFTEST_PASS" in log:
                result.update(status="SELFTEST_PASS", reason="Public truth-table probe passed; official L2 unproven")
            else:
                result.update(status="SELFTEST_SUSPECT", reason="xsim returned without a selftest completion marker")
    write_json(work / "selftest_result.json", result)
    return result
