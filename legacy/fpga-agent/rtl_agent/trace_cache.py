"""Bounded, double-buffered cache of confirmed durable call events.

The request owner maps this before launching children. Returning a timeout only
copies resident bounded bytes; it never opens or scans the trace at that point.
Disk trace is authoritative; cache overflow is explicit and usage is cumulative.
"""
import json
import mmap
import os
from pathlib import Path

SLOT = 65536
SIZE = 1 + 2 * SLOT
CACHES = {}


class DeliveryState:
    """One-byte OS-coherent delivery register; no fsync after deadline.

    P=pending, S=sent, T=socket deadline, F=delivery failure. Detailed durable
    JSON is best-effort within remaining budget. This register is evidence of
    process-observed delivery, not a guarantee against sudden power loss.
    """
    def __init__(self, path):
        with Path(path).open("wb") as stream:
            stream.write(b"P")
            stream.flush()
            os.fsync(stream.fileno())
        self.stream = Path(path).open("r+b")
        self.map = mmap.mmap(self.stream.fileno(), 1)

    def mark(self, value):
        self.map[0] = ord(value)

    def close(self):
        self.map.close()
        self.stream.close()


class TraceCache:
    def __init__(self, path, create=False):
        self.path = Path(path)
        if create:
            with self.path.open("wb") as stream:
                stream.truncate(SIZE)
        self.stream = self.path.open("r+b")
        self.map = mmap.mmap(self.stream.fileno(), SIZE)
        if create:
            self.publish([], None, 0, False)

    def raw(self):
        # Single-byte selector publishes a completed inactive buffer. A reader
        # retries once if a concurrent writer changes the selector.
        for _ in range(2):
            slot = self.map[0] & 1
            start = 1 + slot * SLOT
            size = int.from_bytes(self.map[start:start+4], "big")
            raw = self.map[start+4:start+4+min(size, SLOT-4)]
            if self.map[0] & 1 == slot:
                return raw
        return b'{"trace":"","trace_incomplete":true,"missing_scope":"concurrent_cache_update","known_usage":null}'

    def publish(self, rows, usage, unknown, incomplete, dropped=0):
        value = {"trace": "".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows),
                 "known_usage": usage, "unknown_usage_calls": unknown,
                 "trace_incomplete": incomplete,
                 "dropped_rows": dropped,
                 "cost_complete": usage is not None and unknown == 0 and not incomplete,
                 "missing_scope": "cache_overflow_or_unreported_writes" if incomplete else None}
        raw = json.dumps(value, ensure_ascii=False).encode()
        while len(raw) > SLOT-4 and rows:
            rows.pop(0)
            dropped += 1
            value.update(trace="".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows),
                         trace_incomplete=True, missing_scope="cache_overflow", dropped_rows=dropped, cost_complete=False)
            raw = json.dumps(value, ensure_ascii=False).encode()
        if len(raw) > SLOT-4:
            raise ValueError("cache metadata exceeds fixed capacity")
        slot = 1 - (self.map[0] & 1)
        start = 1 + slot * SLOT
        self.map[start:start+4+len(raw)] = len(raw).to_bytes(4, "big") + raw
        self.map[0] = slot

    def add(self, row):
        value = json.loads(self.raw())
        rows = [json.loads(line) for line in value["trace"].splitlines()]
        usage = value.get("known_usage")
        unknown = value.get("unknown_usage_calls", 0)
        if row.get("tool") == "llm":
            if row.get("phase") == "start":
                unknown += 1
            elif row.get("phase") == "response" and all(type(row.get(k)) is int and row[k] >= 0 for k in ("tokens_in", "tokens_out")):
                usage = usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                usage["prompt_tokens"] += row["tokens_in"]
                usage["completion_tokens"] += row["tokens_out"]
                usage["total_tokens"] += row["tokens_in"] + row["tokens_out"]
                unknown = max(0, unknown-1)
        rows.append(row)
        self.publish(rows, usage, unknown, value.get("trace_incomplete", False), value.get("dropped_rows", 0))

    def close(self):
        self.map.close()
        self.stream.close()


def confirmed_event(path, row):
    direct = CACHES.get(str(Path(path).resolve()))
    if direct:
        direct.add(row)
        return
    cache = os.environ.get("AGENT_TRACE_CACHE")
    trace = os.environ.get("AGENT_TRACE_FILE")
    if cache and trace and Path(path).resolve() == Path(trace).resolve():
        mapped = TraceCache(cache)
        try:
            mapped.add(row)
        finally:
            mapped.close()
