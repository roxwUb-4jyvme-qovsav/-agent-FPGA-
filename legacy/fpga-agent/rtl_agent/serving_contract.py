"""Organizer baseline admission and shared request configuration; no generation."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SHA256 = "17aaa609d4dbd36a94d1390e39f769394448f8588f3ccdd2bd37db9e34ed05e2"
BASELINE_MAX_TOKENS = 16384
BASELINE_TIMEOUT_S = 840
REQUEST_DEADLINE_S = 900


def official_baseline():
    """Load only the hash-verified organizer module; never invoke its main()."""
    source = ROOT / "baseline.py"
    if hashlib.sha256(source.read_bytes()).hexdigest() != BASELINE_SHA256:
        raise ValueError("Official baseline SHA256 mismatch")
    spec = importlib.util.spec_from_file_location("_rtl_frozen_official_baseline", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.MAX_TOKENS != BASELINE_MAX_TOKENS or module.TIMEOUT_S != BASELINE_TIMEOUT_S:
        raise ValueError("Official baseline budget mismatch")
    return module


def generation_fields(value):
    """Exactly the two organizer-recognized keys, without model-family defaults."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("generation must be an object")
    result = {}
    kwargs = value.get("chat_template_kwargs")
    if isinstance(kwargs, dict):
        kwargs = {k: v for k, v in kwargs.items()
                  if isinstance(k, str) and isinstance(v, (bool, int, float, str))}
        if any(isinstance(v, float) and not math.isfinite(v) for v in kwargs.values()):
            raise ValueError("generation values must be finite")
        if kwargs:
            result["chat_template_kwargs"] = kwargs
    effort = value.get("reasoning_effort")
    if isinstance(effort, str) and effort:
        result["reasoning_effort"] = effort
    return result


def load_generation():
    # Reuse the unchanged organizer parser, rather than a second interpretation.
    return generation_fields(official_baseline().load_generation())


def baseline_messages(prompt, interface=""):
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(interface, str):
        raise ValueError("Public prompt and interface must be strings; prompt must be nonempty")
    text = prompt + ("\n\nInterface:\n" + interface if interface else "")
    return [{"role": "system", "content": official_baseline().SYS["rtl"]},
            {"role": "user", "content": text}]


def capacity_result(prompt_tokens, actual_context, declared_context):
    """Pure arithmetic only. Unknown measurements never become a PASS."""
    result = {"status": "NOT_VERIFIED", "prompt_tokens": prompt_tokens,
              "request_max_tokens": BASELINE_MAX_TOKENS,
              "actual_context": actual_context, "declared_context": declared_context,
              "required_context": None, "reason": "TOKENIZER_OR_CONTEXT_UNKNOWN",
              "scope": "REQUEST_CAPACITY_ONLY_NOT_MODEL_GPU_EDA_VALIDATION"}
    if any(type(v) is not int for v in (prompt_tokens, actual_context, declared_context)):
        return result
    if prompt_tokens < 0 or actual_context <= 0 or declared_context <= 0:
        return result
    result["required_context"] = prompt_tokens + BASELINE_MAX_TOKENS
    if actual_context != declared_context:
        result.update(status="BLOCKED", reason="MODEL_CONTEXT_MISMATCH")
    elif result["required_context"] > actual_context:
        result.update(status="BLOCKED", reason="CONTEXT_LIMIT_EXCEEDED")
    else:
        result.update(status="PASS", reason="BASELINE_REQUEST_FITS_ACTUAL_CONTEXT")
    return result


def check_baseline_admission(config, prompt, interface="", trace=None, timeout_s=None):
    """Tokenize exact public baseline messages, never call chat/completions/EDA."""
    from .local_model import tokenize_chat
    generation = load_generation()
    if generation_fields(config.get("generation")) != generation:
        raise ValueError("Agent and baseline generation settings differ")
    tokenized = tokenize_chat(config, baseline_messages(prompt, interface), trace, timeout_s)
    result = capacity_result(tokenized.get("count"), tokenized.get("max_model_len"),
                             config.get("context_tokens"))
    result.update(model=config["model"], base_url=config["base_url"], generation=generation,
                  baseline_sha256=BASELINE_SHA256,
                  messages_sha256=hashlib.sha256(json.dumps(baseline_messages(prompt, interface),
                                      ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
    if result["status"] != "PASS":
        raise ValueError(result["reason"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tokenize only; no generation or EDA. Requires an authorized local service.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise FileExistsError("Refusing to overwrite admission evidence")
    from agent.main import _model_config
    prompt = (args.input / "prompt.txt").read_text(encoding="utf-8")
    iface = args.input / "interface.txt"
    report = check_baseline_admission(_model_config(), prompt,
                                     iface.read_text(encoding="utf-8") if iface.is_file() else "")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes((json.dumps(report, indent=2) + "\n").encode())
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
