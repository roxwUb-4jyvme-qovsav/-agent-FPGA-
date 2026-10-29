"""Only owner-issued decisions can write the request authority.

Storage and authority resolution are supervised subprocess operations. Lost
confirmation is UNKNOWN, never an invented opposite terminal outcome.
"""
import json
from pathlib import Path
import sys
import time

from rtl_agent.runtime_audit import durable_json


def main():
    job = json.load(sys.stdin)
    evidence = Path(job["evidence"])
    action = job["action"]
    if action == "commit":
        value = job["value"]
        if not value["finalization_incomplete"]:
            proposal = json.loads((evidence / "finalization_prepared.json").read_text())
            if proposal["phase"] != "PREPARED":
                raise ValueError("Not prepared")
            value["sha256"] = proposal["sha256"]
        if time.monotonic() >= job["absolute"]:
            raise TimeoutError("No commit budget")
        durable_json(evidence / "audit_index.json", value)
    elif action == "delivery":
        durable_json(evidence / "http_delivery.json", job["value"])
        return
    value = json.loads((evidence / "audit_index.json").read_text())
    reason = value.get("reason") or "NONE"
    # Fixed, small framing. No solution/trace JSON parsing in the owner.
    sys.stdout.write("AUTH %s %d %d %s\n" % (value["status"], bool(value["finalization_incomplete"]), bool(value["archive_incomplete"]), reason))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stdout.write("ERR " + type(exc).__name__ + "\n")
        raise SystemExit(2)
