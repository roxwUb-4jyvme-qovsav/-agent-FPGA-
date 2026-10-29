"""Killable I/O worker boundary; no ordinary thread is used as an I/O deadline."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

from .runtime_audit import PROCESSES

ROOT = Path(__file__).resolve().parents[1]


def job(module, value, absolute):
    remaining = absolute - time.monotonic()
    if remaining <= 0:
        return None, "WALLCLOCK_TIMEOUT"
    try:
        proc = subprocess.Popen([sys.executable, "-m", module], cwd=ROOT,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=(os.name != "nt"))
    except OSError as exc:
        return None, "WORKER_ERROR_" + type(exc).__name__
    PROCESSES[proc.pid] = proc
    try:
        raw, error = proc.communicate(json.dumps(value).encode(), timeout=max(.001, remaining))
        if proc.returncode != 0:
            if re.fullmatch(rb"ERR [A-Za-z_]{1,80}\r?\n", raw):
                return None, "WORKER_ERROR_" + raw.strip().split()[1].decode("ascii")
            return None, "WORKER_ERROR"
        if time.monotonic() >= absolute:
            return None, "WALLCLOCK_TIMEOUT"
        return raw, None
    except subprocess.TimeoutExpired:
        # These Python I/O workers never spawn EDA or other children. Avoid
        # procfs enumeration/taskkill and its independent grace budgets here.
        proc.kill()
        try:
            proc.wait(timeout=.1)
        except subprocess.TimeoutExpired:
            return None, "WORKER_REAP_UNCONFIRMED"
        return None, "WALLCLOCK_TIMEOUT"
    except (OSError, ValueError, TypeError):
        return None, "WORKER_ERROR"
    finally:
        PROCESSES.pop(proc.pid, None)
