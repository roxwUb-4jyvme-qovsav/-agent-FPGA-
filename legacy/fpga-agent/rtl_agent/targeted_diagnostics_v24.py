"""Structured candidate-only repair diagnostics for staged v2.4 EDA."""
from __future__ import annotations

import hashlib
import ast
import json
from pathlib import Path
import re

from .targeted_diagnostics import EDA_RULES, _extract_line, _source_location

LOG_NAMES = {
    "analyze": ("analyze.stdout.log", "xvlog.log"),
    "elaborate": ("elaborate.stdout.log", "xelab.log"),
    "synthesize": ("synthesize.stdout.log", "vivado.log"),
}
DEFAULT_GOAL = {
    "analyze": "Correct the candidate's SystemVerilog compile error at the reported location.",
    "elaborate": "Resolve the candidate's module hierarchy, parameter or port elaboration error.",
    "synthesize": "Correct the candidate's synthesis error while preserving the public specification.",
}
DIAGNOSTIC = re.compile(r"(?i)\b(?:ERROR|FATAL|CRITICAL WARNING)\s*:|\bRTL_AGENT_")
_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_COMMENT_STRING = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"', re.S)
_ORDINARY_RANGE = re.compile(
    r"(?<![A-Za-z0-9_$.\\])(?P<object>" + _IDENT + r")\s*\[(?P<bounds>[^\[\]\n]+)\]")
_NONCONSTANT = re.compile(r"'(" + _IDENT + r")'\s+is\s+not\s+a\s+constant\b", re.I)
_SOURCE_SITE = re.compile(r"\[([^\[\]\r\n]*candidate\.sv):([1-9][0-9]*)(?::[1-9][0-9]*)?\]\s*$", re.I)


def _bound_compile_log(work: Path, candidate: str) -> tuple[str, dict] | None:
    """Use the existing compiler receipt, not a bare error number or stale log.

    This checks application-level provenance, not authenticity against malicious
    writers. Missing receipts simply keep the pre-existing generic diagnosis.
    """
    try:
        paths = {name: work / name for name in (
            "candidate.sv", "compile_result.json", "analyze.diagnostic.log", "analyze.stdout.log")}
        limits = {"candidate.sv": 262144, "compile_result.json": 65536,
                  "analyze.diagnostic.log": 4194304, "analyze.stdout.log": 4194304}
        if work.is_symlink() or any(p.is_symlink() or not p.is_file() or
                p.stat().st_size > limits[name] for name, p in paths.items()):
            return None
        rtl = paths["candidate.sv"].read_bytes()
        if rtl != candidate.encode("utf-8"):
            return None
        receipt_bytes = paths["compile_result.json"].read_bytes()
        receipt = json.loads(receipt_bytes)
        if (not isinstance(receipt, dict) or receipt.get("input_scope") != "candidate_only" or
                receipt.get("status") != "FAIL" or receipt.get("failed_stage") != "analyze" or
                receipt.get("candidate_sha256") != hashlib.sha256(rtl).hexdigest()):
            return None
        record = receipt.get("stages", {}).get("analyze", {})
        if (not isinstance(record, dict) or record.get("status") != "FAIL" or
                type(record.get("returncode")) is not int or record["returncode"] == 0 or
                record.get("timed_out") is not False or record.get("diagnostic_saved") is not True or
                record.get("log_artifact") != "analyze.diagnostic.log"):
            return None
        diagnostic = paths["analyze.diagnostic.log"].read_bytes()
        if hashlib.sha256(diagnostic).hexdigest() != record.get("log_sha256"):
            return None
        # _tool_result writes exactly this UTF-8 merge. Reject stale stdout even
        # when someone retained a different, internally consistent receipt.
        stdout = paths["analyze.stdout.log"].read_text(encoding="utf-8")
        merged = stdout
        vendor = work / "analyze.log"
        if vendor.exists():
            if vendor.is_symlink() or not vendor.is_file() or vendor.stat().st_size > 4194304:
                return None
            merged += "\n" + vendor.read_text(encoding="utf-8")
        if diagnostic != merged.encode("utf-8"):
            return None
        return diagnostic.decode("utf-8"), {
            "candidate_sha256": receipt["candidate_sha256"],
            "compile_result_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "diagnostic_sha256": record["log_sha256"],
            "basis": "CURRENT_CANDIDATE_AND_COMPILER_RECEIPT_AND_DIAGNOSTIC_DIGEST"}
    except (OSError, ValueError, AttributeError, TypeError, UnicodeError):
        return None


