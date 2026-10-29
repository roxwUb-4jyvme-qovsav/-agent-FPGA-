"""Map internal v2.x artifacts to the official JSONL tool-call contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def write_official_trace(work: Path, output: Path, result: dict,
                         model_name: str, vivado_version: str) -> list[dict]:
    """Emit call-level records without copying prompt, code or secret values."""
    if output.is_file() and output.stat().st_size:
        live = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(row.get("accounting") == "write_ahead" for row in live):
            return live
    records: list[dict] = [{"ts": round(time.time(), 3), "tool": "agent_meta",
                            "agent_version": result.get("agent_version"),
                            "model": model_name, "vivado_version": vivado_version,
                            "public_spec_only": True}]
    for round_dir in sorted(work.glob("round_*")):
        if not round_dir.is_dir():
            continue
        try:
            round_index = int(round_dir.name.split("_")[-1])
        except ValueError:
            continue
        token_req = _read(round_dir / "tokenizer" / "tokenize_request.json")
        token_resp = _read(round_dir / "tokenizer" / "tokenize_response.json")
        if token_req is not None:
            records.append({"tool": "tokenizer", "round": round_index,
                            "prompt_sha256": _sha(round_dir / "prompt.txt"),
                            "tokens_in": token_resp.get("count") if token_resp else None,
                            "context_limit": token_resp.get("max_model_len") if token_resp else None,
                            "status": "ok" if token_resp else "error"})
        request = _read(round_dir / "model_request.json")
        response = _read(round_dir / "model_response.json")
        if request is not None:
            usage = response.get("usage", {}) if response else {}
            choices = response.get("choices", []) if response else []
            records.append({"tool": "llm", "round": round_index,
                            "model": request.get("model"), "request_max_tokens": request.get("max_tokens"),
                            "tokens_in": usage.get("prompt_tokens"),
                            "tokens_out": usage.get("completion_tokens"),
                            "finish": choices[0].get("finish_reason") if choices else None,
                            "status": "ok" if response else "error"})
        interface = _read(round_dir / "interface_result.json") or _read(round_dir / "interface_check.json")
        if interface:
            records.append({"tool": "check_interface", "round": round_index,
                            "status": interface.get("status"), "issues": interface.get("issues", []),
                            "advisories": interface.get("advisories", []),
                            "public_spec_only": True})
        static = _read(round_dir / "static_result.json") or _read(round_dir / "candidate_validation.json")
        if static:
            records.append({"tool": "static_check", "round": round_index,
                            "status": static.get("status"), "problems": static.get("problems", [])})
        eda_root = work / "eda" / round_dir.name
        compile_result = _read(eda_root / "compile" / "compile_result.json")
        if compile_result:
            for internal, external in (("analyze", "xvlog"), ("elaborate", "xelab")):
                stage = compile_result.get("stages", {}).get(internal)
                if stage:
                    records.append({"tool": external, "round": round_index,
                                    "status": stage.get("status"), "sec": stage.get("elapsed_s"),
                                    "log_sha256": stage.get("log_sha256"),
                                    "diagnostics": stage.get("diagnostics", "")[:1000]})
        selftest = _read(eda_root / "selftest" / "selftest_result.json")
        if selftest:
            for stage in ("xvlog", "xelab", "xsim"):
                info = selftest.get("stages", {}).get(stage)
                if info:
                    records.append({"tool": stage, "purpose": "team_generated_selftest",
                                    "round": round_index, "rc": info.get("returncode"),
                                    "sec": info.get("elapsed_s"),
                                    "log_sha256": info.get("log_sha256")})
            records.append({"tool": "selftest", "round": round_index,
                            "status": selftest.get("status"),
                            "requirement_sha256": selftest.get("requirement_sha256"),
                            "testbench_sha256": selftest.get("testbench_sha256"),
                            "candidate_sha256": selftest.get("candidate_sha256"),
                            "not_official_oracle": True})
        synth = _read(eda_root / "synth" / "synth_result.json")
        if synth:
            records.append({"tool": "synth_design", "round": round_index,
                            "status": synth.get("status"), "sec": synth.get("elapsed_s"),
                            "log_sha256": synth.get("log_sha256")})
        diagnosis = _read(round_dir / "targeted_diagnostic.json")
        if diagnosis:
            records.append({"tool": "repair_diagnostic", "round": round_index,
                            "stage": diagnosis.get("stage"),
                            "category": diagnosis.get("category"),
                            "signature": diagnosis.get("signature"),
                            "goal": diagnosis.get("repair_goal"),
                            "log_lines_forwarded": diagnosis.get("log_lines_forwarded")})
        record = _read(round_dir / "round_result.json")
        if record is None:
            for row in result.get("rounds", []):
                if row.get("round") == round_index:
                    record = row
                    break
        if record:
            if record.get("repair_goal") or (round_index and record.get("targeted_diagnostic")):
                records.append({"tool": "repair", "round": round_index,
                                "goal_sha256": hashlib.sha256(str(record.get("repair_goal", "")).encode()).hexdigest(),
                                "status": record.get("status")})
            records.append({"tool": "round_stop", "round": round_index,
                            "status": record.get("status"), "candidate_sha256": record.get("candidate_sha256"),
                            "sec": record.get("wall_elapsed_s")})
    records.append({"tool": "agent_stop", "status": result.get("status"),
                    "reason": result.get("stop_reason"), "sec": result.get("elapsed_s"),
                    "usage": result.get("usage", {}), "costs": result.get("costs", {}),
                    "official_level": result.get("official_level", {}).get("level")})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return records
