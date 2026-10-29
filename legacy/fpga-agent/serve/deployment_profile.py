"""Pure CodeV deployment configuration and bounded launch planning.

Importing this module does not query GPU, connect to a service or start vLLM.
The module entry is used only by the explicitly invoked serve/vllm.sh launcher.
"""
from __future__ import annotations
import math
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

MAX_VRAM_BYTES = 32_000_000_000
PLANNED_VRAM_BYTES = 30_000_000_000
_BINDING_LOCK = threading.Lock()


def profile(environ=None):
    env = os.environ if environ is None else environ
    alias = env.get('MODEL_NAME', 'codev-r1-7b')
    if alias != 'codev-r1-7b' or env.get('LLM_MODEL', alias) != alias:
        raise ValueError('CodeV deployment requires one identical registered alias')
    url = env.get('LLM_BASE_URL', 'http://127.0.0.1:8002/v1').rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != '/v1' or parsed.port is None):
        raise ValueError('Deployment endpoint must be an explicit loopback /v1 URL')
    if env.get('PORT') and env['PORT'] != str(parsed.port):
        raise ValueError('PORT conflicts with LLM_BASE_URL; refusing a second endpoint')
    raw_context = env.get('AGENT_CONTEXT_TOKENS', '32768')
    if str(raw_context) != '32768':
        raise ValueError('This deployment profile locks context to 32768')
    visible = env.get('HIP_VISIBLE_DEVICES', '0')
    if not visible.isdigit() or ',' in visible:
        raise ValueError('One inference GPU must be selected')
    return {'model': alias, 'base_url': url, 'host': '127.0.0.1',
            'port': parsed.port, 'context_tokens': 32768,
            'model_path': env.get('MODEL_PATH', '/opt/rtl-agent/model/weights'),
            'visible_device': visible, 'max_vram_bytes': MAX_VRAM_BYTES}


def validate_memory(measurement):
    if not isinstance(measurement, dict) or measurement.get('status') != 'MEASURED':
        raise RuntimeError('Measured GPU VRAM bytes are required; unknown is not ready')
    total = measurement.get('total_bytes')
    used = measurement.get('used_bytes')
    if (type(total) is not int or type(used) is not int
            or total <= 0 or not 0 <= used <= total):
        raise ValueError('Invalid measured GPU byte counters')
    if used > MAX_VRAM_BYTES:
        raise RuntimeError('Actual GPU VRAM exceeds 32000000000 bytes')
    return {'used_bytes': used, 'total_bytes': total, 'limit_bytes': MAX_VRAM_BYTES}


def measured_memory(timeout=8):
    from .gpu_binding import resolve_counter
    if not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError('GPU memory query requires a positive finite timeout')
    deadline = time.monotonic() + timeout
    # Export only to this process/its children, never to system configuration.
    # This also lets the unchanged Agent VRAM probe inherit a proven counter.
    if not _BINDING_LOCK.acquire(timeout=min(timeout, max(0, deadline - time.monotonic()))):
        raise TimeoutError('GPU binding lock deadline exceeded')
    try:
        binding = resolve_counter(timeout=min(timeout, max(0, deadline - time.monotonic())))
        os.environ.setdefault('AGENT_INFERENCE_GPU', binding['physical_counter'])
        if str(int(os.environ['AGENT_INFERENCE_GPU'])) != binding['physical_counter']:
            raise RuntimeError('GPU counter changed during startup binding')
    finally:
        _BINDING_LOCK.release()
    remaining = min(timeout, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError('GPU memory query deadline exceeded')
    from rtl_agent.gpu_measurement import probe_vram
    result = probe_vram(remaining)
    result['physical_binding'] = binding
    return result


def validate_ready_memory(measurement):
    result = validate_memory(measurement)
    if result['used_bytes'] == 0:
        raise RuntimeError('Loaded model memory must not be reported as zero')
    return result


def build_vllm_command(environ, measurement, executable):
    cfg = profile(environ)
    memory = validate_memory(measurement)
    model = Path(cfg['model_path'])
    if not model.is_dir() or not (model / 'config.json').is_file():
        raise ValueError('MODEL_PATH must already contain staged local model files')
    raw = environ.get('GPU_MEMORY_UTILIZATION')
    if raw is None or raw == '':
        remaining = PLANNED_VRAM_BYTES - memory['used_bytes']
        if remaining <= 0:
            raise RuntimeError('No conservative VRAM headroom; do not launch another service')
        utilization = min(.95, remaining / memory['total_bytes'])
    else:
        utilization = float(raw)
    if not math.isfinite(utilization) or not 0 < utilization <= 1:
        raise ValueError('GPU_MEMORY_UTILIZATION must be finite and within (0, 1]')
    if memory['used_bytes'] + utilization * memory['total_bytes'] > MAX_VRAM_BYTES:
        raise ValueError('Requested GPU reservation plus current usage exceeds the byte limit')
    # This is a launch plan, not a promise about actual inference peak memory.
    return [executable, '-m', 'vllm.entrypoints.openai.api_server',
            '--model', str(model), '--served-model-name', cfg['model'],
            '--host', cfg['host'], '--port', str(cfg['port']),
            '--max-model-len', str(cfg['context_tokens']),
            '--dtype', 'bfloat16', '--tensor-parallel-size', '1',
            '--max-num-seqs', '1', '--enforce-eager',
            '--gpu-memory-utilization', format(utilization, '.10g')]


def main():
    command = build_vllm_command(os.environ, measured_memory(), sys.executable)
    os.execv(sys.executable, command)


if __name__ == '__main__':
    main()
