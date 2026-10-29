"""只检测本地工具环境，不发模型请求或运行工程。"""
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

TOOLS = {"iverilog": ("iverilog", "-V"), "verilator": ("verilator", "--version"),
         "yosys": ("yosys", "-V"), "kicad": ("kicad-cli", "version"), "docker": ("docker", "--version")}


def detect_tools() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name, command in TOOLS.items():
        executable = shutil.which(command[0])
        if not executable:
            results[name] = {"status": "NOT_CONFIGURED", "version": None, "reason": "EXECUTABLE_NOT_FOUND"}
            continue
        try:
            response = subprocess.run([executable, *command[1:]], capture_output=True, text=True, timeout=5, shell=False)
            output = (response.stdout + response.stderr).strip().splitlines()
            results[name] = {"status": "AVAILABLE" if response.returncode == 0 else "UNHEALTHY", "version": output[0][:300] if output else None,
                             "reason": None if response.returncode == 0 else "VERSION_PROBE_FAILED"}
        except (OSError, subprocess.TimeoutExpired):
            results[name] = {"status": "UNHEALTHY", "version": None, "reason": "VERSION_PROBE_FAILED"}
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "tools": results, "isolation": "NOT_CONFIGURED", "hardware_test_status": "NOT_RUN"}
