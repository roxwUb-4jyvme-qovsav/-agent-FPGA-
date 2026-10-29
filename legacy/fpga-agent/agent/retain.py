"""Bounded retention: critical legacy originals first, new diagnostics best-effort."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from rtl_agent.runtime_audit import durable_json

EXTRA_SUFFIXES = {".tcl", ".jou", ".xdc", ".sv", ".v", ".rpt", ".str", ".dcp"}


def retain(value):
    root, destination = Path(value["root"]), Path(value["destination"])
    absolute = value["absolute"]
    index = destination.parent / "eda_evidence_manifest.json"
    manifest = {"status": "INCOMPLETE", "phase": "RETENTION_STARTED", "reason": "RETENTION_NOT_COMMITTED",
                "files": {}, "file_details": {}, "omitted": [], "unknown_scope": str(root), "affected_path": str(root),
                "enumeration_failed": False,
                "diagnostic_capture": {"status": "INCOMPLETE", "scope": "NEW_OPTIONAL_EXECUTION_EVIDENCE", "omitted": []}}
    # Existing critical retention authority is unchanged. New diagnostics have a separate status.
    durable_json(index, manifest)

    def persist():
        if time.monotonic() >= absolute:
            raise TimeoutError("WALLCLOCK_TIMEOUT")
        durable_json(index, manifest)

    def is_extra(source):
        return ("eda_execution" in source.relative_to(root).parts or
                source.suffix in EXTRA_SUFFIXES and source.name != "selftest.sv")

    critical_committed = False
    try:
        destination.mkdir(parents=True, exist_ok=True)
        # Do not let optional Tcl/receipts consume the critical originals' budget first.
        for extra in (False, True):
            if extra and absolute - time.monotonic() < .25:
                manifest["diagnostic_capture"]["omitted"].append({"file": None, "reason": "OPTIONAL_RETENTION_DEADLINE_MARGIN", "unknown_scope": str(root)})
                break
            try:
                for source in root.rglob("*"):
                    if time.monotonic() >= absolute:
                        raise TimeoutError("WALLCLOCK_TIMEOUT")
                    if is_extra(source) != extra:
                        continue
                    if not extra and source.suffix not in {".json", ".log"} and source.name != "selftest.sv":
                        continue
                    if extra and source.suffix not in EXTRA_SUFFIXES | {".json", ".log"}:
                        continue
                    relative = source.relative_to(root).as_posix()
                    collection = manifest["diagnostic_capture"]["omitted"] if extra else manifest["omitted"]
                    if (len(manifest["files"]) + len(manifest["omitted"]) >= 256 or
                            len(json.dumps(manifest).encode()) + len(relative.encode()) + 1200 > 60000):
                        if extra:
                            collection.append({"file": relative, "reason": "MANIFEST_SIZE_LIMIT", "unknown_scope": str(root)})
                            break
                        manifest.update(enumeration_failed=True, reason="MANIFEST_SIZE_LIMIT")
                        break
                    try:
                        if source.is_symlink() or not source.is_file():
                            continue
                        if source.stat().st_size > 4 * 1024 * 1024:
                            raise ValueError("EVIDENCE_SIZE_LIMIT")
                        target = destination / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if any(p.is_symlink() for p in (destination, *target.parents) if p == destination or destination in p.parents):
                            raise ValueError("LINKED_DESTINATION")
                        data = source.read_bytes()
                        if len(data) > 4 * 1024 * 1024:
                            raise ValueError("EVIDENCE_SIZE_LIMIT")
                        digest = hashlib.sha256(data).hexdigest()
                        if target.exists():
                            if target.is_symlink() or target.read_bytes() != data:
                                raise FileExistsError("EVIDENCE_PATH_CONFLICT")
                        else:
                            # Exclusive creation never overwrites another round/run, even on a race.
                            with target.open("xb") as stream:
                                stream.write(data)
                                stream.flush()
                                os.fsync(stream.fileno())
                        if time.monotonic() >= absolute:
                            raise TimeoutError("WALLCLOCK_TIMEOUT")
                        manifest["files"][relative] = digest
                        manifest["file_details"][relative] = {"source": str(source), "path": str(target), "bytes": len(data), "sha256": digest}
                    except Exception as exc:
                        collection.append({"file": relative, "reason": "PROCESS_INTERRUPTED" if isinstance(exc, InterruptedError) else type(exc).__name__})
                        if not extra:
                            manifest["reason"] = collection[-1]["reason"]
                    persist()
            except Exception as exc:
                reason = "WALLCLOCK_TIMEOUT" if isinstance(exc, TimeoutError) else type(exc).__name__
                if extra:
                    manifest["diagnostic_capture"]["omitted"].append({"file": None, "reason": reason, "unknown_scope": str(root)})
                else:
                    manifest.update(enumeration_failed=True, reason=reason)
            if not extra and not manifest["omitted"] and not manifest["enumeration_failed"]:
                manifest.update(status="COMPLETE", phase="RETAINED", reason=None, unknown_scope=None)
                # Only a successful durable write establishes the critical commit.
                persist()
                critical_committed = True
        diagnostic = manifest["diagnostic_capture"]
        diagnostic["status"] = "INCOMPLETE" if diagnostic["omitted"] else "SAVED"
        persist()
    except Exception as exc:
        if critical_committed:
            manifest["diagnostic_capture"]["status"] = "INCOMPLETE"
            manifest["diagnostic_capture"]["omitted"].append({"file": None, "reason": type(exc).__name__})
        else:
            manifest.update(status="INCOMPLETE", phase="RETENTION_FAILED",
                            reason="WALLCLOCK_TIMEOUT" if isinstance(exc, TimeoutError) else type(exc).__name__, unknown_scope=str(root))
        try:
            durable_json(index, manifest)
        except Exception:
            pass
    return manifest


def main():
    value = json.load(sys.stdin)
    result = retain(value)
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()