"""Linux task descendants: birth/UID checks, pidfds and scoped orphan reaping.

Only a task's own tracked descendants are signalled/reaped. Resident inference
services must be started separately by start.sh, never by a single-task entry.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import threading
import time


def become_subreaper():
    if not Path('/proc/self/stat').exists():
        return False
    try:
        return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def processes():
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            result[int(entry.name)] = (int(fields[1]), int(fields[2]), int(fields[3]),
                                      fields[19], entry.stat().st_uid)
        except (OSError, IndexError, ValueError):
            pass
    return result


class TaskProcess:
    def __init__(self, proc):
        self.proc = proc
        self.known = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.enabled = os.name == 'posix' and Path('/proc/self/stat').exists()
        self.report = {"scope": "task_descendants_only", "signalled": [], "reaped": [],
                       "identity_rejected": [], "remaining": [], "linux_runtime": self.enabled}
        self.thread = None
        if self.enabled:
            self._capture()
            self.thread = threading.Thread(target=self._watch, daemon=True)
            self.thread.start()

    def _capture(self):
        with self.lock:
            self._capture_locked()

    def _capture_locked(self):
        rows = processes()
        root = rows.get(self.proc.pid)
        if self.proc.pid not in self.known and root and root[0] == os.getpid():
            self.known[self.proc.pid] = root
        current = {pid for pid, row in self.known.items() if rows.get(pid) == row}
        # PPID may change when an orphan is adopted; identity uses birth+UID.
        current |= {pid for pid, row in self.known.items() if pid in rows and rows[pid][3:] == row[3:]}
        while True:
            children = {pid for pid, row in rows.items() if row[0] in current and row[4] == os.getuid()}
            new = children - current
            for pid in new:
                self.known[pid] = rows[pid]
            if not new:
                break
            current |= new
        # Non-detached descendants remain attributable after the shell exits.
        for pid, row in rows.items():
            if row[1] == self.proc.pid and row[2] == self.proc.pid and row[4] == os.getuid():
                leader = rows.get(self.proc.pid)
                expected = self.known.get(self.proc.pid)
                if expected and (leader is None or leader[3:] == expected[3:]):
                    self.known.setdefault(pid, row)

    def _watch(self):
        while not self.stop.wait(.02):
            self._capture()

    def _signal(self, pid, original, sig):
        descriptor = None
        try:
            if hasattr(os, 'pidfd_open'):
                descriptor = os.pidfd_open(pid)
            current = processes().get(pid)
            if not current or current[3:] != original[3:]:
                self.report['identity_rejected'].append(pid)
                return
            if descriptor is not None and hasattr(signal, 'pidfd_send_signal'):
                signal.pidfd_send_signal(descriptor, sig)
            else:
                # Without a pidfd we cannot close the verify/kill reuse race.
                # Reap an exited child, but do not signal by an unsafe PID.
                self.report['identity_rejected'].append(pid)
                return
            self.report['signalled'].append({"pid": pid, "signal": int(sig), "via": "pidfd"})
        except (ProcessLookupError, PermissionError):
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def close(self, absolute):
        self.stop.set()
        if self.thread:
            self.thread.join(max(0, min(.1, absolute-time.monotonic())))
        if not self.enabled:
            return self.report
        self._capture()
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with self.lock:
                snapshot = list(self.known.items())
            for pid, row in snapshot:
                self._signal(pid, row, sig)
            if sig == signal.SIGTERM:
                try:
                    self.proc.wait(timeout=max(0, min(.05, absolute-time.monotonic())))
                except Exception:
                    pass
        while True:
            rows = processes()
            left = []
            with self.lock:
                snapshot = list(self.known.items())
            for pid, original in snapshot:
                row = rows.get(pid)
                if not row or row[3:] != original[3:]:
                    continue
                left.append(pid)
                if pid == self.proc.pid:
                    self.proc.poll()
                elif row[0] == os.getpid():
                    try:
                        got, _ = os.waitpid(pid, os.WNOHANG)
                        if got:
                            self.report['reaped'].append(pid)
                    except ChildProcessError:
                        pass
            if not left or time.monotonic() >= absolute:
                self.report['remaining'] = left
                return self.report
            time.sleep(min(.01, max(0, absolute-time.monotonic())))
