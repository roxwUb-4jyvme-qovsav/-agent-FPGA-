"""Prepare artifacts in a killable child; never commit the request authority."""
import hashlib
import json
from pathlib import Path
import sys
import time

from rtl_agent.runtime_audit import append_event, durable_json
from rtl_agent.trace_cache import TraceCache


def _read_limited(path, limit):
    if not path.is_file():
        return ""
    if path.stat().st_size > limit:
        raise ValueError("Response artifact exceeds limit")
    return path.read_text(encoding="utf-8")


def finalize(job, evidence, output, absolute):
    ledger_cost = None
    # Obtain independently readable durable cost before a potentially stalled
    # trace read. This also covers direct/frozen baseline-style trace writers.
    # A missing/unreadable ledger leaves UNKNOWN, never fabricated zero usage.
    if job.get("cache"):
        cache = TraceCache(job["cache"])
        try:
            for ledger_path in [output / "budget_ledger.json", *output.glob("agent_v24_*/budget_ledger.json")]:
                try:
                    ledger = json.loads(_read_limited(ledger_path, 65536))
                    cost = ledger.get("usage")
                    if (isinstance(cost, dict) and all(type(cost.get(k)) is int and cost[k] >= 0
                            for k in ("prompt_tokens", "completion_tokens", "total_tokens")) and
                            cost["total_tokens"] == cost["prompt_tokens"] + cost["completion_tokens"]):
                        prior = json.loads(cache.raw())
                        rows = [json.loads(line) for line in prior["trace"].splitlines()]
                        cache.publish(rows, cost, ledger.get("unknown_usage_calls", prior.get("unknown_usage_calls", 0)), True)
                        ledger_cost = cost
                        break
                except (OSError, ValueError, TypeError):
                    continue
        finally:
            cache.close()
    append_event(output / "trace.jsonl", {"tool": "request_finalization", "phase": "preparing",
                 "request_status_source": "../audit_index.json"})
    # Publish trace before hashing, so a later stalled hash cannot hide known
    # calls. Parse the bounded trace only in this cancellable child.
    trace = _read_limited(output / "trace.jsonl", 16 * 1024 * 1024)
    rows = [json.loads(line) for line in trace.splitlines()]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    unknown = 0
    for row in rows:
        if row.get("tool") == "llm":
            if row.get("phase") == "start": unknown += 1
            if all(type(row.get(k)) is int and row[k] >= 0 for k in ("tokens_in", "tokens_out")):
                usage["prompt_tokens"] += row["tokens_in"]
                usage["completion_tokens"] += row["tokens_out"]
                usage["total_tokens"] += row["tokens_in"] + row["tokens_out"]
                unknown = max(0, unknown-1)
            elif row.get("phase") not in ("start", "response"):
                unknown += 1
    accounting_complete = unknown == 0
    if ledger_cost and ledger_cost["total_tokens"] > usage["total_tokens"]:
        usage = ledger_cost
        accounting_complete = False
    if job.get("cache"):
        cache = TraceCache(job["cache"])
        try: cache.publish(rows[:], usage, unknown, False)
        finally: cache.close()
    files = {}
    retention = []
    for path in sorted(evidence.rglob("*")):
        if path.name in {"audit_index.json", "finalization_prepared.json", "http_delivery.json", "http_delivery.state", "trace.cache"} or path.name.endswith(".partial"):
            continue
        if path.is_file() and not path.is_symlink():
            if time.monotonic() >= absolute:
                raise TimeoutError("WALLCLOCK_TIMEOUT")
            files[path.relative_to(evidence).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name == "eda_evidence_manifest.json":
                retained = json.loads(_read_limited(path, 65536))
                retention.append({"manifest": path.relative_to(evidence).as_posix(), "status": retained.get("status", "INCOMPLETE")})
    if not retention and (output / "eda_retention_request.json").is_file():
        retention.append({"manifest": "output/eda_retention_request.json", "status": "INCOMPLETE"})
    archive_incomplete = any(item["status"] != "COMPLETE" for item in retention)
    solution = _read_limited(output / "solution.v", 4 * 1024 * 1024)
    body = {"solution": solution, "trace": trace, "known_usage": usage,
            "unknown_usage_calls": unknown, "trace_incomplete": False, "missing_scope": None,
            "cost_complete": accounting_complete, "retention_manifests": retention,
            "retention_state": "INCOMPLETE" if archive_incomplete else "COMPLETE" if retention else "NOT_APPLICABLE"}
    wire = json.dumps(body, ensure_ascii=False).encode()
    if time.monotonic() >= absolute:
        raise TimeoutError("WALLCLOCK_TIMEOUT")
    durable_json(evidence / "finalization_prepared.json", {
        "phase": "PREPARED", "execution_result": job["status"], "sha256": files,
        "archive_incomplete": archive_incomplete, "request_status_source": "audit_index.json",
        "wire_sha256": hashlib.sha256(wire).hexdigest()})
    sys.stdout.buffer.write(b"PREPARED " + (b"1\n" if archive_incomplete else b"0\n") + wire)
    sys.stdout.buffer.flush()
    return 0


def main():
    job = json.load(sys.stdin)
    evidence = Path(job["evidence"])
    return finalize(job, evidence, evidence / "output", job["absolute"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stdout.write("ERR " + type(exc).__name__ + "\n")
        raise SystemExit(2)