def _dynamic_range_diagnosis(raw: list[str], candidate: str, work: Path) -> dict | None:
    """Recognize only unambiguous, same-line ordinary ranges in bound errors.

    Not a SystemVerilog parser or rewrite: macros, nested/ternary syntax,
    declarations and uncertain/multiple objects deliberately remain generic.
    """
    if not candidate or len(candidate) > 262144:
        return None
    bound = _bound_compile_log(work, candidate)
    if bound is None:
        return None
    transcript, binding = bound
    entries = [line for line in raw if re.search(r"\bVRFC\s+10-2951\b", line, re.I)]
    if not entries:
        return None
    def secondary_module_notice(line: str) -> bool:
        match = re.match(r"\s*ERROR:\s*\[VRFC 10-8530\]\s+module\s+'(" + _IDENT +
                         r")'\s+is ignored due to previous errors\s+", line, re.I)
        loc = _SOURCE_SITE.search(line)
        if not match or not loc or line not in transcript.splitlines():
            return False
        path = Path(loc.group(1))
        if not path.is_absolute():
            if loc.group(1) != "candidate.sv":
                return False
            path = work / path
        try:
            same_file = path.resolve(strict=True) == (work / "candidate.sv").resolve(strict=True)
        except OSError:
            return False
        source_lines = candidate.splitlines()
        number = int(loc.group(2))
        return same_file and number <= len(source_lines) and bool(re.search(
            r"\bmodule\s+" + re.escape(match.group(1)) + r"(?![A-Za-z0-9_$])", source_lines[number-1]))
    if any(re.search(r"\bERROR\s*:", line, re.I) and not re.search(
            r"\bVRFC\s+10-(?:2951|1775)\b", line, re.I) and not secondary_module_notice(line) for line in raw):
        return None
    masked = _COMMENT_STRING.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), candidate)
    # Preprocessing and source remapping are not interpreted by this recognizer.
    if "`" in masked:
        return None
    lines = masked.splitlines()
    sites = []
    for entry in entries:
        found, location = _NONCONSTANT.search(entry), _SOURCE_SITE.search(entry)
        if (not found or not location or entry not in transcript.splitlines() or
                not re.match(r"\s*ERROR\s*:", entry, re.I)):
            return None
        symbol = found.group(1)
        path = Path(location.group(1))
        if not path.is_absolute():
            if location.group(1) != "candidate.sv":
                return None
            path = work / path
        try:
            if path.resolve(strict=True) != (work / "candidate.sv").resolve(strict=True):
                return None
        except OSError:
            return None
        number = int(location.group(2))
        if number > len(lines):
            return None
        cited = lines[number - 1]
        token = r"(?<![A-Za-z0-9_$])" + re.escape(symbol) + r"(?![A-Za-z0-9_$])"
        # A parameter identifier may be constant. Do not infer otherwise from a
        # diagnostic alone; ambiguous declarations/scopes abstain conservatively.
        if any(re.search(token, m.group()) for m in re.finditer(
                r"\b(?:parameter|localparam)\b[^;]*;", masked, re.S)):
            return None
        matched = []
        for m in _ORDINARY_RANGE.finditer(cited):
            bounds = m.group("bounds")
            if (bounds.count(":") != 1 or re.search(r"[+-]\s*:", bounds) or
                    "?" in bounds or not re.search(token, bounds)):
                continue
            prefix = re.split(r";|\bbegin\b|\bend\b", cited[:m.start()])[-1]
            if (re.search(r"\b(?:module|input|output|inout|wire|reg|logic|var|integer|int|"
                          r"typedef|parameter|localparam)\b", prefix) or
                    not re.search(r"(?:<=|(?<![=!<>])=(?!=))", prefix)):
                continue
            upper, lower = (part.strip() for part in bounds.split(":"))
            if not upper or not lower or len(m.group()) > 180:
                continue
            matched.append({"line": number, "object": m.group("object"),
                            "expression": m.group(), "nonconstant_bound": symbol,
                            "upper_bound": upper, "lower_bound": lower})
        if len(matched) != 1:
            # A plain concatenation of the same object's reported dynamic ranges
            # is unambiguous. Other multiple expressions stay conservative.
            joined = r"\s*,\s*".join(re.escape(x["expression"]) for x in matched)
            if (not 2 <= len(matched) <= 4 or len({x["object"] for x in matched}) != 1 or
                    not re.fullmatch(r"\s*(?:assign\s+)?" + _IDENT +
                                     r"\s*(?:<=|=)\s*\{\s*" + joined + r"\s*\}\s*;\s*", cited)):
                return None
        sites.extend(matched)
    unique = list({(x["line"], x["expression"]): x for x in sites}.values())
    if (not 1 <= len(unique) <= 4 or len({(x['line'], x['object'], x['nonconstant_bound']) for x in unique}) != 1):
        return None
    site = unique[0]
    expressions = ', '.join(x['expression'] for x in unique)
    bounds = (f"Upper bound: {site['upper_bound']}; lower bound: {site['lower_bound']}. "
              if len(unique) == 1 else ' '.join(
                  f"Range {x['expression']}: upper bound {x['upper_bound']}; lower bound {x['lower_bound']}."
                  for x in unique) + ' ')
    goal = (
        f"Ordinary part-select {expressions} of '{site['object']}' at line {site['line']} "
        f"uses compiler-reported nonconstant '{site['nonconstant_bound']}'. "
        + bounds +
        "Ordinary [upper:lower] bounds must be elaboration-time constants; even a constant "
        "mathematical width does not make dynamic bounds legal. Review the selected width "
        "and its relation to both bounds against the full public specification. Choose a "
        "repair that preserves expression sizing: concatenation adds operand widths, "
        "and assignment may discard high-order bits. Replacing a selected field by a "
        "full-width expression inside an unchanged concatenation is not generally "
        "equivalent. Explicitly check operand widths, the composed result width, and "
        "any assignment truncation before choosing a replacement. Distinguish each "
        "operation's boundary rule from the public specification; do not substitute "
        "saturation or identity behavior for a specified modular count, or vice versa. "
        "Check zero counts, counts equal to the data width and larger legal counts "
        "against that public rule without using any external testbench or answer. Choose a "
        "legal structure only when supported by that specification; an indexed part-select "
        "may have a variable base but requires a constant width. Preserve public interface "
        "names, widths, signedness, behavior and boundary cases, including zero/large "
        "indices and reset/timing semantics where applicable. Do not infer shift, rotate "
        "or extraction behavior from this error alone. Return complete corrected RTL, "
        "with concise comments explaining the range/width change, without a TB or external prose.")
    return {"category": "VARIABLE_PART_SELECT_RANGE", "repair_goal": goal,
            "related_locations": {"ranges": unique}, "evidence_binding": binding}


