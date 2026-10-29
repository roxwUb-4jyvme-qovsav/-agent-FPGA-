"""Local OpenAI-compatible inference client and baseline experiment harness."""
from __future__ import annotations

import csv
import hashlib
import http.client
import json
import math
from pathlib import Path
import re
import socket
import threading
import time
from urllib import parse
import uuid

from .core import evaluate, load_task, write_json


DEFAULT_CONFIG = Path("configs/qwen25_coder_7b_q4km.json")
DEFAULT_SUITE = Path("benchmarks/verilogeval_v2/quick20/suite.json")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ProtocolError(ValueError):
    pass


class GenerationError(ValueError):
    pass


class RequestTimeout(ConnectionError):
    """A local inference HTTP request exceeded its explicit deadline."""


def local_url(url: str):
    parsed = parse.urlparse(url)
    if (parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("URL must be an unauthenticated local HTTP endpoint without query or fragment")
    _ = parsed.port  # Validate the port before opening a connection.
    return parsed
FORBIDDEN_RTL = (
    (re.compile(r"(?im)^\s*`\s*include\b"), "preprocessor include"),
    (re.compile(r"(?i)\b(?:import|export)\s+\"DPI"), "DPI declaration"),
    (re.compile(r"(?im)^\s*bind\b"), "bind statement"),
    (re.compile(r"(?i)\$(?:fopen|fclose|fwrite|fdisplay|system|finish|stop|fatal|error|warning|readmem[hb]|writemem[hb]|dump\w*|monitor)\b"),
     "unsafe system task"),
    (re.compile(r"(?i)\b(?:tb|good1|stats1)\s*\."), "evaluator hierarchy reference"),
)


def load_model_config(path: Path) -> dict:
    config = json.loads(path.resolve().read_text(encoding="utf-8-sig"))
    for key in ("model", "base_url"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"Model config {key} must be a non-empty string")
    parsed = local_url(config["base_url"])
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS or parsed.username or parsed.password:
        raise ValueError("base_url must be an unauthenticated local HTTP endpoint")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query or fragment data")
    config["base_url"] = config["base_url"].rstrip("/")
    for key, default in (("temperature", 0.0), ("top_p", 1.0), ("max_tokens", 1024),
                         ("seed", 20260917), ("timeout_s", 180)):
        config.setdefault(key, default)
    for key in ("temperature", "top_p", "timeout_s"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Model config {key} must be finite and non-negative")
    if config["timeout_s"] <= 0 or not 0 < config["top_p"] <= 1:
        raise ValueError("timeout_s must be positive and top_p must be in (0, 1]")
    config.setdefault("max_response_bytes", 2 * 1024 * 1024)
    for key in ("max_tokens", "seed", "max_response_bytes"):
        if isinstance(config[key], bool) or not isinstance(config[key], int):
            raise ValueError(f"Model config {key} must be an integer")
    if config["max_tokens"] <= 0:
        raise ValueError("max_tokens must be positive")
    if not 1 <= config["max_response_bytes"] <= 16 * 1024 * 1024:
        raise ValueError("max_response_bytes must be between 1 byte and 16 MiB")
    if config.get("prompt_mode", "public_spec_only") != "public_spec_only":
        raise ValueError("Baseline prompt_mode must be public_spec_only")
    return config


def _json_request(url: str, timeout: float, payload: dict | None = None,
                  max_bytes: int = 2 * 1024 * 1024, trace: Path | None = None) -> dict:
    parsed = local_url(url)
    # Direct numeric loopback connection: no DNS, environment proxy or redirects.
    host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
    connection = http.client.HTTPConnection(host, parsed.port or 80, timeout=timeout)
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    raw = bytearray()
    timer = None
    started = time.monotonic()
    expired = threading.Event()
    try:
        connection.connect()
        connected_socket = connection.sock

        def expire():
            expired.set()
            try:
                connected_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("request total deadline exceeded")
        timer = threading.Timer(remaining, expire)
        timer.daemon = True
        timer.start()
        connection.request("GET" if body is None else "POST", parsed.path or "/", body, headers)
        response = connection.getresponse()
        if trace:
            write_json(trace / "http_response.json", {"status": response.status,
                       "headers": dict(response.getheaders())})
        with response:
            while True:
                chunk = response.read1(min(65536, max_bytes + 1 - len(raw)))
                raw.extend(chunk)
                if expired.is_set() or time.monotonic() - started >= timeout:
                    raise TimeoutError("request total deadline exceeded")
                if len(raw) > max_bytes:
                    raise ProtocolError("Local model response exceeds max_response_bytes")
                if not chunk:
                    break
        if response.status != 200:
            raise ProtocolError(f"Local model HTTP status {response.status}; redirects are disabled")
    except (http.client.HTTPException, TimeoutError, OSError) as exc:
        message = f"Local model service request failed: {exc}"
        if expired.is_set() or isinstance(exc, (TimeoutError, socket.timeout)):
            raise RequestTimeout(message) from exc
        raise ConnectionError(message) from exc
    finally:
        if timer:
            timer.cancel()
        connection.close()
        if trace:
            (trace / "model_response.raw").write_bytes(raw[:max_bytes])
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Local model service returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Local model service response must be a JSON object")
    return value


def check_model_service(config: dict) -> dict:
    started = time.monotonic()
    response = _json_request(config["base_url"] + "/models", config["timeout_s"],
                             max_bytes=config.get("max_response_bytes", 2 * 1024 * 1024))
    if not isinstance(response.get("data"), list):
        raise ProtocolError("Model list must contain a data array")
    ids = [item.get("id") for item in response["data"] if isinstance(item, dict)]
    return {
        "status": "READY" if config["model"] in ids else "NOT_READY",
        "configured_model": config["model"],
        "available_models": ids,
        "base_url": config["base_url"],
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def tokenize_chat(config: dict, messages: list[dict], trace: Path | None = None,
                  timeout_s: float | None = None) -> dict:
    """Count the exact chat-template tokens with the serving vLLM instance."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    parsed = local_url(config["base_url"])
    endpoint = parse.urlunparse((parsed.scheme, parsed.netloc, "/tokenize", "", "", ""))
    payload = {
        "model": config["model"],
        "messages": messages,
        "add_generation_prompt": True,
        "continue_final_message": False,
        "add_special_tokens": False,
    }
    from .serving_contract import generation_fields
    payload.update(generation_fields(config.get("generation")))
    started = time.monotonic()
    if trace:
        trace.mkdir(parents=True, exist_ok=True)
        write_json(trace / "tokenize_request.json", payload)
    response = _json_request(
        endpoint,
        float(config["timeout_s"] if timeout_s is None else timeout_s),
        payload,
        config.get("max_response_bytes", 2 * 1024 * 1024),
        trace,
    )
    if trace:
        write_json(trace / "tokenize_response.json", response)
    count = response.get("count")
    max_model_len = response.get("max_model_len")
    tokens = response.get("tokens")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ProtocolError("Tokenizer response count must be a non-negative integer")
    if isinstance(max_model_len, bool) or not isinstance(max_model_len, int) or max_model_len <= 0:
        raise ProtocolError("Tokenizer response max_model_len must be a positive integer")
    if not isinstance(tokens, list) or len(tokens) != count or any(
            isinstance(token, bool) or not isinstance(token, int) or token < 0 for token in tokens):
        raise ProtocolError("Tokenizer response tokens must match count")
    return {
        "request": payload,
        "response": response,
        "count": count,
        "max_model_len": max_model_len,
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def complete_spec(config: dict, spec: str, trace: Path | None = None,
                  require_stop: bool = True) -> dict:
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("Public task specification must be non-empty")
    # The baseline intentionally sends the public specification as its only message.
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": spec}],
        "temperature": config["temperature"],
        "top_p": config["top_p"],
        "max_tokens": config["max_tokens"],
        "seed": config["seed"],
        "stream": False,
    }
    from .serving_contract import generation_fields
    payload.update(generation_fields(config.get("generation")))
    started = time.monotonic()
    if trace:
        write_json(trace / "model_request.json", payload)
    response = _json_request(config["base_url"] + "/chat/completions", config["timeout_s"], payload,
                             config.get("max_response_bytes", 2 * 1024 * 1024), trace)
    from .runtime_audit import account_response
    account_response(response)
    if trace:
        write_json(trace / "model_response.json", response)
    if response.get("model") != config["model"]:
        raise ProtocolError("Response model does not match configured model alias")
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProtocolError("Local model response has no choices[0].message.content") from exc
    if trace and isinstance(content, str):
        (trace / "model_output.txt").write_text(content, encoding="utf-8")
    finish_reason = response["choices"][0].get("finish_reason")
    if require_stop and finish_reason != "stop":
        raise GenerationError("Model output incomplete or unsupported finish_reason")
    if not isinstance(finish_reason, str) or not finish_reason:
        raise ProtocolError("Local model response has no finish_reason")
    if require_stop and (not isinstance(content, str) or not content.strip()):
        raise GenerationError("Local model returned empty content")
    return {"request": payload, "response": response, "content": content,
            "finish_reason": finish_reason,
            "elapsed_s": round(time.monotonic() - started, 3)}


def rtl_tokens(source: str) -> str:
    """Mask comments and strings without changing offsets; EDA remains the parser."""
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                  lambda m: re.sub(r"[^\n]", " ", m.group()), source, flags=re.DOTALL)


def _is_auxiliary_testbench(source: str, top_module: str) -> bool:
    """Identify a separate example testbench without parsing or rewriting the DUT."""
    tokens = rtl_tokens(source)
    declarations = re.findall(
        r"\bmodule\s+(?:(?:automatic|static)\s+)?([A-Za-z_][\w$]*)", tokens
    )
    if top_module in declarations:
        return False
    unsafe = any(pattern.search(source) for pattern, _ in FORBIDDEN_RTL)
    testbench_name = any(
        re.match(r"(?i)^(?:tb|testbench)(?:_|$)", name) or re.search(r"(?i)(?:_|^)tb$", name)
        for name in declarations
    )
    instantiates_top = re.search(
        rf"\b{re.escape(top_module)}\b\s*(?:#\s*\([^;]*?\)\s*)?[A-Za-z_][\w$]*\s*\(",
        tokens,
        re.DOTALL,
    )
    return bool(unsafe or (testbench_name and instantiates_top))


def extract_candidate(text: str, top_module: str = "TopModule") -> str:
    if not isinstance(text, str) or not text.strip():
        raise GenerationError("Model output is empty")
    if "```" in text:
        blocks = list(re.finditer(r"```([^\r\n`]*)\r?\n(.*?)```", text, re.DOTALL))
        remainder = re.sub(r"```([^\r\n`]*)\r?\n(.*?)```", "", text, flags=re.DOTALL)
        if not blocks or "```" in remainder:
            raise GenerationError("Unclosed or malformed code fence")
        if any(m.group(1).strip().lower() not in ("", "sv", "verilog", "systemverilog") for m in blocks):
            raise GenerationError("Unsupported code fence language")
        remainder_tokens = rtl_tokens(remainder)
        outside_declaration = re.search(
            r"(?m)^\s*module\s+(?:(?:automatic|static)\s+)?[A-Za-z_][\w$]*\s*(?:#\s*\(|\(|;)",
            remainder_tokens,
        )
        outside_endmodule = re.search(r"(?m)^\s*endmodule\b", remainder_tokens)
        if outside_declaration or outside_endmodule:
            raise GenerationError("RTL appears outside code fences; output is ambiguous")
        fenced_sources = [m.group(2) for m in blocks]
        top_blocks = [source for source in fenced_sources if re.search(
            rf"\bmodule\s+(?:(?:automatic|static)\s+)?{re.escape(top_module)}\b",
            rtl_tokens(source),
        )]
        if len(top_blocks) == 1:
            # Models sometimes append a second fenced example testbench. It is not
            # part of the requested DUT and may contain otherwise forbidden tasks.
            # Keep ordinary secondary blocks so split helper modules still work.
            fenced_sources = [source for source in fenced_sources
                              if source == top_blocks[0] or
                              not _is_auxiliary_testbench(source, top_module)]
        source = "\n".join(fenced_sources)
    else:
        source = text  # Preserve directives, packages and helpers; never slice RTL silently.
    tokens = rtl_tokens(source)
    declarations = re.findall(r"\bmodule\s+(?:(?:automatic|static)\s+)?([A-Za-z_][\w$]*)", tokens)
    if declarations.count(top_module) != 1:
        raise GenerationError(f"Expected exactly one module {top_module} declaration")
    opened = False
    for token in re.findall(r"\b(?:module|endmodule)\b", tokens):
        if (token == "module") == opened:
            raise GenerationError("Unbalanced or nested module declarations")
        opened = token == "module"
    if opened:
        raise GenerationError("Generated module has no endmodule")
    return source.strip() + "\n"


def validate_candidate(source: str, top_module: str = "TopModule") -> dict:
    problems = []
    encoded = source.encode("utf-8")
    if len(encoded) > 256 * 1024:
        problems.append("candidate exceeds 256 KiB")
    if "\x00" in source or "\ufffd" in source:
        problems.append("candidate contains invalid text markers")
    count = len(re.findall(rf"\bmodule\s+(?:(?:automatic|static)\s+)?{re.escape(top_module)}\b", rtl_tokens(source)))
    if count != 1:
        problems.append(f"expected one {top_module} declaration, found {count}")
    for pattern, label in FORBIDDEN_RTL:
        if pattern.search(source):
            problems.append(label)
    return {"status": "PASS" if not problems else "REJECTED", "problems": problems,
            "bytes": len(encoded), "top_module": top_module}


def run_baseline_case(task_path: Path, model_config: dict, output: Path,
                      vivado_bin: str | None = None, generate_only: bool = False) -> dict:
    work = output.resolve() / ("baseline_" + uuid.uuid4().hex[:12])
    work.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": 1, "task_id": task_path.stem, "mode": "baseline",
              "work_dir": str(work), "status": "NOT_RUN"}
    started = time.monotonic()
    phase = "task"
    try:
        write_json(work / "model_config.json", model_config)
        task = load_task(task_path, require_rtl=False)
        result["task_id"] = task["task_id"]
        write_json(work / "source_task.json", task)
        phase = "model"
        completion = complete_spec(model_config, task["spec"], work)
        result["usage"] = completion["response"].get("usage")
        write_json(work / "model_request.json", completion["request"])
        write_json(work / "model_response.json", completion["response"])
        (work / "model_output.txt").write_text(completion["content"], encoding="utf-8")
        phase = "generation"
        candidate = extract_candidate(completion["content"], task["top_module"])
        validation = validate_candidate(candidate, task["top_module"])
        write_json(work / "candidate_validation.json", validation)
        if validation["status"] != "PASS":
            result.update(status="GENERATION_ERROR", reason="Candidate policy rejected model output",
                          failure_stage="generation", candidate_validation=validation,
                          generation_elapsed_s=completion["elapsed_s"])
        else:
            candidate_path = work / "candidate.sv"
            candidate_path.write_text(candidate, encoding="utf-8")
            eval_task = {
                "task_id": task["task_id"], "spec": task["spec"], "top_module": task["top_module"],
                "tb_top": task["tb_top"], "clock_port": task.get("clock_port"),
                "rtl_files": [str(candidate_path)], "testbench_files": task["testbench_files"],
                "timeout_s": task["timeout_s"], "stage_timeouts_s": task["stage_timeouts_s"],
                "source_task": task["manifest"],
            }
            eval_manifest = work / "eval_task.json"
            write_json(eval_manifest, eval_task)
            result["generation_elapsed_s"] = completion["elapsed_s"]
            result["candidate"] = str(candidate_path)
            if generate_only:
                result.update(status="GENERATED", reason="Candidate generated and validated; EDA not requested")
            else:
                phase = "eda"
                eda = evaluate(eval_manifest, work / "eda", vivado_bin)
                result.update(status=eda["status"], eda_result=str(Path(eda["work_dir"]) / "result.json"),
                              eda_work_dir=eda["work_dir"])
                failed = next((name for name, stage in eda["stages"].items()
                               if stage["status"] not in ("PASS", "NOT_RUN")), "")
                result["failure_stage"] = failed
                result["reason"] = eda["stages"][failed]["reason"] if failed else eda.get("reason", "")
    except GenerationError as exc:
        result.update(status="GENERATION_ERROR", reason=str(exc), failure_stage="generation")
    except ProtocolError as exc:
        result.update(status="PROTOCOL_ERROR", reason=str(exc), failure_stage="model")
    except ConnectionError as exc:
        result.update(status="MODEL_ERROR", reason=str(exc), failure_stage=phase)
    except OSError as exc:
        result.update(status="TASK_ERROR" if phase == "task" else "INFRA_ERROR", reason=str(exc), failure_stage=phase)
    except (ValueError, KeyError, TypeError) as exc:
        status = {"task": "TASK_ERROR", "model": "PROTOCOL_ERROR", "generation": "GENERATION_ERROR",
                  "eda": "INFRA_ERROR"}[phase]
        result.update(status=status, reason=str(exc), failure_stage=phase)
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    write_json(work / "baseline_result.json", result)
    return result


def run_baseline_suite(suite_path: Path, config_path: Path, output: Path,
                       vivado_bin: str | None = None, limit: int | None = None,
                       generate_only: bool = False, resume: Path | None = None) -> list[dict]:
    config = load_model_config(config_path)
    suite_path = suite_path.resolve()
    suite = json.loads(suite_path.read_text(encoding="utf-8-sig"))
    if not isinstance(suite, list) or not suite:
        raise ValueError("Baseline suite must be a non-empty JSON list")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise ValueError("limit must be a positive integer")
    cases = suite if limit is None else suite[:limit]
    snapshot = {"config": config, "suite_path": str(suite_path), "cases": cases,
                "generate_only": generate_only, "vivado_bin": vivado_bin, "inputs": {},
                "code": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (Path(__file__), Path(__file__).with_name("core.py"))}}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("task"), str):
            raise ValueError("Each suite entry must contain a task path")
        path = (suite_path.parent / case["task"]).resolve()
        snapshot["inputs"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        try:
            task = load_task(path, require_rtl=False)
        except (ValueError, OSError, KeyError, TypeError):
            continue  # A bad task becomes a recorded TASK_ERROR rather than losing the suite.
        for source in task["testbench_files"]:
            snapshot["inputs"][source] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    if resume:
        run = resume.resolve()
        if json.loads((run / "suite_snapshot.json").read_text(encoding="utf-8")) != snapshot:
            raise ValueError("Resume refused: configuration, source, evaluator or code changed")
    else:
        run = output.resolve() / ("suite_" + uuid.uuid4().hex[:12])
        run.mkdir(parents=True, exist_ok=False)
        atomic_json(run / "suite_snapshot.json", snapshot)
    print(f"Baseline run: {run}", flush=True)
    rows = []
    for index, case in enumerate(cases):
        checkpoint = run / f"case_{index:04d}.json"
        if checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            for name, expected in saved["artifacts"].items():
                artifact = Path(name)
                if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
                    raise ValueError(f"Resume refused: completed artifact missing or changed: {name}")
            result = saved["result"]
        else:
            result = run_baseline_case(suite_path.parent / case["task"], config, run, vivado_bin, generate_only)
            work = Path(result["work_dir"])
            artifacts = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in work.iterdir() if p.is_file()}
            if result.get("eda_result"):
                p = Path(result["eda_result"])
                artifacts[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
            atomic_json(checkpoint, {"result": result, "artifacts": artifacts})
        rows.append({key: result.get(key, "") for key in
                     ("task_id", "status", "failure_stage", "reason", "generation_elapsed_s", "elapsed_s", "work_dir", "eda_result")})
        atomic_json(run / "baseline_summary.json", rows)
        csv_path = run / "baseline_summary.csv"
        temporary = csv_path.with_suffix(".csv.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(csv_path)
        print(f"{result['task_id']}: {result['status']} -> {result['work_dir']}", flush=True)
    return rows


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_json(temporary, value)
    temporary.replace(path)
