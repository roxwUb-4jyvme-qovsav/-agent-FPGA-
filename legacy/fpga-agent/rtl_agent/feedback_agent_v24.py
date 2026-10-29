"""Agent v2.4: bounded, staged RTL generation with conservative self-checks."""
from __future__ import annotations

import hashlib
import json
import os
import signal
from pathlib import Path
import time
import uuid

from .behavior_selftest_v24 import run_selftest
from .candidate_tools_v24 import check_compile, check_synthesis
from .core import write_json
from .interface_contract import extract_public_contract
from .local_model import complete_spec, extract_candidate, tokenize_chat, validate_candidate
from .official_levels import official_result
from .release_interface_policy import check_interface_contract
from .targeted_diagnostics import diagnose_interface, normalized_candidate_hash
from .targeted_diagnostics_v24 import diagnose_stage, make_repair_risk, check_repair_risk
from .targeted_diagnostics_v24 import check_repair_boundary_v6
from .targeted_diagnostics_v24 import diagnose_public_rotation, public_rotation_contract
from .runtime_audit import ACTIVE, Journal, durable_json, install_signal_handlers


def _render_repair_edit_request(base_prompt: str, previous: str, repair_goal: str) -> str:
    """Keep frozen guidance and specification; put the rejected RTL before its correction."""
    return ("Repair the rejected implementation below; this is a correction task, not a fresh design.\n"
            "The exact public specification and its measured behavioral requirement govern timing; "
            "generic implementation-style advice must not add a cycle or override that requirement.\n\n" +
            base_prompt + "\n\nPrevious candidate (rejected; not the final answer):\n```systemverilog\n" +
            previous + "\n```\n\nRepair goal from checks on your own candidate only:\n" + repair_goal +
            "\nApply the required behavioral correction to that candidate. Preserve unrelated specified behavior. "
            "Do not return the rejected implementation unchanged.\n"
            "Final output contract: return the complete corrected RTL containing exactly one top-level module "
            "named TopModule. Do not output multiple alternative TopModule implementations in the same final "
            "response. Return only the single final RTL implementation; do not substitute explanatory prose, "
            "candidate comparisons, or a second RTL version for the final implementation.")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _append(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


# The approved public development scope, not an RTL answer or task-name rule.
_BOUNDARY_V6_PUBLIC_SPEC_SHA256 = '7ec90b75ea0b0b0be9cbb75ad633d18def219aac00582132ad92b50a6f90d54a'


def _boundary_review_v6(risk: dict, spec: str, origin: str, code: str, remaining_s: float) -> dict | None:
    """Fail closed on diagnostic output; never alter old V5 status semantics."""
    if (_sha(spec) != _BOUNDARY_V6_PUBLIC_SPEC_SHA256 or not isinstance(risk,dict) or
            risk.get('risk_type') != 'FULL_WIDTH_SHIFT_CONCAT_NARROWING'):
        return None
    unknown = {'schema_version':1,'status':'INDETERMINATE','reason_code':'INTEGRATION_OUTPUT_INVALID',
        'reason':'No trustworthy target-property result available.','functional_proof':False,
        'target_property_proven':False,'evidence':{},'risk_id':risk.get('risk_id'),
        'public_spec_sha256':_sha(spec),'origin_candidate_sha256':_sha(origin),'candidate_sha256':_sha(code),
        'scope':'BOUND_PUBLIC_ROTATION_CASE_UNSIGNED_TWO_STATE_EXPRESSION_ONLY'}
    try:
        r = check_repair_boundary_v6(risk,spec,origin,code,limits={'seconds':max(0.0,min(1.0,remaining_s))})
        if (not isinstance(r,dict) or r.get('status') not in ('RESOLVED','UNRESOLVED','INDETERMINATE') or
                r.get('functional_proof') is not False or type(r.get('target_property_proven')) is not bool or
                r['target_property_proven'] != (r['status']=='RESOLVED') or
                not isinstance(r.get('evidence'),dict) or not isinstance(r.get('reason'),str) or
                not isinstance(r.get('reason_code'),str)):
            return unknown
        # The API may abstain before it can produce bound result fields.
        for key in ('risk_id','public_spec_sha256','origin_candidate_sha256','candidate_sha256'):
            expected = risk['risk_id'] if key=='risk_id' else unknown[key]
            if r.get(key,expected)!=expected or (r['status']!='INDETERMINATE' and key not in r):
                return unknown
        if r.get('scope') != unknown['scope']:
            return unknown
        if r['status']=='INDETERMINATE':
            if r['evidence'] or 'counterexample' in r:
                return unknown
            return {**unknown,**r}
        if r['status']=='UNRESOLVED':
            evidence=r['evidence'];c=evidence.get('counterexample');context=evidence.get('context')
            if not isinstance(c,dict) or not isinstance(context,dict):return unknown
            names=('count','data','observed_value','public_rule_value','output_bit','required_source_bit')
            if any(type(c.get(k)) is not int for k in names):return unknown
            width=risk['width'];count_bits=context.get('count_bits')
            if (type(width) is not int or not 0<width<=64 or type(count_bits) is not int or not 0<count_bits<=6 or
                context.get('width')!=width or context.get('operand')!=risk['operand'] or context.get('count')!=risk['count'] or
                not isinstance(context.get('expression'),str) or c.get('source')!='DERIVED_FROM_PUBLIC_RULE_AND_BIT_DEPENDENCIES_NOT_EVALUATOR_DATA' or
                not 0<=c['count']<1<<count_bits or any(not 0<=c[k]<1<<width for k in ['data','observed_value','public_rule_value']) or
                not 0<=c['output_bit']<width):return unknown
            terms=c.get('observed_source_bits')
            if (not isinstance(terms,list) or len(terms)>width+1 or len(set(terms))!=len(terms) or
                    any(type(k) is not int or not -1<=k<width for k in terms)):
                return unknown
            bit=c['output_bit'];amount=c['count']%width;data=c['data']
            public=((data<<amount)|(data>>(width-amount)))&((1<<width)-1)
            observed_bit=int(-1 in terms or any(data>>k&1 for k in terms if k>=0))
            if (c['required_source_bit']!=(bit-c['count'])%width or c['public_rule_value']!=public or
                    (c['observed_value']>>bit&1)!=observed_bit or observed_bit==(public>>bit&1)):
                return unknown
            # Render only validated evidence; retain the full diagnostic in r.
            json.dumps(r,allow_nan=False)
            receiver=context.get('receiver');selector=risk.get('selector');mode=risk.get('mode_value')
            operand=context['operand'];count_name=context['count'];expression=context['expression']
            if (any(not isinstance(name,str) or not name for name in
                    (receiver,selector,operand,count_name,expression)) or type(mode) is not int):
                return unknown
            digits=(width+3)//4
            if not terms:
                cause='The selected output bit has no input contribution and is constant zero.'
            elif -1 in terms:
                cause='The selected output bit includes constant one, regardless of the input bits.'
            else:
                cause='The selected output bit depends on a different set of input bits than the public rule requires.'
            sizing_note=''
            # Explain only an already-proved witness, never change its verdict.
            try:
                from .boundary_semantics_v6 import Budget, Parser, Engine
                if len(expression.encode('utf-8'))<=4096 and expression in code:
                    explanation_budget=Budget({'tokens':128,'steps':4096,
                                               'seconds':min(0.02,max(0.0,remaining_s))},time.monotonic)
                    parser=Parser(expression,explanation_budget)
                    tree=parser.parse()
                    if parser.i==len(parser.tokens) and tree[0]=='concat' and len(tree[1])==2:
                        engine=Engine(tree,context,explanation_budget)
                        shifts=tree[1]
                        if all(n[0]=='op' and n[1] in ('<<','>>') and
                               n[2]==('id',operand) and engine.width(n)==width for n in shifts):
                            vector=engine.vector(tree,c['count'],width)[:width]
                            value=sum(int(-1 in t or any(data>>k&1 for k in t if k>=0))<<i
                                      for i,t in enumerate(vector))
                            dropped=engine.vector(shifts[0],c['count'])
                            if (value==c['observed_value'] and sorted(vector[bit])==terms and
                                c['required_source_bit'] in dropped[bit] and
                                observed_bit==0 and (public>>bit)&1):
                                high_shift=engine.count(shifts[0][3],c['count'])[0]
                                low_shift=engine.count(shifts[1][3],c['count'])[0]
                                sizing_note=(
                                    f'Width trace (recomputed at this witness): each shift width={width}; '
                                    f'concat width={2*width}; destination width={width}. '
                                    f'The assignment retains range=[{width-1}:0], the right concatenation term '
                                    f'({operand} {shifts[1][1]} {low_shift}), and discards range=[{2*width-1}:{width}], '
                                    f'the left concatenation term ({operand} {shifts[0][1]} {high_shift}). '
                                    f'That discarded term carries input bit {c["required_source_bit"]} '
                                    f'to its bit {bit}, but the retained term does not: the required contribution is lost. '
                                    'Swapping the two full-width terms still discards one term; '
                                    'preserve both required contributions in the destination under the public rule.\n')
            except Exception:
                # Optional explanation failure must not erase validated evidence.
                sizing_note=''
            feedback=(
                'Public-specification-derived boundary counterexample, not an independent test result.\n'
                f'Bound target: {selector}={mode}; {receiver} is {width}-bit unsigned. '
                f'Expression: {json.dumps(expression,ensure_ascii=False)}.\n'
                f'For {count_name}={c["count"]}, {operand}=0x{data:0{digits}X} ({data}), '
                f'the expression predicts {receiver}=0x{c["observed_value"]:0{digits}X} ({c["observed_value"]}); '
                f'the public rule requires 0x{public:0{digits}X} ({public}).\n'
                f'Output bit {bit}: observed_source_bits={json.dumps(terms)} '
                f'(empty means zero; -1 means constant one), required_source_bit={c["required_source_bit"]} '
                f'of {operand}; actual bit={observed_bit}, required bit={(public>>bit)&1}. {cause}\n'
                f'{sizing_note}'
                f'Repair the missing or incorrect contribution, not just the expression spelling or operand order. '
                f'For every legal {count_name}, use its public rotation count modulo {width} and preserve '
                f'output bit i from {operand}[(i - ({count_name} % {width})) modulo {width}]. '
                'An effective count of zero must preserve the input, including counts equal to the data width. '
                'This witness proves a target-property violation, not the cause of any unanalysed truncation. '
                'Do not assume every widened expression is wrong or copy a fixed RTL template. '
                'Keep public ports, widths, signedness, other operations and timing unchanged. '
                'Return exactly one complete RTL implementation under the existing final output contract; '
                'no alternatives or explanatory prose. A resolved target still requires all original checks.')
            if len(feedback)>4000 and sizing_note:
                feedback=feedback.replace(sizing_note,'',1)
            if len(feedback)>4000:return unknown
            return {**r,'repair_feedback':feedback}
        context=r['evidence'].get('context',{})
        bits=context.get('count_bits')
        if (context.get('width')!=risk['width'] or context.get('operand')!=risk['operand'] or
                context.get('count')!=risk['count'] or type(bits) is not int or not 0<bits<=6 or
                r['evidence'].get('counts_completed')!=1<<bits or
                r['evidence'].get('data_bits_symbolic')!=risk['width'] or
                r.get('reason_code')!='EXACT_FINITE_BIT_DEPENDENCY_PROOF'):
            return unknown
        # A bounded property proof is not overall functionality or an EDA verdict.
        json.dumps(r,allow_nan=False)
        return r
    except Exception as exc:
        return {**unknown,'reason_code':'DIAGNOSTIC_EXCEPTION','reason':type(exc).__name__}


def _public_rotation_review(spec: str, code: str, round_dir: Path, remaining_s: float) -> dict | None:
    """Persist a bound result before it may influence the existing Repair path."""
    limits={'seconds':max(0.0,min(0.25,remaining_s))}
    contract=public_rotation_contract(spec,limits)
    if contract is None:
        return None
    contract_digest=_sha(json.dumps(contract,sort_keys=True))
    review=diagnose_public_rotation(spec,code,limits=limits)
    if (not isinstance(review,dict) or review.get('status') not in ('RESOLVED','UNRESOLVED','INDETERMINATE') or
            review.get('public_spec_sha256')!=_sha(spec) or review.get('candidate_sha256')!=_sha(code) or
            review.get('contract_sha256')!=contract_digest or
            review.get('functional_proof') is not False or type(review.get('target_property_proven')) is not bool or
            review['target_property_proven'] != (review['status']=='RESOLVED') or
            not isinstance(review.get('evidence'),dict) or
            (review['status']=='INDETERMINATE' and (review['evidence'] or 'counterexample' in review or 'repair_feedback' in review)) or
            (review['status']=='UNRESOLVED' and (not isinstance(review.get('repair_feedback'),str) or
                                               not review['repair_feedback'] or len(review['repair_feedback'])>4000))):
        review={'status':'INDETERMINATE','reason_code':'PUBLIC_REVIEW_OUTPUT_INVALID','evidence':{},
                'functional_proof':False,'target_property_proven':False,
                'public_spec_sha256':_sha(spec),'candidate_sha256':_sha(code),'contract_sha256':contract_digest}
    destination=round_dir/'public_rotation_review.json'
    durable_json(destination,review)
    if json.loads(destination.read_text(encoding='utf-8'))!=review:
        raise RuntimeError('PUBLIC_ROTATION_EVIDENCE_READBACK_FAILED')
    return review


def generate_agent_v24(spec_path: Path, model_config: dict, agent_config: dict,
                       output: Path, *, top: str = "TopModule",
                       vivado_bin: str | None = None, eda_root: Path | None = None) -> dict:
    import threading
    token = ACTIVE.set(ACTIVE.get())
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)} if threading.current_thread() is threading.main_thread() else {}
    try:
        return _generate_agent_v24(spec_path, model_config, agent_config, output,
                                   top=top, vivado_bin=vivado_bin, eda_root=eda_root)
    finally:
        ACTIVE.reset(token)
        for sig, handler in saved.items():
            signal.signal(sig, handler)


