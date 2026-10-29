"""Conservative structural RTL contract extracted only from a public prompt.

This is deliberately not a functional oracle. Unsupported or ambiguous syntax
is recorded and skipped rather than turned into a repair instruction.
"""
from __future__ import annotations

import hashlib
import re


IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_MODULE = re.compile(rf"\bmodule\s+(?:(?:automatic|static)\s+)?({IDENT})\b")
_PORT = re.compile(rf"^\s*(input|output|inout)\s+(.+?)\s*$", re.I | re.S)
_RANGE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")
_WORDS = re.compile(r"\b(?:wire|reg|logic|var|tri|signed|unsigned)\b", re.I)


def _clean_rtl(source: str) -> str:
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                  lambda m: re.sub(r"[^\n]", " ", m.group()), source,
                  flags=re.S)


def _split_commas(value: str) -> list[str]:
    parts, start = [], 0
    square = round_depth = brace = 0
    for i, char in enumerate(value):
        if char == "[": square += 1
        elif char == "]": square -= 1
        elif char == "(": round_depth += 1
        elif char == ")": round_depth -= 1
        elif char == "{": brace += 1
        elif char == "}": brace -= 1
        elif char == "," and square == round_depth == brace == 0:
            parts.append(value[start:i].strip())
            start = i + 1
    parts.append(value[start:].strip())
    return parts