def _public_rotation_width_review(spec: str, candidate: str, dynamic: dict) -> dict | None:
    """Bind a rewrite-width risk to explicit public prose and a simple RTL case.

    This is deliberately not a general SV evaluator or functional oracle. The
    existing bound compiler receipt proves the illegal ranges, not a functional
    result. We only explain the sizing consequence of a possible replacement.
    Unsupported, conflicting or parameterized syntax abstains without a hint.
    """
    if (not isinstance(spec, str) or not 0 < len(spec) <= 65536 or
            re.search(r"(?i)\b(?:example|illustrative|not|instead|obsolete|ignore|revis\w*|except|unless)\b", spec)):
        return None
    rules = list(re.finditer(r"(?i)\bRotate\s+left\s+uses\s+(" + _IDENT +
                            r")\s+modulo\s+([0-9]+)\s*\.", spec))
    modes = list(re.finditer(r"(?i)\b(" + _IDENT + r")\s*=\s*([0-9]+)\s+rotate\s+left\b", spec))
    if len(rules) != 1 or len(modes) != 1:
        return None
    count, width = rules[0].group(1), int(rules[0].group(2))
    selector, mode_value = modes[0].group(1), int(modes[0].group(2))
    if not 2 <= width <= 256:
        return None
    masked = _COMMENT_STRING.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), candidate)
    if "`" in masked or len(re.findall(r"\bmodule\b", masked)) != 1:
        return None
    def ports(text):
        headers = re.findall(r"\bmodule\s+" + _IDENT + r"\s*\(([^()]*)\)\s*;", text, re.S)
        if len(headers) != 1:
            return None
        result = {}
        for item in headers[0].split(','):
            m = re.fullmatch(r"\s*(input|output)\s+(?:(?:wire|logic|reg)\s+)?"
                             r"(?:(signed|unsigned)\s+)?(?:\[\s*([0-9]+)\s*:\s*([0-9]+)\s*\]\s*)?(" +
                             _IDENT + r")\s*", item)
            if not m or m.group(5) in result:
                return None
            result[m.group(5)] = (m.group(1), abs(int(m.group(3))-int(m.group(4)))+1
                                  if m.group(3) else 1, m.group(2) == 'signed')
        return result
    public, actual = ports(spec), ports(masked)
    sites = dynamic.get('related_locations', {}).get('ranges', [])
    if not public or actual != public or len(sites) != 2:
        return None
    obj = sites[0]['object']
    if (any(x['object'] != obj or x['nonconstant_bound'] != count for x in sites) or
            public.get(obj) != ('input', width, False) or
            public.get(count, ('',))[0] != 'input' or
            public.get(selector, ('',))[0] != 'input'):
        return None
    def affine(text):
        node = ast.parse(text, mode='eval').body
        def walk(n):
            if isinstance(n, ast.Constant) and type(n.value) is int:
                return (0, n.value)
            if isinstance(n, ast.Name) and n.id == count:
                return (1, 0)
            if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)):
                a,b = walk(n.left),walk(n.right);sign = 1 if isinstance(n.op, ast.Add) else -1
                return (a[0]+sign*b[0], a[1]+sign*b[1])
            raise ValueError('unsupported affine bound')
        return walk(node)
    try:
        bounds = [(affine(s['upper_bound']), affine(s['lower_bound'])) for s in sites]
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return None
    if (sum(a[0]-b[0] for a,b in bounds), sum(a[1]-b[1]+1 for a,b in bounds)) != (0,width):
        return None
    line_number = sites[0]['line']
    lines = masked.splitlines(keepends=True)
    line = lines[line_number-1]
    assignment = re.fullmatch(r"\s*(?:assign\s+)?(" + _IDENT + r")\s*(?:<=|=)\s*\{[^{}]+\}\s*;\s*", line)
    if not assignment:
        return None
    receiver = assignment.group(1)
    destination_width = public.get(receiver, (None,None))[1]
    if destination_width is None:
        declarations = re.findall(r"\b(?:logic|wire|reg)\s+(?:signed\s+)?\[\s*([0-9]+)\s*:\s*([0-9]+)\s*\]\s+" +
                                  re.escape(receiver) + r"\s*;", masked)
        if len(declarations) != 1:
            return None
        destination_width = abs(int(declarations[0][0])-int(declarations[0][1]))+1
        aliases = re.findall(r"\bassign\s+(" + _IDENT + r")\s*=\s*" + re.escape(receiver) + r"\s*;", masked)
        if len(aliases) != 1 or public.get(aliases[0]) != ('output',width,False):
            return None
    if destination_width != width:
        return None
    offset = sum(map(len,lines[:line_number-1]))
    cases = list(re.finditer(r"\bcase\s*\(\s*" + re.escape(selector) + r"\s*\)(.*?)\bendcase\b", masked, re.S))
    if len(cases) != 1 or not cases[0].start(1) <= offset < cases[0].end(1):
        return None
    body = cases[0].group(1);relative = offset-cases[0].start(1)
    labels = list(re.finditer(r"(?m)^\s*([0-9]+\s*'[bBoOdDhH][0-9a-fA-F]+|[0-9]+|default)\s*:", body))
    preceding = [m for m in labels if m.start() <= relative]
    if not preceding:
        return None
    label = preceding[-1].group(1).replace(' ','')
    try:
        value = int(label.split("'")[1][1:], {'b':2,'o':8,'d':10,'h':16}[label.split("'")[1][0].lower()]) if "'" in label else int(label)
    except (ValueError,KeyError):
        return None
    if value != mode_value:
        return None
    feedback = (
        f"Public semantic width review (repair risk, not a functional verdict): '{rules[0].group()}' "
        f"The reported fields of '{obj}' have symbolic total width {width}, matching receiver "
        f"'{receiver}' ({width} bits). This does not prove valid indices or correct rotation. "
        f"Replacing BOTH fields with full-width shifts and concatenating them instead creates "
        f"{2*width} bits; assignment to {width} bits discards the entire high operand. "
        "Do not mistake that narrowing concatenation for a same-width combination of shifted "
        "pieces. Choose a legal replacement whose expression width and bit combination actually "
        "preserve the explicit cyclic public operation, including its zero-count identity. "
        "Keep unrelated operations and signedness unchanged; no automatic RTL rewrite is authorized.")
    return {'status':'REPAIR_SEMANTIC_RISK','basis':'EXPLICIT_PUBLIC_SPEC_AND_CURRENT_RTL_AND_BOUND_COMPILER_RECEIPT',
            'public_spec_sha256':hashlib.sha256(spec.encode('utf-8')).hexdigest(),
            'candidate_sha256':dynamic['evidence_binding']['candidate_sha256'],
            'requirement_excerpt':rules[0].group(),'operand':obj,'receiver':receiver,'width':width,
            'selected_symbolic_width':width,'replacement_concat_width':2*width,'line':line_number,
            'feedback':feedback,'functional_pass_proven':False}


