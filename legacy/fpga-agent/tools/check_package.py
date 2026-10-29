"""CPU-only publication check. No model, service, download, task or EDA call."""
from pathlib import Path
import ast
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    binding = json.loads((ROOT / 'docs/SOURCE_BINDING.json').read_text(encoding='utf-8'))
    checked = 0
    for name, expected in binding['runtime_files_sha256'].items():
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing/linked source: ' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Source mismatch: ' + name)
        checked += 1
    python_files = 0
    for folder in ('agent', 'rtl_agent', 'serve'):
        for path in sorted((ROOT / folder).glob('*.py')):
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            python_files += 1
    config = json.loads((ROOT / 'configs/agent_v24.json').read_text(encoding='utf-8'))
    assert config['max_rounds'] == 3 and config['max_total_tokens'] == 12000
    assert config['max_wall_time_s'] == 300 and config['max_eda_calls'] == 3
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['model']['revision'] == '286cf433f596f1b8525529c1163eb81c19425c22'
    assert manifest['public_development_validation']['equal_budget'] is False
    print(json.dumps({'status':'PASS_CPU_PACKAGE_CHECK', 'runtime_hashes':checked,
                      'python_syntax_files':python_files, 'model_calls':0, 'EDA_calls':0,
                      'official_acceptance':'NOT_RUN'}))


if __name__ == '__main__':
    main()