def _balanced_end(value: str, start: int) -> int | None:
    if start >= len(value) or value[start] != "(":
        return None
    depth = 0
    for i in range(start, len(value)):
        if value[i] == "(": depth += 1
        elif value[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _width(text: str) -> tuple[int | None, bool]:
    ranges = re.findall(r"\[[^\]]+\]", text)
    if not ranges:
        return 1, True
    if len(ranges) != 1:
        return None, False
    match = _RANGE.fullmatch(ranges[0])
    if not match:
        return None, False
    return abs(int(match.group(1)) - int(match.group(2))) + 1, True


def _declaration(text: str, inherited: dict | None = None) -> dict | None:
    """Parse one simple ANSI/declaration segment; return None when uncertain."""
    match = _PORT.match(text)
    if match:
        direction, rest = match.group(1).lower(), match.group(2).strip()
    elif inherited:
        direction, rest = inherited["direction"], text.strip()
    else:
        return None
    if "=" in rest or "interface" in rest.lower().split():
        return None
    names = re.findall(rf"\b{IDENT}\b", re.sub(r"\[[^\]]+\]", " ", rest))
    names = [name for name in names if name.lower() not in
             {"wire", "reg", "logic", "var", "tri", "signed", "unsigned"}]
    if len(names) != 1 or not re.search(rf"\b{re.escape(names[0])}\s*$", rest):
        return None
    name = names[0]
    prefix = rest[:rest.rfind(name)].strip()
    signed_flag = bool(re.search(r"\bsigned\b", prefix))
    if re.search(r"\bunsigned\b", prefix):
        signed_flag = False
    range_match = re.search(r"\[[^\]]+\]", prefix)
    if range_match:
        width, known = _width(range_match.group())
    elif match:
        width, known = 1, True
    else:
        width = inherited["width"]
        known = width is not None
        signed_flag = inherited["signed"]
    leftover = _WORDS.sub(" ", re.sub(r"\[[^\]]+\]", " ", prefix)).strip()
    if leftover:
        return None
    return {"name": name, "direction": direction,
            "width": width if known else None, "signed": signed_flag}


def extract_public_contract(spec: str) -> dict:
    """Extract only explicitly stated structural requirements from public text."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("Public specification must be non-empty")
    names = re.findall(rf"\bmodule\s+(?:named\s+)?({IDENT})\b", spec, re.I)
    unique_names = list(dict.fromkeys(names))
    main = re.search(
        rf"\b(?:implement|create|design|write)\s+(?:a\s+|an\s+|the\s+)?"
        rf"(?:synthesizable\s+)?(?:SystemVerilog\s+|Verilog\s+)?"
        rf"module\s+(?:named\s+)?({IDENT})\b", spec, re.I)
    top = main.group(1) if main else (unique_names[0] if len(unique_names) == 1 else None)
    warnings = []
    if len(unique_names) > 1 and not main:
        warnings.append("Multiple module names appear in prose; top name is ambiguous")
    ports: list[dict] = []
    ports_complete = False
    source_format = "none"
    block = re.search(r"\bPorts\s*:\s*([^.]*)\.", spec, re.I | re.S)
    if block:
        source_format = "ports_clause"
        parts = _split_commas(block.group(1))
        parsed = [_declaration(part) for part in parts]
        if parts and all(parsed):
            ports = parsed  # type: ignore[assignment]
            ports_complete = True
        else:
            warnings.append("Ports clause contains unsupported syntax; port checks skipped")
    else:
        bullet_lines = re.findall(r"(?m)^\s*-\s*(?:input|output|inout)\b[^\n]*", spec)
        if bullet_lines:
            source_format = "port_bullets"
            one_bit_default = bool(re.search(
                r"All input and output ports are one bit unless otherwise specified", spec, re.I))
            parsed = []
            for line in bullet_lines:
                match = re.fullmatch(
                    rf"\s*-\s*(input|output|inout)\s+({IDENT})\s*(?:\(\s*(\d+)\s+bits?\s*\))?\s*",
                    line, re.I)
                if not match:
                    parsed = []
                    warnings.append("Port bullet contains unsupported syntax; port checks skipped")
                    break
                bits = int(match.group(3)) if match.group(3) else (1 if one_bit_default else None)
                parsed.append({"name": match.group(2), "direction": match.group(1).lower(),
                               "width": bits, "signed": None})
            if parsed:
                ports = parsed
                ports_complete = True
    if source_format == "none":
        warnings.append("No parseable public port list; port checks skipped")
    if len({port["name"] for port in ports}) != len(ports):
        warnings.append("Duplicate public port names; port checks skipped")
        ports, ports_complete = [], False
    for port in ports:
        if port["width"] is None:
            warnings.append(f"Public width of port {port['name']} is unresolved; width check skipped")
    required_submodules = []
    submodule_patterns = (
        (rf"\bmust\s+(?:also\s+)?(?:define|include|implement|provide)\s+"
         rf"(?:a\s+)?(?:submodule|module)\s+(?:named\s+)?({IDENT})\b", "definition"),
        (rf"\brequired\s+submodule\s+(?:named\s+)?({IDENT})\b", "definition"),
        (rf"\bmust\s+instantiate\s+(?:a\s+)?(?:submodule|module)\s+"
         rf"(?:named\s+)?({IDENT})\b", "instance"),
    )
    for pattern, kind in submodule_patterns:
        for match in re.finditer(pattern, spec, re.I):
            item = {"name": match.group(1), "kind": kind, "evidence": match.group(0)}
            if item not in required_submodules:
                required_submodules.append(item)
    return {"schema_version": 1, "source": "public_spec_only",
            "spec_sha256": hashlib.sha256(spec.encode("utf-8")).hexdigest(),
            "top_module": top, "ports": ports, "ports_complete": ports_complete,
            "port_source_format": source_format,
            "required_submodules": required_submodules, "warnings": warnings}


def _candidate_modules(source: str) -> tuple[list[str], dict[str, dict]]:
    clean = _clean_rtl(source)
    names: list[str] = []
    parsed: dict[str, dict] = {}
    for match in _MODULE.finditer(clean):
        name = match.group(1)
        names.append(name)
        start = match.end()
        endmodule = re.search(r"\bendmodule\b", clean[start:])
        if not endmodule:
            continue
        chunk = clean[start:start + endmodule.start()]
        cursor = 0
        while cursor < len(chunk) and chunk[cursor].isspace(): cursor += 1
        if cursor < len(chunk) and chunk[cursor] == "#":
            cursor += 1
            while cursor < len(chunk) and chunk[cursor].isspace(): cursor += 1
            end = _balanced_end(chunk, cursor)
            if end is None: continue
            cursor = end + 1
            while cursor < len(chunk) and chunk[cursor].isspace(): cursor += 1
        if cursor >= len(chunk) or chunk[cursor] != "(":
            continue
        header_end = _balanced_end(chunk, cursor)
        if header_end is None or ";" not in chunk[header_end + 1:]:
            continue
        header = chunk[cursor + 1:header_end]
        body = chunk[chunk.find(";", header_end) + 1:]
        segments = _split_commas(header)
        ports, inherited, complete = {}, None, True
        ansi = any(_PORT.match(segment) for segment in segments)
        if ansi:
            for segment in segments:
                port = _declaration(segment, inherited)
                if port is None or port["name"] in ports:
                    complete = False
                    break
                ports[port["name"]] = port
                inherited = port
        else:
            header_names = [segment.strip() for segment in segments if segment.strip()]
            if not all(re.fullmatch(IDENT, item) for item in header_names):
                complete = False
            declarations = {}
            for decl in re.finditer(r"\b(input|output|inout)\b([^;]*);", body):
                parts = _split_commas(decl.group(0)[:-1])
                inherited = None
                for part in parts:
                    port = _declaration(part, inherited)
                    if port:
                        declarations[port["name"]] = port
                        inherited = port
            if complete and set(header_names) == set(declarations):
                ports = {item: declarations[item] for item in header_names}
            else:
                complete = False
        parsed[name] = {"ports": ports, "ports_complete": complete, "body": body}
    return names, parsed


def check_interface_contract(contract: dict, candidate: str) -> dict:
    """Return machine-readable diagnostics; abstain on unsupported syntax."""
    if contract.get("source") != "public_spec_only":
        raise ValueError("Interface contract must come from a public specification")
    names, parsed = _candidate_modules(candidate)
    issues = []
    skipped = list(contract.get("warnings", []))

    def issue(code: str, **fields) -> None:
        issues.append({"code": code, **fields})

    top = contract.get("top_module")
    if top and top not in names:
        issue("TOP_MODULE_MISSING", expected=top, observed=names)
    elif top and names.count(top) > 1:
        issue("TOP_MODULE_DUPLICATE", expected=top, observed_count=names.count(top))
    if top and top in parsed and names.count(top) == 1:
        observed = parsed[top]
        if contract.get("ports_complete") and observed["ports_complete"]:
            expected_names = {port["name"] for port in contract["ports"]}
            observed_names = set(observed["ports"])
            for name in sorted(expected_names - observed_names):
                issue("PORT_MISSING", port=name)
            for name in sorted(observed_names - expected_names):
                issue("PORT_UNEXPECTED", port=name)
            for expected in contract["ports"]:
                actual = observed["ports"].get(expected["name"])
                if actual is None:
                    continue
                if actual["direction"] != expected["direction"]:
                    issue("PORT_DIRECTION_MISMATCH", port=expected["name"],
                          expected=expected["direction"], observed=actual["direction"])
                if expected["width"] is not None and actual["width"] is not None and actual["width"] != expected["width"]:
                    issue("PORT_WIDTH_MISMATCH", port=expected["name"],
                          expected=expected["width"], observed=actual["width"])
                elif expected["width"] is not None and actual["width"] is None:
                    skipped.append(f"Could not resolve width of candidate port {expected['name']}")
                if expected["signed"] is not None and actual["signed"] != expected["signed"]:
                    issue("PORT_SIGNED_MISMATCH", port=expected["name"],
                          expected=expected["signed"], observed=actual["signed"])
        elif contract.get("ports_complete"):
            skipped.append("Candidate top port declaration is unsupported; port checks skipped")
    elif top and top in names and contract.get("ports_complete"):
        skipped.append("Candidate top header could not be parsed; port checks skipped")
    elif not top:
        skipped.append("Public top-module name is not unambiguous")
    for required in contract.get("required_submodules", []):
        name = required["name"]
        if required["kind"] == "definition" and name not in names:
            issue("REQUIRED_SUBMODULE_MISSING", submodule=name, requirement="definition")
        elif required["kind"] == "instance" and top in parsed:
            body = parsed[top]["body"]
            if not re.search(rf"\b{re.escape(name)}\s*(?:#\s*\([^;]*?\)\s*)?{IDENT}\s*\(", body, re.S):
                issue("REQUIRED_SUBMODULE_MISSING", submodule=name, requirement="instance")
        elif required["kind"] == "instance":
            skipped.append(f"Cannot inspect {name} instantiation without parsed top body")
    status = "FAIL" if issues else ("INDETERMINATE" if skipped else "PASS")
    return {"schema_version": 1, "status": status, "issues": issues,
            "skipped_checks": skipped, "candidate_modules": names,
            "contract_spec_sha256": contract["spec_sha256"],
            "scope": "public_structure_only"}


def interface_feedback(report: dict) -> str:
    """Bounded, deterministic repair input without any evaluator material."""
    if report["status"] != "FAIL":
        return ""
    lines = ["Public interface contract check failed. Correct only these structural mismatches:"]
    for item in report["issues"]:
        fields = ", ".join(f"{key}={value}" for key, value in item.items() if key != "code")
        lines.append(f"- {item['code']}: {fields}")
    lines.append("Preserve the requested RTL behavior and return a complete candidate.")
    return "\n".join(lines)
