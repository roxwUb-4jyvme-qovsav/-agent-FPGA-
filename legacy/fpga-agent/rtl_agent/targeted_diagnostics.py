"""Candidate-only structural/EDA diagnostics for Agent v2.3 repair."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re


_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_DIAGNOSTIC = re.compile(r"(?i)\b(?:ERROR|FATAL|CRITICAL WARNING)\s*:|\bRTL_AGENT_")
_LOCATION = (
    re.compile(r"(?i)\b(?:candidate\.s?v|candidate\.v|[^\s\\/:]+\.s?v)\s*[:(]\s*(\d+)"),
    re.compile(r"(?i)\bline\s+(\d+)\b"),
    re.compile(r"\[(?:[^\]]*?:)?(\d+)(?::\d+)?\]"),
)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")

INTERFACE_GOALS = {
    "TOP_MODULE_MISSING": "Declare the exact public top module name and retain all required ports.",
    "TOP_MODULE_DUPLICATE": "Keep exactly one declaration of the public top module.",
    "PORT_MISSING": "Add the missing public port to the top module declaration.",
    "PORT_UNEXPECTED": "Remove the extra top-level port while preserving the required public ports.",
    "PORT_DIRECTION_MISMATCH": "Use the required input/output/inout direction for this port.",
    "PORT_WIDTH_MISMATCH": "Change this port's declared packed width to the required number of bits.",
    "PORT_SIGNED_MISMATCH": "Match the public signed qualifier on this port declaration.",
    "REQUIRED_SUBMODULE_MISSING": "Define or instantiate the explicitly required submodule as specified.",
}

EDA_RULES = (
    ("MISSING_MODULE", re.compile(r"(?i)(?:module|design unit|cell).*?(?:not found|undefined|unresolved|cannot find)"),
     "Define the referenced helper module or correct its name/instantiation."),
    ("UNKNOWN_IDENTIFIER", re.compile(r"(?i)(?:undeclared|not declared|unknown identifier|cannot find (?:signal|variable)|not found in scope)"),
     "Declare the referenced signal or correct the misspelled identifier at the reported location."),
    ("PORT_CONNECTION", re.compile(r"(?i)(?:port .*?(?:not found|does not exist|cannot be connected)|too (?:many|few) port|invalid port connection)"),
     "Match the instantiated module's declared port names, directions and connection widths."),
    ("MULTIPLE_DRIVERS", re.compile(r"(?i)(?:multiple drivers|multi-driven|driven by more than one|conflicting drivers)"),
     "Remove conflicting assignments so each signal has one valid driver."),
    ("LATCH_INFERRED", re.compile(r"(?i)(?:latch inferred|inferred latch|unwanted latch)"),
     "Assign the affected combinational signal on every path without adding a latch."),
    ("UNSUPPORTED_SYNTHESIS", re.compile(r"(?i)(?:unsupported|not supported|cannot synthesize|non-synthesizable)"),
     "Replace the unsupported construct with equivalent synthesizable RTL."),
    ("SYNTAX_ERROR", re.compile(r"(?i)(?:syntax error|unexpected token|parse error|near .*? expecting)"),
     "Correct the reported SystemVerilog syntax while preserving the required interface and behavior."),
)


def normalized_candidate_hash(source: str) -> str:
    """Ignore comments and formatting, but preserve identifiers and literals."""
    clean = _COMMENT.sub(" ", source)
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\d+\s*\'\s*[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|[A-Za-z_$][\w$]*|[^\s]', clean)
    return hashlib.sha256("\x1f".join(tokens).encode("utf-8")).hexdigest()


def _source_location(source: str, line_number: int | None = None,
                     symbol: str | None = None) -> dict:
    lines = source.splitlines()
    if line_number is not None and 1 <= line_number <= len(lines):
        return {"line": line_number, "snippet": lines[line_number - 1].strip()[:240],
                "basis": "tool_line_number"}
    if symbol and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", symbol):
        for i, line in enumerate(lines, 1):
            if re.search(rf"\b{re.escape(symbol)}\b", line):
                return {"line": i, "snippet": line.strip()[:240], "basis": "symbol_match"}
    return {"line": None, "snippet": "", "basis": "unresolved"}


def _repair_text(diagnostic: dict, max_chars: int) -> str:
    lines = [
        f"Candidate-only {diagnostic['stage']} failure: {diagnostic['category']}.",
        f"Repair Goal: {diagnostic['repair_goal']}",
    ]
    for item in diagnostic["findings"][:4]:
        loc = item["location"]
        where = f"line {loc['line']}" if loc["line"] else "location unresolved"
        lines.append(f"- {item['code']} at {where}: {item['message']}")
        if loc["snippet"]:
            lines.append(f"  Candidate: {loc['snippet']}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def diagnose_interface(report: dict, candidate: str, max_chars: int) -> dict:
    if report.get("status") != "FAIL" or report.get("scope") != "public_structure_only":
        raise ValueError("Expected a failed public structural interface report")
    findings = []
    for item in report["issues"]:
        code = item["code"]
        symbol = item.get("port") or item.get("submodule") or item.get("expected")
        location = _source_location(candidate, symbol=symbol if isinstance(symbol, str) else None)
        if location["line"] is None:
            location = _source_location(candidate, symbol="module")
        message = ", ".join(f"{key}={value}" for key, value in item.items() if key != "code")
        findings.append({"code": code, "message": message, "location": location,
                         "evidence": "public_interface_contract"})
    goals = list(dict.fromkeys(INTERFACE_GOALS.get(item["code"], "Correct the public interface mismatch.")
                               for item in report["issues"]))
    result = {"schema_version": 1, "source": "public_spec_and_candidate_only",
              "stage": "interface", "category": "INTERFACE_MISMATCH",
              "findings": findings, "repair_goal": " ".join(goals),
              "raw_log_lines_seen": 0, "log_lines_forwarded": 0}
    result["repair_feedback"] = _repair_text(result, max_chars)
    result["signature"] = _signature(result)
    return result


def _extract_line(text: str) -> int | None:
    for pattern in _LOCATION:
        match = pattern.search(text)
        if match:
            value = int(match.group(1))
            if value > 0:
                return value
    return None


def _signature(diagnostic: dict) -> str:
    pieces = [diagnostic["stage"], diagnostic["category"]]
    for finding in diagnostic["findings"][:4]:
        pieces.extend((finding["code"], str(finding["location"]["line"]),
                       finding["message"][:120]))
    return hashlib.sha256("\x1f".join(pieces).encode("utf-8")).hexdigest()


def diagnose_eda(eda: dict, candidate: str, max_chars: int) -> dict:
    """Read only logs from this candidate EDA run; never an evaluator manifest."""
    if eda.get("status") != "FAIL" or eda.get("failed_stage") not in ("analyze", "synthesize"):
        raise ValueError("Expected candidate-only analyze/synthesize FAIL")
    if eda.get("input_scope") != "candidate_only" or eda.get("hidden_evaluator_access") is not False:
        raise ValueError("EDA result is not candidate-only")
    work = Path(eda["work_dir"]).resolve()
    stage = eda["failed_stage"]
    names = ("analyze.stdout.log", "xvlog.log") if stage == "analyze" else (
        "synthesize.stdout.log", "vivado.log")
    raw_lines = []
    for name in names:
        path = work / name
        if path.is_file():
            raw_lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    selected = []
    for line in raw_lines:
        if line.lstrip().startswith("#") or not _DIAGNOSTIC.search(line):
            continue
        clean = re.sub(r"[\t ]+", " ", line).strip()
        clean = clean.replace(str(work), "<candidate-work>")
        if clean and clean not in selected:
            selected.append(clean[:400])
        if len(selected) >= 8:
            break
    first = selected[0] if selected else str(eda.get("reason", "No diagnostic"))[:400]
    category, goal = ("COMPILE_ERROR", "Correct the candidate compile error at the reported location.") \
        if stage == "analyze" else ("SYNTHESIS_ERROR", "Correct the candidate synthesis error at the reported location.")
    for code, pattern, repair_goal in EDA_RULES:
        if pattern.search("\n".join(selected)):
            category, goal = code, repair_goal
            break
    location = _source_location(candidate, line_number=_extract_line(first))
    if location["line"] is None:
        for line in selected:
            line_number = _extract_line(line)
            if line_number:
                location = _source_location(candidate, line_number=line_number)
                break
    result = {"schema_version": 1, "source": "candidate_eda_only",
              "stage": stage, "category": category,
              "findings": [{"code": category, "message": first,
                            "location": location, "evidence": "candidate_eda_log"}],
              "repair_goal": goal, "raw_log_lines_seen": len(raw_lines),
              "log_lines_forwarded": min(len(selected), 8),
              "selected_log_lines": selected}
    result["repair_feedback"] = _repair_text(result, max_chars)
    result["signature"] = _signature(result)
    return result
