"""Measure VRAM bytes; utilization percentages are never memory quantities."""
from __future__ import annotations
import json
import os
import subprocess
import time


def parse_vram(raw: str, device: str) -> dict:
    data = json.loads(raw)
    card = data.get("card" + str(device))
    if not isinstance(card, dict):
        raise ValueError("Selected inference device missing from raw VRAM measurement")
    def byte_value(label):
        matches = [value for key, value in card.items()
                   if "vram" in key.lower() and label in key.lower() and "(b)" in key.lower()
                   and (label != "total" or "used" not in key.lower())]
        if len(matches) != 1 or not str(matches[0]).isdigit():
            raise ValueError("VRAM interface must report explicit integer bytes")
        return int(matches[0])
    used, total = byte_value("used"), byte_value("total")
    if not 0 <= used <= total or total <= 0:
        raise ValueError("Invalid VRAM measurement")
    return {"device": str(device), "used_bytes": used, "total_bytes": total,
            "used_gib": used / 2**30, "total_gib": total / 2**30, "unit": "bytes"}


def probe_vram(timeout: float = 8) -> dict:
    device = os.environ.get("AGENT_INFERENCE_GPU")
    visible = os.environ.get("ROCR_VISIBLE_DEVICES", os.environ.get("HIP_VISIBLE_DEVICES", ""))
    if device is None:
        if not visible or "," in visible or not visible.isdigit():
            return {"status": "NOT_VERIFIED", "reason": "Set AGENT_INFERENCE_GPU to the physical rocm-smi device index"}
        device = visible
    command = ["rocm-smi", "--showmeminfo", "vram", "--json"]
    record = {"command": command, "physical_device": device, "visible_devices": visible,
              "ts": time.time(), "status": "NOT_VERIFIED"}
    try:
        from .runtime_audit import PROCESSES, terminate_tree
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=(os.name != "nt"))
        PROCESSES[process.pid] = process
        try:
            stdout, stderr = process.communicate(timeout=max(0.01, timeout))
        finally:
            if process.poll() is None:
                terminate_tree(process, grace=.1)
            PROCESSES.pop(process.pid, None)
        record.update(returncode=process.returncode, stdout=stdout, stderr=stderr)
        if process.returncode != 0:
            raise ValueError("rocm-smi VRAM query failed")
        record.update(parse_vram(stdout, device), status="MEASURED")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        record["reason"] = type(exc).__name__
    return record