def _generate_agent_v24(spec_path: Path, model_config: dict, agent_config: dict,
                        output: Path, *, top: str = "TopModule",
                        vivado_bin: str | None = None, eda_root: Path | None = None) -> dict:
    started = time.monotonic()
    limit = float(agent_config["max_wall_time_s"])
    deadline = started + limit
    if os.environ.get("AGENT_DEADLINE_MONOTONIC"):
        deadline = min(deadline, float(os.environ["AGENT_DEADLINE_MONOTONIC"]))
    work = output.resolve() / ("agent_v24_" + uuid.uuid4().hex[:12])
    work.mkdir(parents=True, exist_ok=False)
    trajectory = work / "trajectory.jsonl"
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    costs = {name: 0 for name in ("model_calls", "tokenizer_calls", "interface_checks",
                                 "xvlog", "xelab", "xsim", "synth_design", "repair_rounds")}
    result = {"agent_version": "2.4", "status": "NOT_RUN", "stop_reason": "",
              "final_candidate": None, "work_dir": str(work), "rounds": [],
              "scope": {"public_spec_only": True, "hidden_testbench": False,
                        "reference_rtl": False, "oracle": False, "training": False}}
    ledger = {"agent_version": "2.4", "limits": {key: agent_config[key] for key in
              ("max_rounds", "max_total_tokens", "max_eda_calls", "max_wall_time_s")},
              "usage": totals, "counts": costs, "stop_reason": ""}
    ledger["eda_budget_unit"] = "candidate_batches (legacy max_eda_calls)"
    ledger["eda_batches"] = 0
    journal = Journal(work, output, ledger, totals, costs, model_config["model"], deadline)
    eda_root = (eda_root or work / "eda").resolve()
    eda_root.mkdir(parents=True, exist_ok=True)
    spec = spec_path.read_text(encoding="utf-8-sig")
    if not spec.strip():
        raise ValueError("Public prompt is empty")
    contract = extract_public_contract(spec)
    if contract["top_module"] and contract["top_module"] != top:
        raise ValueError("Explicit public top module differs from configured top")
    skill_path = Path(agent_config["skill_path"])
    skill_bytes = skill_path.read_bytes()
    if hashlib.sha256(skill_bytes).hexdigest() != agent_config["expected_skill_sha256"]:
        raise ValueError("Frozen Skill v4 hash mismatch")
    skill = skill_bytes.decode("utf-8-sig")
    write_json(work / "public_contract.json", contract)
    (work / "public_spec.txt").write_text(spec, encoding="utf-8")
    candidate_eda_calls = 0
    previous = ""
    repair_goal = ""
    repair_edit_binding = None
    pending_risk = None
    risk_origin = ""
    seen: set[str] = set()
    seen_normalized: set[str] = set()
    context_token = ACTIVE.set(journal)
    old_signals = install_signal_handlers(journal)
    try:
        for index in range(int(agent_config["max_rounds"])):
            round_dir = work / f"round_{index:02d}"
            round_dir.mkdir()
            rec: dict = {"round": index, "status": "STARTED", "stages": {},
                         "wall_started_s": round(time.monotonic() - started, 3)}
            result["rounds"].append(rec)
            journal.round = index
            journal.event("round_start", "start", status="STARTED")
            if time.monotonic() >= deadline:
                result.update(status="TIMEOUT", stop_reason="WALLCLOCK_TIMEOUT")
                break
            if candidate_eda_calls >= agent_config["max_eda_calls"]:
                result.update(status="BUDGET_EXHAUSTED", stop_reason="EDA_BUDGET_EXHAUSTED")
                break
            if index:
                costs["repair_rounds"] += 1
                rec["repair_goal"] = repair_goal
                journal.event("repair", "start", goal_sha256=_sha(repair_goal))
            prompt = ("Generate a complete synthesizable SystemVerilog implementation of the public "
                      "specification below. The top module must be TopModule. Output only RTL.\n\n"
                      "Frozen domain guidance:\n" + skill + "\n\nPublic specification:\n" + spec)
            if repair_goal:
                if repair_edit_binding == (_sha(previous), _sha(repair_goal)):
                    prompt = _render_repair_edit_request(prompt, previous, repair_goal)
                else:
                    prompt += ("\n\nRepair goal from checks on your own candidate only:\n" + repair_goal +
                               "\nPrevious candidate:\n```systemverilog\n" + previous + "\n```\n"
                               "Final output contract: return the complete corrected RTL containing exactly one "
                               "top-level module named TopModule. Do not output multiple alternative "
                               "TopModule implementations in the same final response. Return only the "
                               "single final RTL implementation; do not substitute explanatory prose, "
                               "candidate comparisons, or a second RTL version for the final implementation.")
            (round_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            messages = [{"role": "user", "content": prompt}]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result.update(status="TIMEOUT", stop_reason="WALLCLOCK_TIMEOUT")
                break
            costs["tokenizer_calls"] += 1
            journal.event("tokenizer", "start", usage_known=False)
            tok = tokenize_chat(model_config, messages, round_dir / "tokenizer",
                                min(float(model_config["timeout_s"]), remaining))
            rec["tokenizer"] = {"prompt_tokens": tok["count"],
                                "server_context": tok["max_model_len"],
                                "elapsed_s": tok["elapsed_s"]}
            journal.event("tokenizer", "end", tokens_in=tok["count"], context_limit=tok["max_model_len"])
            if model_config.get("context_tokens") not in (None, tok["max_model_len"]):
                result.update(status="ERROR", stop_reason="MODEL_CONTEXT_MISMATCH")
                break
            budget_left = agent_config["max_total_tokens"] - totals["total_tokens"]
            allowance = min(model_config["max_tokens"], tok["max_model_len"] - tok["count"],
                            budget_left - tok["count"])
            rec["request_max_tokens"] = max(0, allowance)
            if allowance < agent_config["min_completion_tokens"]:
                reason = ("CONTEXT_LIMIT_EXCEEDED" if tok["max_model_len"] - tok["count"] <
                          agent_config["min_completion_tokens"] else "TOTAL_TOKEN_BUDGET_EXHAUSTED")
                result.update(status="BUDGET_EXHAUSTED", stop_reason=reason)
                break
            call_config = dict(model_config, max_tokens=allowance,
                               timeout_s=min(float(model_config["timeout_s"]),
                                             max(0.1, deadline - time.monotonic())))
            costs["model_calls"] += 1
            journal.event("llm", "start", model=model_config["model"], request_max_tokens=allowance,
                          tokens_in=None, tokens_out=None)
            answer = complete_spec(call_config, prompt, round_dir, require_stop=False)
            journal.account(answer["response"])
            if answer["request"].get("messages") != messages:
                result.update(status="ERROR", stop_reason="MODEL_PROMPT_MISMATCH")
                break
            usage = answer["response"].get("usage", {})
            if any(type(usage.get(k)) is not int or usage[k] < 0 for k in totals) or usage["prompt_tokens"] != tok["count"] or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
                result.update(status="ERROR", stop_reason="TOKEN_ACCOUNTING_MISMATCH")
                break
            rec.update(model_usage=usage, model_elapsed_s=answer["elapsed_s"],
                       finish_reason=answer["finish_reason"])
            if totals["total_tokens"] > agent_config["max_total_tokens"]:
                result.update(status="BUDGET_EXHAUSTED", stop_reason="TOTAL_TOKEN_BUDGET_EXHAUSTED")
                break
            if answer["finish_reason"] != "stop":
                result.update(status="ERROR", stop_reason="OUTPUT_TRUNCATED" if answer["finish_reason"] == "length" else "MODEL_GENERATION_ERROR")
                break
            if not isinstance(answer["content"], str) or not answer["content"].strip():
                result.update(status="ERROR", stop_reason="MODEL_GENERATION_ERROR")
                break
            code = extract_candidate(answer["content"], top)
            digest = _sha(code)
            rec["candidate_sha256"] = digest
            if digest in seen:
                result.update(status="ERROR", stop_reason="REPEATED_CANDIDATE")
                break
            normalized = normalized_candidate_hash(code)
            if normalized in seen_normalized:
                result.update(status="ERROR", stop_reason="INEFFECTIVE_MODIFICATION")
                break
            seen.add(digest)
            seen_normalized.add(normalized)
            candidate = round_dir / "candidate.sv"
            with candidate.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(code)
            costs["interface_checks"] += 1
            journal.event("check_interface", "start")
            interface = check_interface_contract(contract, code)
            journal.event("check_interface", "end", status=interface["status"])
            write_json(round_dir / "interface_result.json", interface)
            rec["interface"] = {"status": interface["status"],
                                "issues": interface["issues"],
                                "advisories": interface.get("advisories", [])}
            if interface["status"] == "FAIL":
                diagnostic = diagnose_interface(interface, code, 4000)
                write_json(round_dir / "targeted_diagnostic.json", diagnostic)
                repair_goal = diagnostic["repair_feedback"]
                rec["diagnostic_category"] = diagnostic["category"]
                previous = code
                rec["status"] = "INTERFACE_REJECTED"
            else:
                static = validate_candidate(code, top)
                journal.event("static_check", "end", status=static["status"])
                write_json(round_dir / "static_result.json", static)
                rec["static"] = static["status"]
                postcheck = None
                boundary_v6 = None
                if static["status"] == "PASS" and pending_risk is not None:
                    postcheck = check_repair_risk(pending_risk, spec, risk_origin, code)
                    write_json(round_dir / "repair_postcheck.json", postcheck)
                    rec["repair_postcheck"] = postcheck
                    boundary_v6 = _boundary_review_v6(pending_risk,spec,risk_origin,code,deadline-time.monotonic())
                    if boundary_v6 is not None:
                        write_json(round_dir / "repair_boundary_v6.json", boundary_v6)
                        rec["repair_boundary_v6"] = boundary_v6
                        if time.monotonic() >= deadline:
                            result.update(status="TIMEOUT", stop_reason="WALLCLOCK_TIMEOUT")
                            break
                if static["status"] != "PASS":
                    repair_goal = "Static RTL policy: " + "; ".join(static["problems"])
                    previous = code
                    rec["status"] = "STATIC_REJECTED"
                elif boundary_v6 and boundary_v6["status"] == "UNRESOLVED":
                    repair_goal = boundary_v6["repair_feedback"]
                    write_json(round_dir / "repair_boundary_feedback_v6.json", boundary_v6)
                    previous = code
                    rec["status"] = "REPAIR_BOUNDARY_UNRESOLVED"
                elif postcheck and postcheck["status"] == "UNRESOLVED":
                    # A previously reported, evidenced structure remains. Reuse
                    # the normal next-round path and all existing hard budgets;
                    # do not spend EDA or accept this as a repaired candidate.
                    repair_goal = ("Repair postcondition evidence (candidate-only; not an independent test):\n" +
                                   json.dumps(postcheck, ensure_ascii=False, sort_keys=True))
                    write_json(round_dir / "repair_postcondition_feedback.json", postcheck)
                    previous = code
                    rec["status"] = "REPAIR_RISK_UNRESOLVED"
                elif candidate_eda_calls >= agent_config["max_eda_calls"]:
                    result.update(status="BUDGET_EXHAUSTED", stop_reason="EDA_BUDGET_EXHAUSTED")
                    break
                else:
                    candidate_eda_calls += 1
                    ledger["eda_batches"] = candidate_eda_calls
                    journal.snapshot()
                    compile_result = check_compile(candidate, eda_root / f"round_{index:02d}" / "compile", deadline,
                                                   vivado_bin, top)
                    rec["stages"]["compile"] = compile_result
                    if compile_result["status"] == "TIMEOUT":
                        result.update(status="TIMEOUT", stop_reason="WALLCLOCK_TIMEOUT")
                        break
                    if compile_result["status"] == "INFRA_ERROR":
                        result.update(status="INFRA_ERROR", stop_reason="EDA_INFRA_ERROR")
                        break
                    if compile_result["status"] != "PASS":
                        diagnostic = diagnose_stage(compile_result.get("failed_stage", "analyze"),
                                                    eda_root / f"round_{index:02d}" / "compile", code,
                                                    public_spec=spec)
                        write_json(round_dir / "targeted_diagnostic.json", diagnostic)
                        repair_goal = diagnostic["repair_feedback"]
                        rec["diagnostic_category"] = diagnostic["category"]
                        previous = code
                        rec["status"] = "COMPILE_REJECTED"
                        new_risk = make_repair_risk(diagnostic, spec, code)
                        if new_risk is not None:
                            pending_risk, risk_origin = new_risk, code
                            write_json(round_dir / "repair_risk.json", new_risk)
                            rec["repair_risk_id"] = new_risk["risk_id"]
                    else:
                        selfcheck = run_selftest(spec, candidate, eda_root / f"round_{index:02d}" / "selftest",
                                                 deadline, vivado_bin, top)
                        rec["stages"]["selftest"] = selfcheck
                        journal.event("selftest", "end", status=selfcheck["status"], not_official_oracle=True)
                        public_rotation=_public_rotation_review(spec,code,round_dir,deadline-time.monotonic())
                        if public_rotation is not None:
                            rec['stages']['public_rotation']=public_rotation
                            journal.event('public_rotation','end',status=public_rotation['status'],not_official_oracle=True)
                        if time.monotonic()>=deadline:
                            result.update(status='TIMEOUT',stop_reason='WALLCLOCK_TIMEOUT')
                            break
                        if public_rotation and public_rotation['status']=='UNRESOLVED':
                            repair_goal=public_rotation['repair_feedback']
                            previous=code
                            rec['status']='PUBLIC_ROTATION_UNRESOLVED'
                            if index+1>=agent_config['max_rounds']:
                                result.update(status='UNSOLVED',stop_reason='PUBLIC_ROTATION_UNRESOLVED')
                        elif selfcheck["status"] == "RTL_SUSPECT":
                            # The requirement is a complete public truth table, but
                            # our generated testbench is still fallible. Ask for a
                            # review against the public source, not a blind patch.
                            bound_public_feedback = selfcheck.get('repair_feedback')
                            if (selfcheck.get('source') == 'public_spec_only' and
                                selfcheck.get('candidate_sha256') == digest and
                                selfcheck.get('requirement_sha256') == _sha(spec) and
                                isinstance(bound_public_feedback,str) and
                                0 < len(bound_public_feedback) <= 2000 and
                                selfcheck.get('counterexamples')):
                                repair_goal = bound_public_feedback
                                repair_edit_binding = ((digest, _sha(repair_goal))
                                                       if selfcheck.get('public_probe_kind') == 'accepted_bit_sequence'
                                                       else None)
                            else:
                                repair_goal = ("Review the RTL against the explicit public truth table. "
                                           "Our self-generated probe suspects a mismatch but is not an oracle. "
                                           "Correct the RTL only if the public requirement supports it.\n" +
                                           str(selfcheck.get("reason", "")))[:2000]
                            previous = code
                            rec["status"] = "RTL_SUSPECT"
                            if index + 1 >= agent_config["max_rounds"]:
                                result.update(status="UNSOLVED", stop_reason="SELFTEST_RTL_SUSPECT_UNRESOLVED")
                        else:
                            synth = check_synthesis(candidate, eda_root / f"round_{index:02d}" / "synth",
                                                    deadline, vivado_bin, top)
                            rec["stages"]["synthesis"] = synth
                            if synth["status"] == "PASS":
                                final = work / "final_candidate.sv"
                                with final.open("w", encoding="utf-8", newline="\n") as stream:
                                    stream.write(code)
                                result.update(status="GENERATED", stop_reason="SYNTH_PASS_FUNCTION_UNPROVEN",
                                              final_candidate=str(final), candidate_sha256=digest,
                                              selftest_status=selfcheck["status"])
                                rec["status"] = "ACCEPTED_CANDIDATE"
                            elif synth["status"] == "FAIL":
                                diagnostic = diagnose_stage("synthesize",
                                                            eda_root / f"round_{index:02d}" / "synth", code)
                                write_json(round_dir / "targeted_diagnostic.json", diagnostic)
                                repair_goal = diagnostic["repair_feedback"]
                                rec["diagnostic_category"] = diagnostic["category"]
                                previous = code
                                rec["status"] = "SYNTH_REJECTED"
                            elif synth["status"] == "TIMEOUT":
                                result.update(status="TIMEOUT", stop_reason="WALLCLOCK_TIMEOUT")
                            else:
                                result.update(status="INFRA_ERROR", stop_reason="EDA_INFRA_ERROR")
            rec["wall_elapsed_s"] = round(time.monotonic() - started, 3)
            _append(trajectory, {"record_type": "ROUND_SNAPSHOT", **rec})
            journal.snapshot()
            if result["status"] != "NOT_RUN":
                break
        if result["status"] == "NOT_RUN":
            result.update(status="UNSOLVED", stop_reason="ROUND_BUDGET_EXHAUSTED")
    except Exception as exc:
        from .local_model import RequestTimeout
        reason = ("PROCESS_INTERRUPTED" if isinstance(exc, InterruptedError) else
                  "WALLCLOCK_TIMEOUT" if isinstance(exc, RequestTimeout) or time.monotonic() >= deadline
                  else type(exc).__name__)
        result.update(status="TIMEOUT" if reason == "WALLCLOCK_TIMEOUT" else "ERROR",
                      stop_reason=reason, error=str(exc)[:400])
    # Early budget/error exits still need a full round trajectory entry.
    persisted_rounds = set()
    if trajectory.is_file():
        for line in trajectory.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("record_type") == "ROUND_SNAPSHOT" or (
                    "event" not in row and "status" in row and "round" in row
                ):
                    persisted_rounds.add(row["round"])
            except (ValueError, KeyError):
                pass
    for rec in result["rounds"]:
        if rec["round"] not in persisted_rounds:
            if rec["status"] == "STARTED":
                rec["status"] = result["status"]
            rec["wall_elapsed_s"] = round(time.monotonic() - started, 3)
            _append(trajectory, {"record_type": "ROUND_SNAPSHOT", **rec})
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    result["usage"] = dict(totals)
    result["costs"] = dict(costs)
    result["official_level"] = official_result(candidate_checks={
        "synthesis": result.get("stop_reason") == "SYNTH_PASS_FUNCTION_UNPROVEN",
        "selftest": result.get("selftest_status", "INDETERMINATE")})
    ledger["stop_reason"] = result["stop_reason"]
    journal.event("agent_stop", "end", status=result["status"], reason=result["stop_reason"],
                  usage=dict(totals), costs=dict(costs), sec=result["elapsed_s"], official_level=None)
    durable_json(work / "budget_ledger.json", ledger)
    write_json(work / "agent_result.json", result)
    ACTIVE.reset(context_token)
    for sig, handler in old_signals.items():
        signal.signal(sig, handler)
    return result
