"""Read-only binding of one exposed AMD render node to a rocm-smi counter.

No model loading, GPU compute or service control. Ambiguity is a startup error;
HIP's process-local index is never substituted for a physical memory counter.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import stat
import subprocess


def _pci(value):
    if not isinstance(value, str):
        raise ValueError('PCI address must be a string')
    match = re.fullmatch(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])', value.strip())
    if not match:
        raise ValueError('Unrecognized PCI address')
    domain, bus, slot, function = (int(x, 16) for x in match.groups())
    return f'{domain:04x}:{bus:02x}:{slot:02x}.{function}'


def visible_amd_pci(dev_root=Path('/dev/dri'), sys_root=Path('/sys/class/drm')):
    records = []
    for node in sorted(Path(dev_root).glob('renderD*')):
        if not re.fullmatch(r'renderD[0-9]+', node.name) or not stat.S_ISCHR(node.stat().st_mode):
            continue
        device = Path(sys_root) / node.name / 'device'
        if (device / 'vendor').read_text(encoding='ascii').strip().lower() != '0x1002':
            continue
        records.append({'render_node': str(node), 'pci': _pci(device.resolve(strict=True).name)})
    if len(records) != 1:
        raise RuntimeError('Exactly one exposed AMD render device is required for automatic counter binding')
    return records[0]


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate key in GPU bus metadata')
        value[key] = item
    return value


def select_counter(visible, raw):
    """Pure selection from sysfs identity and vendor-reported PCI bus metadata."""
    wanted = _pci(visible['pci'])
    data = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(data, dict):
        raise ValueError('GPU bus query must return an object')
    matches = []
    for card, fields in data.items():
        match = re.fullmatch(r'card([0-9]+)', card)
        if not match or not isinstance(fields, dict):
            continue
        buses = [value for key, value in fields.items()
                 if re.sub('[^a-z0-9]', '', key.lower()) in ('pcibus', 'pcibusid', 'pcibusaddress')]
        if len(buses) > 1:
            raise ValueError('Ambiguous GPU PCI metadata')
        if buses and _pci(buses[0]) == wanted:
            matches.append(str(int(match[1])))
    if len(matches) != 1:
        raise RuntimeError('Exposed GPU must match exactly one vendor-reported physical counter')
    return {'method': 'EXPOSED_RENDER_PCI_AND_ROCM_SMI_BUS', 'physical_counter': matches[0],
            'pci': wanted, 'render_node': visible['render_node']}


def resolve_counter(environ=None, timeout=8):
    env = os.environ if environ is None else environ
    explicit = env.get('AGENT_INFERENCE_GPU')
    if explicit is not None:
        if not explicit.isascii() or not explicit.isdigit():
            raise ValueError('AGENT_INFERENCE_GPU must be a physical counter integer')
        return {'method': 'EXPLICIT_DEPLOYMENT_CONFIGURATION', 'physical_counter': str(int(explicit))}
    if env.get('HIP_VISIBLE_DEVICES', '0') != '0' or env.get('ROCR_VISIBLE_DEVICES', '0') != '0':
        raise RuntimeError('Automatic binding requires the single visible GPU selector; otherwise supply verified explicit mapping')
    if timeout <= 0:
        raise TimeoutError('GPU binding deadline exceeded')
    visible = visible_amd_pci()
    command = ['rocm-smi', '--showbus', '--json']
    query = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if query.returncode != 0:
        raise RuntimeError('Read-only GPU PCI query failed')
    result = select_counter(visible, query.stdout)
    result['command'] = command
    return result
