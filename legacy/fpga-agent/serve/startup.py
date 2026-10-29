"""Idempotent Linux startup; explicit bash launcher, setsid, early logs and PID.

This does not download weights or start the submission HTTP endpoint. The
container-rebuild fallback needs only the resident model, not HTTP transport.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from agent.main import _model_config
from rtl_agent.local_model import tokenize_chat
from rtl_agent.runtime_audit import durable_json
from .task_process import TaskProcess, become_subreaper
from .deployment_profile import profile, validate_ready_memory, measured_memory

ROOT = Path(__file__).resolve().parents[1]


def ready(config, absolute):
    left = min(2, absolute-time.monotonic())
    if left <= 0:
        raise TimeoutError('Startup deadline exceeded')
    try:
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with local.open(config['base_url'] + '/models', timeout=left) as response:
            data = json.load(response)
    except urllib.error.HTTPError:
        raise RuntimeError('Existing model service returned an HTTP error; refusing duplicate launch')
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False
    if [item.get('id') for item in data.get('data', [])] != [config['model']]:
        raise RuntimeError('Existing model alias differs; refusing duplicate launch')
    model_context = data['data'][0].get('max_model_len')
    if model_context is not None and model_context != config['context_tokens']:
        raise RuntimeError('Existing model context differs')
    result = tokenize_chat(config, [{'role': 'user', 'content': 'readiness'}],
                           timeout_s=max(.001, min(2, absolute-time.monotonic())))
    if result['max_model_len'] != config['context_tokens']:
        raise RuntimeError('Tokenizer context differs')
    if os.environ.get('AGENT_REQUIRE_VRAM', '1') == '1':
        validate_ready_memory(measured_memory())
    return True


def start():
    if not sys.platform.startswith('linux'):
        raise RuntimeError('start.sh requires Linux; Windows tests use explicit fixtures')
    import fcntl
    profile()
    config = _model_config()
    timeout = float(os.environ.get('START_TIMEOUT_S', '900'))
    if not 0 < timeout <= 3600:
        raise ValueError('START_TIMEOUT_S outside 0..3600')
    log_dir = Path(os.environ.get('SERVE_LOG_DIR', '/tmp/rtl-serve'))
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / 'startup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        absolute = time.monotonic() + timeout
        if ready(config, absolute):
            return {'status': 'READY', 'action': 'ALREADY_RUNNING', 'model': config['model']}
        if os.name == 'posix' and not become_subreaper():
            raise RuntimeError('Startup requires a Linux child subreaper')
        model = Path(os.environ.get('MODEL_PATH', ''))
        if not (model / 'config.json').is_file():
            raise ValueError('MODEL_PATH must contain staged local weights')
        scratch = Path(os.environ.get('EDA_TMP', '/tmp/eda'))
        scratch.mkdir(parents=True, exist_ok=True)
        # Do not run broad directory expiry/deletion. Each task owns its scratch
        # and retains small evidence before its TemporaryDirectory is removed.
        command = ['setsid', 'bash', str(ROOT / 'serve/vllm.sh')]
        with (log_dir / 'vllm.log').open('ab', buffering=0) as log:
            proc = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT)
        state = {'status': 'STARTING', 'pid': proc.pid, 'command': command,
                 'model': config['model'], 'context_tokens': config['context_tokens'],
                 'log': 'vllm.log', 'started_at': time.time()}
        scope = None
        try:
            scope = TaskProcess(proc)
            durable_json(log_dir / 'startup.json', state)
            while time.monotonic() < absolute:
                code = proc.poll()
                if code is not None:
                    raise RuntimeError('Model exited during startup: ' + str(code))
                if ready(config, absolute):
                    state.update(status='READY', ready_at=time.time())
                    durable_json(log_dir / 'startup.json', state)
                    scope.stop.set()
                    if scope.thread:
                        scope.thread.join(.1)
                    return state
                time.sleep(min(.25, max(0, absolute-time.monotonic())))
            raise TimeoutError('Model startup timeout')
        except Exception as exc:
            # Only our Popen object is cancelled. No PID file / numeric kill.
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            state.update(status='FAILED', reason=type(exc).__name__, returncode=proc.poll(),
                         ended_at=time.time())
            state['cleanup'] = (scope.close(time.monotonic()+2) if scope is not None else
                                {'status': 'INCOMPLETE', 'reason': 'OBSERVER_NOT_CREATED'})
            durable_json(log_dir / 'startup.json', state)
            raise


if __name__ == '__main__':
    print(json.dumps(start()))
