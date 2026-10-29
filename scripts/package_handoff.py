"""从 Git 提交打包交接源码；排除旧交付包，逐文件记录 SHA256。"""

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def package(ref: str, output: Path) -> Path:
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True, encoding="utf-8").strip())
    commit = subprocess.check_output(["git", "rev-parse", "--verify", ref + "^{commit}"], cwd=root, text=True, encoding="utf-8").strip()
    content = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=root)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"agent-FPGA-{commit[:7]}.zip"
    if target.exists():
        raise FileExistsError(f"交付包已存在：{target}")
    entries = []
    temporary = target.with_suffix(".zip.tmp")
    try:
        with tarfile.open(fileobj=io.BytesIO(content)) as source, zipfile.ZipFile(temporary, "x", zipfile.ZIP_DEFLATED) as archive:
            for item in source:
                path = PurePosixPath(item.name)
                if "deliveries" in path.parts or not item.isfile():
                    continue
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Git 归档中出现非法路径")
                handle = source.extractfile(item)
                assert handle is not None
                data = handle.read()
                archive.writestr("agent-FPGA/" + item.name, data)
                entries.append({"path": item.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            manifest = {"schema_version": "1.0", "source_commit": commit,
                        "hardware_test_status": "NOT_RUN", "files": entries}
            archive.writestr("agent-FPGA/package_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("交付包完整性验证失败")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".manifest.json").write_text(json.dumps({
        "source_commit": commit, "archive": target.name, "sha256": digest,
        "files": len(entries), "manifest": "agent-FPGA/package_manifest.json",
        "hardware_test_status": "NOT_RUN",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path, default=Path("deliveries"))
    args = parser.parse_args()
    print(package(args.ref, args.output).resolve())


if __name__ == "__main__":
    main()