def diagnose_stage(stage: str, work: Path, candidate: str, max_chars: int = 4000,
                   *, public_spec: str | None = None) -> dict:
    if stage not in LOG_NAMES:
        raise ValueError("Only candidate-only analyze, elaborate and synthesis stages are supported")
    lines = []
    for name in LOG_NAMES[stage]:
        path = work / name
        if path.is_file():
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    selected = []
    raw_selected = []
    for line in lines:
        if line.lstrip().startswith("#") or not DIAGNOSTIC.search(line):
            continue
        clean = re.sub(r"\s+", " ", line.strip()).replace(str(work), "<candidate-work>")[:400]
        if clean not in selected:
            selected.append(clean)
            raw_selected.append(line)
        if len(selected) == 8:
            break
    first = selected[0] if selected else "Tool reported failure without a parseable diagnostic"
    category, goal = stage.upper() + "_ERROR", DEFAULT_GOAL[stage]
    for code, pattern, specific_goal in EDA_RULES:
        if pattern.search("\n".join(selected)):
            category, goal = code, specific_goal
            break
    # The tool identifies a procedural assignment to a non-variable object.
    # Keep the actual diagnostics and full candidate as evidence; do not infer a
    # universal declaration rewrite from an error number alone.
    if stage == "analyze" and any(
            re.search(r"\bVRFC\s+10-1280\b", line, re.I) and
            re.search(r"\bnon-register\s+[A-Za-z_][A-Za-z0-9_$]*\b", line, re.I)
            for line in selected):
        goal += (
            " Declaration/driver joint localization: Read the complete module; locate the "
            "reported object's declaration and EVERY assignment/driver, not just the error line. "
            "Relate the declared net/variable type to procedural versus continuous drivers; "
            "check output/net conflicts with always_comb or other procedural assignments. "
            "First choose the smallest legal SystemVerilog repair for the actual structure. "
            "For a procedurally driven output, assess an appropriate variable output "
            "(logic with the original signedness and packed width), or an internal variable "
            "plus one continuous output driver if a net must be retained. Do not assume all "
            "VRFC 10-1280 errors have this cause or fix only a sum slice while leaving the "
            "declaration/driver conflict. Preserve interface names, directions, widths, "
            "signedness, public arithmetic/overflow behavior and reset/timing semantics. "
            "Recheck ALL drivers for conflicts, sign extension and truncation. Return the "
            "complete corrected RTL; explain declaration/driver changes and reasons using "
            "concise SystemVerilog comments inside the RTL, without external prose or a TB.")
    dynamic = _dynamic_range_diagnosis(raw_selected, candidate, work) if stage == "analyze" else None
    semantic = _public_rotation_width_review(public_spec, candidate, dynamic) if dynamic and public_spec else None
    if semantic:
        dynamic['repair_goal'] += ' ' + semantic['feedback']
    if dynamic:
        # The unchanged max_chars contract must not silently drop the new rule's
        # mandatory preservation/width conditions. Use the generic path instead.
        mandatory = (f"Candidate-only {stage} failure: {dynamic['category']}.\n"
                     f"Repair Goal: {dynamic['repair_goal']}\n")
        if len(mandatory) <= max_chars:
            category, goal = dynamic["category"], dynamic["repair_goal"]
        else:
            dynamic = None
            semantic = None
    line_number = dynamic["related_locations"]["ranges"][0]["line"] if dynamic else _extract_line(first)
    location = _source_location(candidate, line_number=line_number)
    feedback = (f"Candidate-only {stage} failure: {category}.\nRepair Goal: {goal}\n" +
                "\n".join(selected))[:max_chars]
    signature = hashlib.sha256((stage + "\x1f" + category + "\x1f" + first).encode()).hexdigest()
    result = {"schema_version": 1, "source": "candidate_eda_only", "stage": stage,
            "category": category, "repair_goal": goal,
            "findings": [{"message": first, "location": location}],
            "raw_log_lines_seen": len(lines), "log_lines_forwarded": len(selected),
            "selected_log_lines": selected, "repair_feedback": feedback,
            "signature": signature}
    if dynamic:
        result.update(related_locations=dynamic["related_locations"], evidence_binding=dynamic["evidence_binding"])
    if semantic:
        result['public_semantic_review'] = semantic
    return result


def _risk_digest(record: dict) -> str:
    return hashlib.sha256(json.dumps({k:v for k,v in record.items() if k != 'risk_id'},
                                   sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def make_repair_risk(diagnostic: dict, spec: str, candidate: str) -> dict | None:
    """Freeze one supported, previously reported risk; never invent a risk from RTL alone."""
    try:
        if (diagnostic.get('source') != 'candidate_eda_only' or diagnostic.get('stage') != 'analyze' or
                diagnostic.get('category') != 'VARIABLE_PART_SELECT_RANGE'):
            return None
        review = diagnostic.get('public_semantic_review')
        if not review or review != _public_rotation_width_review(spec, candidate, diagnostic):
            return None
        digest = hashlib.sha256(candidate.encode()).hexdigest()
        binding = diagnostic['evidence_binding']
        if (binding['candidate_sha256'] != digest or review['candidate_sha256'] != digest or
                review['public_spec_sha256'] != hashlib.sha256(spec.encode()).hexdigest() or
                not all(re.fullmatch('[0-9a-f]{64}',binding.get(k,'')) for k in
                        ['candidate_sha256','compile_result_sha256','diagnostic_sha256'])):
            return None
        rule = re.search(r'(?i)\bRotate\s+left\s+uses\s+(' + _IDENT + r')\s+modulo\s+([0-9]+)\s*\.',spec)
        mode = re.search(r'(?i)\b(' + _IDENT + r')\s*=\s*([0-9]+)\s+rotate\s+left\b',spec)
        record = {'schema_version':1,'risk_type':'FULL_WIDTH_SHIFT_CONCAT_NARROWING',
            'status':'OPEN','source':'PREVIOUS_BOUND_COMPILER_DIAGNOSTIC_AND_EXPLICIT_PUBLIC_SPEC',
            'origin_candidate_sha256':digest,'public_spec_sha256':review['public_spec_sha256'],
            'compiler_evidence':dict(binding),'diagnostic_signature':diagnostic['signature'],
            'location':{'line':review['line'],'operand':review['operand'],'receiver':review['receiver']},
            'target_expression':candidate.splitlines()[review['line']-1].strip(),
            'width':review['width'],'operand':review['operand'],'count':rule.group(1),
            'selector':mode.group(1),'mode_value':int(mode.group(2)),
            'public_requirement':review['requirement_excerpt'],
            'expected_change':'Do not retain two same-source full-width shifts concatenated into a narrower public rotation result.',
            'functional_proof':False}
        record['risk_id'] = _risk_digest(record)
        return record
    except (KeyError,TypeError,ValueError,AttributeError,IndexError,RecursionError):
        return None


def check_repair_risk(risk: dict, spec: str, origin: str, candidate: str) -> dict:
    """Narrow structural postcondition, not an SV equivalence or functional checker.

    Only a simple, unconditional public rotation case arm is supported. Complex
    control flow, macros, casts, helper calls and unknown widths abstain. A
    changed structure still requires the original EDA and independent evaluation.
    """
    result = {'schema_version':1,'status':'INDETERMINATE','reason':'Unsupported or incomplete evidence',
              'risk_id':risk.get('risk_id') if isinstance(risk,dict) else None,
              'functional_proof':False,'source':'PUBLIC_SPEC_AND_CURRENT_CANDIDATE_ONLY'}
    try:
        if (not isinstance(risk,dict) or risk.get('risk_type') != 'FULL_WIDTH_SHIFT_CONCAT_NARROWING' or
                risk.get('risk_id') != _risk_digest(risk) or
                hashlib.sha256(spec.encode()).hexdigest() != risk['public_spec_sha256'] or
                hashlib.sha256(origin.encode()).hexdigest() != risk['origin_candidate_sha256'] or
                not 0 < len(candidate) <= 262144):
            return result
        result.update(origin_candidate_sha256=risk['origin_candidate_sha256'],
                      candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                      risk_type=risk['risk_type'])
        masked = _COMMENT_STRING.sub(lambda m: re.sub(r'[^\n]',' ',m.group()),candidate)
        if '`' in masked or len(re.findall(r'\bmodule\b',masked)) != 1:
            return result
        def ports(text):
            heads = re.findall(r'\bmodule\s+'+_IDENT+r'\s*\(([^()]*)\)\s*;',text,re.S)
            if len(heads) != 1:return None
            values = {}
            for part in heads[0].split(','):
                m = re.fullmatch(r'\s*(input|output)\s+(?:(?:wire|logic|reg)\s+)?(?:(signed|unsigned)\s+)?'
                    r'(?:\[\s*([0-9]+)\s*:\s*([0-9]+)\s*\]\s*)?('+_IDENT+r')\s*',part)
                if not m or m[5] in values:return None
                values[m[5]] = (m[1],abs(int(m[3])-int(m[4]))+1 if m[3] else 1,m[2]=='signed')
            return values
        public,current = ports(spec),ports(masked)
        if not public or current != public:return result
        width,obj,count,selector = (risk[k] for k in ['width','operand','count','selector'])
        if public.get(obj) != ('input',width,False):return result
        cases = list(re.finditer(r'\bcase\s*\(\s*'+re.escape(selector)+r'\s*\)(.*?)\bendcase\b',masked,re.S))
        if len(cases) != 1:return result
        body = cases[0][1]
        labels = list(re.finditer(r"(?m)^\s*([0-9]+\s*'[bBoOdDhH][0-9a-fA-F]+|[0-9]+|default)\s*:",body))
        arms = []
        for i,label in enumerate(labels):
            token = label[1].replace(' ','')
            if token == 'default':continue
            value = int(token.split("'")[1][1:],{'b':2,'o':8,'d':10,'h':16}[token.split("'")[1][0].lower()]) if "'" in token else int(token)
            if value == risk['mode_value']:
                arms.append((body[label.end():labels[i+1].start() if i+1<len(labels) else len(body)],
                             cases[0].start(1)+label.end()))
        if len(arms) != 1:return result
        arm,arm_offset = arms[0]
        assignments = list(re.finditer(r'\b('+_IDENT+r')\s*(?:<=|(?<![=!<>])=(?!=))\s*([^;]+);',arm))
        if len(assignments) != 1:return result
        assign = assignments[0];receiver,rhs = assign[1],assign[2].strip()
        remainder = arm[:assign.start()]+arm[assign.end():]
        if re.sub(r'\b(?:begin|end)\b|\s','',remainder):return result
        if receiver in current:
            if current[receiver] != ('output',width,False):return result
        else:
            declarations = re.findall(r'\b(?:logic|wire|reg)\s+\[\s*([0-9]+)\s*:\s*([0-9]+)\s*\]\s+'+re.escape(receiver)+r'\s*;',masked)
            aliases = re.findall(r'\bassign\s+('+_IDENT+r')\s*=\s*'+re.escape(receiver)+r'\s*;',masked)
            if (len(declarations)!=1 or abs(int(declarations[0][0])-int(declarations[0][1]))+1 != width or
                    len(aliases)!=1 or public.get(aliases[0])!=('output',width,False)):
                return result
        def strip_parens(text):
            text=text.strip()
            while text.startswith('(') and text.endswith(')'):
                depth=0;encloses=True
                for i,ch in enumerate(text):
                    depth += (ch=='(')-(ch==')')
                    if depth<0:return text
                    if depth==0 and i<len(text)-1:encloses=False;break
                if depth!=0 or not encloses:break
                text=text[1:-1].strip()
            return text
        def split(text,delimiter):
            stack=[];start=0;items=[];pairs={')':'(',']':'[','}':'{'}
            for i,ch in enumerate(text):
                if ch in '([{':stack.append(ch)
                elif ch in ')]}':
                    if not stack or stack.pop()!=pairs[ch]:return []
                elif ch==delimiter and not stack:items.append(text[start:i]);start=i+1
            return items+[text[start:]] if not stack else []
        def shift(text):
            text=strip_parens(text)
            m=re.fullmatch(r'\s*'+re.escape(obj)+r'(?:\[\s*'+str(width-1)+r'\s*:\s*0\s*\])?\s*(<<|>>)\s*([^<>]+)',text)
            if not m:return None
            tree=ast.parse(m[2],mode='eval')
            allowed=(ast.Expression,ast.BinOp,ast.Add,ast.Sub,ast.Mod,ast.Name,ast.Load,ast.Constant)
            nodes=list(ast.walk(tree))
            if (len(nodes)>40 or any(not isinstance(n,allowed) for n in nodes) or
                    any(isinstance(n,ast.Name) and n.id!=count for n in nodes) or
                    any(isinstance(n,ast.Constant) and (type(n.value) is not int or not 0<=n.value<=width) for n in nodes) or
                    not any(isinstance(n,ast.Name) and n.id==count for n in nodes)):
                return None
            return m[1]
        rhs=strip_parens(rhs)
        result['evidence_location']={'receiver':receiver,'expression':rhs,'line':masked[:arm_offset+assign.start()].count('\n')+1}
        if rhs.startswith('{') and rhs.endswith('}'):
            parts=split(rhs[1:-1],',')
            if len(parts)==2 and set(map(shift,parts))=={'<<','>>'}:
                result.update(status='UNRESOLVED',reason='The reported narrowing concatenation risk is still structurally present.',
                    observed={'receiver_width':width,'operand_widths':[width,width],'concatenation_width':2*width,
                              'discarded_high_bits':width,'expression':rhs},expected_change=risk['expected_change'])
        else:
            parts=split(rhs,'|')
            if len(parts)==2 and set(map(shift,parts))=={'<<','>>'}:
                result.update(status='STRUCTURE_CHANGED',reason='A same-width bitwise combination replaced the tracked concatenation; no functional correctness claim.')
        return result
    except (ValueError,TypeError,KeyError,AttributeError,IndexError,SyntaxError,RecursionError):
        return result


def check_repair_boundary_v6(risk: dict, spec: str, origin: str, candidate: str,
                             limits: dict | None = None) -> dict:
    """Opt-in CPU diagnostic; old V5 API and Agent dispatch stay unchanged."""
    unknown = {'schema_version':1, 'status':'INDETERMINATE',
               'reason_code':'INPUT_BINDING_INVALID', 'reason':'Risk, source or public specification is not bound.',
               'functional_proof':False, 'target_property_proven':False,
               'scope':'BOUND_PUBLIC_ROTATION_CASE_UNSIGNED_TWO_STATE_EXPRESSION_ONLY',
               'evidence':{}, 'budget':{'steps_used':0}}
    try:
        if (not isinstance(spec,str) or not isinstance(origin,str) or not isinstance(candidate,str) or
                len(spec)>262144 or len(origin)>262144 or len(candidate)>262144):
            return unknown
        if (not isinstance(risk,dict) or risk.get('risk_type')!='FULL_WIDTH_SHIFT_CONCAT_NARROWING' or
                risk.get('source')!='PREVIOUS_BOUND_COMPILER_DIAGNOSTIC_AND_EXPLICIT_PUBLIC_SPEC' or
                risk.get('risk_id')!=_risk_digest(risk) or not isinstance(spec,str) or not isinstance(origin,str) or
                hashlib.sha256(spec.encode()).hexdigest()!=risk['public_spec_sha256'] or
                hashlib.sha256(origin.encode()).hexdigest()!=risk['origin_candidate_sha256']):
            return unknown
        from .boundary_semantics_v6 import analyze
        result=analyze(risk,spec,candidate,limits)
        result.update(risk_id=risk['risk_id'],public_spec_sha256=risk['public_spec_sha256'],
                      origin_candidate_sha256=risk['origin_candidate_sha256'])
        return result
    except Exception as exc:
        unknown['reason']=type(exc).__name__
        return unknown


def public_rotation_contract(spec: str, limits: dict | None = None) -> dict | None:
    """Recognize one explicit supported public contract, never a task identity.

    This deliberately does not infer free-form or revised requirements. A
    capability decision is separate from a proof about the candidate RTL.
    """
    from . import boundary_semantics_v6 as b
    try:
        budget=b.Budget(limits,b.time.monotonic);budget.tick()
        if not isinstance(spec,str) or len(spec.encode())>budget.limits['bytes']:
            return None
        if (not re.search(r'(?i)\bcombinational\b',spec) or
                re.search(r'(?i)\b(?:sequential|posedge|negedge|always_ff|clocked)\b',spec)):return None
        # Authority is checked before matching the convenient positive phrase.
        # Uncertain/revised/example prose cannot become a hard semantic veto.
        if re.search(r'(?i)\b(?:illustrative|ambiguous|conflicting|example|obsolete|'
                     r'withdrawn|revoked|ignore|disregard|supersed\w*|revis\w*|'
                     r'correction|instead|incorrect|erroneous)\b|'
                     r'\bnot\s+(?:the\s+)?(?:required|correct|valid|a\s+rule|rotate)|'
                     r'\b(?:never|do\s+not|don\x27t)\s+rotate\b|'
                     r'错误|撤销|作废|忽略|修订|更正|示例|替代',spec):return None
        rules=re.findall(r'(?i)\bRotate\s+left\s+uses\s+('+b._ID+r')\s+modulo\s+(\d+)\s*\.',spec)
        modes=re.findall(r'(?i)\b('+b._ID+r')\s*=\s*(\d+)\s+rotate\s+left\b',spec)
        if len(rules)!=1 or len(modes)!=1:return None
        count,width=rules[0][0],int(rules[0][1]);selector,mode=modes[0][0],int(modes[0][1])
        if not 0<width<=budget.limits['width']:return None
        _,ports=b._ports(spec)
        operands=[n for n,(direction,w) in ports.items() if direction=='input' and w==width and n not in (count,selector)]
        outputs=[n for n,p in ports.items() if p[0]=='output']
        if (len(operands)!=1 or len(outputs)!=1 or ports[outputs[0]][1]!=width or
                count==selector or ports.get(count,(None,0))[0]!='input' or
                ports.get(selector,(None,0))[0]!='input' or
                not 0<ports[count][1]<=budget.limits['count_bits'] or
                not 0<ports[selector][1]<=budget.limits['count_bits'] or
                not 0<=mode<1<<ports[selector][1]):return None
        return {'width':width,'operand':operands[0],'count':count,'selector':selector,
                'mode_value':mode,'syntax_profile':'PURE_COMBINATIONAL_CASE_V1'}
    except Exception:
        return None


def diagnose_public_rotation(spec: str, candidate: str, limits: dict | None = None) -> dict:
    """Public-rule-only check; no prior compiler failure or evaluator evidence.

    Proves only the selected, unsigned two-state rotation case. Unsupported
    syntax abstains. No files, tools, model calls, or task-name answer templates.
    """
    from . import boundary_semantics_v6 as b
    r={'schema_version':1,'status':'INDETERMINATE','reason_code':'PUBLIC_ROTATION_UNSUPPORTED',
       'reason':'No supported public rotation property was established.',
       'functional_proof':False,'target_property_proven':False,'evidence':{},
       'public_spec_sha256':None,'candidate_sha256':None,
       'syntax_profile':'PURE_COMBINATIONAL_CASE_V1'}
    try:
        budget=b.Budget(limits,b.time.monotonic);budget.tick()
        if not isinstance(spec,str) or not isinstance(candidate,str) or not candidate.strip():return r
        if len(spec.encode())+len(candidate.encode())>budget.limits['bytes']:return r
        r.update(public_spec_sha256=hashlib.sha256(spec.encode()).hexdigest(),
                 candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest())
        contract=public_rotation_contract(spec,limits)
        if contract is None:return r
        count,width,selector,mode=(contract[k] for k in ('count','width','selector','mode_value'))
        operands=[contract['operand']]
        r['contract_sha256']=hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest()
        raw=b.analyze(contract,spec,candidate,limits)
        if (not isinstance(raw,dict) or raw.get('status') not in ('RESOLVED','UNRESOLVED','INDETERMINATE') or
                raw.get('functional_proof') is not False or type(raw.get('target_property_proven')) is not bool or
                raw['target_property_proven'] != (raw['status']=='RESOLVED') or
                raw.get('candidate_sha256',r['candidate_sha256'])!=r['candidate_sha256']):
            r['reason_code']='ANALYSIS_OUTPUT_INVALID';return r
        if raw['status']=='INDETERMINATE':
            if raw.get('evidence') or 'counterexample' in raw:return r
            return {**r,'reason_code':raw.get('reason_code','ANALYSIS_ABSTAINED'),'reason':raw.get('reason','No proof.')}
        expression,context=b._target(spec,candidate,contract,budget)
        evidence=raw.get('evidence');actual_context=evidence.get('context') if isinstance(evidence,dict) else None
        if actual_context!=context:return {**r,'reason_code':'ANALYSIS_CONTEXT_INVALID'}
        if raw['status']=='RESOLVED':
            if (evidence.get('counts_completed')!=1<<context['count_bits'] or
                    evidence.get('data_bits_symbolic')!=width or raw.get('reason_code')!='EXACT_FINITE_BIT_DEPENDENCY_PROOF'):
                return {**r,'reason_code':'ANALYSIS_PROOF_INVALID'}
            return {**r,**raw,'public_spec_sha256':r['public_spec_sha256'],'contract_sha256':r['contract_sha256'],
                    'syntax_profile':r['syntax_profile']}
        c=evidence.get('counterexample')
        fields=('count','data','observed_value','public_rule_value','output_bit','required_source_bit')
        if not isinstance(c,dict) or any(type(c.get(k)) is not int for k in fields):return {**r,'reason_code':'COUNTEREXAMPLE_INVALID'}
        if (not 0<=c['count']<1<<context['count_bits'] or not 0<=c['output_bit']<width or
                any(not 0<=c[k]<1<<width for k in ('data','observed_value','public_rule_value')) or
                c.get('source')!='DERIVED_FROM_PUBLIC_RULE_AND_BIT_DEPENDENCIES_NOT_EVALUATOR_DATA'):
            return {**r,'reason_code':'COUNTEREXAMPLE_INVALID'}
        parser=b.Parser(expression,budget);tree=parser.parse()
        if parser.i!=len(parser.tokens):return r
        vector=b.Engine(tree,context,budget).vector(tree,c['count'],width)[:width]
        data=c['data'];actual=sum(int(-1 in terms or any(data>>k&1 for k in terms))<<i for i,terms in enumerate(vector))
        amount=c['count']%width;wanted=((data<<amount)|(data>>(width-amount)))&((1<<width)-1);bit=c['output_bit']
        if (c['observed_value']!=actual or c['public_rule_value']!=wanted or actual==wanted or
                c.get('observed_source_bits')!=sorted(vector[bit]) or c['required_source_bit']!=(bit-c['count'])%width or
                (actual>>bit&1)==(wanted>>bit&1)):
            return {**r,'reason_code':'COUNTEREXAMPLE_RECOMPUTATION_FAILED'}
        digits=(width+3)//4
        count_contract=None
        count_feedback=''
        # Explain an already-recomputed counterexample, without changing the
        # semantic verdict, frontend, accepted syntax or Repair scheduling.
        # Only the proved OR-of-two-shifts periodicity failure is supported.
        try:
            shifts=tree[2:4] if tree[0]=='op' and tree[1]=='|' else ()
            if (c['count']>=width and len(shifts)==2 and
                    all(n[0]=='op' and n[1] in ('<<','>>') and
                        n[2]==('id',operands[0]) for n in shifts)):
                engine=b.Engine(tree,context,budget)
                reduced=engine.vector(tree,amount,width)[:width]
                reduced_value=sum(int(-1 in terms or any(data>>k&1 for k in terms))<<i
                                  for i,terms in enumerate(reduced))
                if reduced_value==wanted and reduced_value!=actual:
                    counts=[]
                    for node in shifts:
                        raw_count,raw_width,raw_signed=engine.count(node[3],c['count'])
                        normalized_count,normalized_width,normalized_signed=engine.count(node[3],amount)
                        counts.append({'operator':node[1],'actual_shift_count':raw_count,
                                       'count_expression_width':raw_width,'count_expression_signed':raw_signed,
                                       'shift_count_at_reduced_input':normalized_count})
                    count_contract={'source':'RECOMPUTED_CURRENT_EXPRESSION_AND_PUBLIC_MODULO_PROPERTY',
                        'candidate_sha256':r['candidate_sha256'],'public_spec_sha256':r['public_spec_sha256'],
                        'selector':selector,'mode_value':mode,'count_name':count,'operand':operands[0],
                        'width':width,'counterexample_count':c['count'],'reduced_count':amount,'data':data,
                        'actual_value':actual,'same_expression_at_reduced_count':reduced_value,
                        'public_rule_value':wanted,'shift_counts':counts,
                        'required_property':f'For this rotation branch, f({count}) = f({count} modulo {width}).',
                        'preserve_other_modes':True,'complete_functional_proof':False}
                    json.dumps(count_contract,allow_nan=False)
                    count_feedback=(
                        '\nMachine-checked count trace: '+json.dumps(count_contract,sort_keys=True)+'\n'
                        f'Localized Repair obligation for {selector}={mode}: normalize {count} to '
                        f'its public rotation value ({count} modulo {width}) BEFORE computing either '
                        'directional shift amount. Use that SAME reduced count consistently in both '
                        'terms, including the complementary subtraction. The expression already produces '
                        f'0x{reduced_value:0{digits}X} at the equivalent reduced input {amount}, but '
                        f'0x{actual:0{digits}X} at the current legal input {c["count"]}; this proves '
                        'that its count handling is not periodic as required. Do not merely add '
                        'begin/end or special-case this one witness. Keep every nonrotation mode and '
                        'its full original shift amount unchanged. Whole-width and zero rotations '
                        'must be identities. Recheck the complete count domain before returning RTL.\n')
        except b.Abstain:
            # Optional explanation cannot erase a validated original witness.
            count_contract=None
            count_feedback=''
        feedback=(f'Public rotation property violation derived from your current RTL, not an independent test result.\n'
                  f'Bound case {selector}={mode}, {context["receiver"]} width={width}, expression={json.dumps(expression)}.\n'
                  f'For {count}={c["count"]} (effective rotation {amount}), {operands[0]}=0x{data:0{digits}X}, '
                  f'current result=0x{actual:0{digits}X}; public rule requires 0x{wanted:0{digits}X}. '
                  f'Output bit {bit}: observed_source_bits={c["observed_source_bits"]}, required_source_bit={c["required_source_bit"]}.\n'
                  f'{count_feedback}'
                  f'Correct the selected rotation for every legal {count}, using its public meaning modulo {width}. '
                  f'Zero or whole-width rotation must preserve the input; preserve all wraparound contributions. '
                  'Changing spelling, concatenation order, or merely compiling does not prove this property. '
                  'Keep public ports, widths, signedness, other operations and timing unchanged. '
                  'Return one complete RTL implementation under the existing final output contract. '
                  'This is a limited target-property check, never complete functional acceptance.')
        if len(feedback)>4000 and count_feedback:
            feedback=feedback.replace(count_feedback,'',1)
            count_contract=None
        if len(feedback)>4000:return {**r,'reason_code':'FEEDBACK_SIZE_LIMIT'}
        bound={**r,**raw,'public_spec_sha256':r['public_spec_sha256'],'contract_sha256':r['contract_sha256'],
               'syntax_profile':r['syntax_profile'],'repair_feedback':feedback}
        if count_contract is not None:
            bound['repair_count_contract']=count_contract
        json.dumps(bound,allow_nan=False);return bound
    except b.Abstain as exc:
        return {**r,'reason_code':str(exc)}
    except Exception as exc:
        return {**r,'reason_code':'PUBLIC_ROTATION_EXCEPTION','reason':type(exc).__name__}
