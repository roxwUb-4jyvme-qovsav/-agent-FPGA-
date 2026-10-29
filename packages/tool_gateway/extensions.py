"""扩展包只验证和锁定；绝不导入包或执行安装脚本。"""
import hashlib
import json
from typing import Any, Iterable, Mapping

from packages.orchestration.dag import DependencyGraph, GraphError


class ExtensionError(ValueError):
    pass


def permission_diff(old: Iterable[Mapping[str, Any]], new: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    before = {json.dumps(dict(value), sort_keys=True): dict(value) for value in old}
    after = {json.dumps(dict(value), sort_keys=True): dict(value) for value in new}
    return {"added": [after[key] for key in sorted(after.keys() - before.keys())],
            "removed": [before[key] for key in sorted(before.keys() - after.keys())]}


def validate_package(manifest: Mapping[str, Any], payload: bytes, dependencies: Mapping[str, Mapping[str, Any]], *,
                     trusted: bool = False, approved_sources: Iterable[str] = (), signature_verified: bool = False) -> dict[str, Any]:
    if manifest.get("engine_api") != "1.0":
        raise ExtensionError("ENGINE_API_INCOMPATIBLE")
    if hashlib.sha256(payload).hexdigest() != manifest.get("payload_sha256"):
        raise ExtensionError("PAYLOAD_HASH_MISMATCH")
    if not (trusted or (manifest.get("signature") and signature_verified)):
        raise ExtensionError("EXPLICIT_TRUST_REQUIRED")
    sources = set(approved_sources)
    if manifest.get("source_ref") not in sources:
        raise ExtensionError("SOURCE_NOT_APPROVED")
    packages = {str(key): dict(value) for key, value in dependencies.items()}
    own_id = str(manifest["id"])
    existing = packages.get(own_id)
    if existing and existing.get("version") == manifest["version"] and existing.get("payload_sha256") != manifest["payload_sha256"]:
        raise ExtensionError("SAME_VERSION_DIFFERENT_CONTENT")
    packages[own_id] = dict(manifest)
    graph: dict[str, list[str]] = {}
    for package_id, package in packages.items():
        if package.get("source_ref") not in sources:
            raise ExtensionError("DEPENDENCY_SOURCE_NOT_APPROVED")
        graph[package_id] = []
        for requirement in package.get("dependencies", ()):
            target = packages.get(requirement["id"])
            if not target:
                raise ExtensionError("DEPENDENCY_MISSING")
            if requirement["version"] != target.get("version") or requirement["payload_sha256"] != target.get("payload_sha256"):
                raise ExtensionError("DEPENDENCY_LOCK_MISMATCH")
            graph[package_id].append(requirement["id"])
    try:
        order = DependencyGraph(graph).topological_order()
    except GraphError as exc:
        raise ExtensionError(str(exc)) from exc
    for package_id in order:
        if package_id == own_id:
            continue
        dependency = packages[package_id]
        if dependency.get("status") not in {"APPROVED", "ENABLED"} or dependency.get("trusted") is not True:
            raise ExtensionError("DEPENDENCY_NOT_APPROVED")
    return {"status": "VALIDATED", "package_id": own_id, "payload_sha256": manifest["payload_sha256"],
            "signature_state": "verified" if signature_verified else "unsigned" if not manifest.get("signature") else "unverified",
            "dependency_lock": [{"id": key, "version": packages[key]["version"], "payload_sha256": packages[key]["payload_sha256"]} for key in order]}


EXTENSION_TRANSITIONS: dict[str, set[str]] = {"DISCOVERED": {"QUARANTINED"}, "QUARANTINED": {"VALIDATED", "FAILED"},
    "VALIDATED": {"APPROVED", "FAILED"}, "APPROVED": {"ENABLED", "FAILED", "DISABLED"},
    "ENABLED": {"DISABLED", "FAILED"}, "DISABLED": {"ENABLED", "REMOVED"}, "FAILED": {"QUARANTINED", "REMOVED"}, "REMOVED": set()}


def transition_extension(current: str, target: str, *, dependencies_valid: bool = False, compatible: bool = False,
                         trusted: bool = False, healthy: bool = False, approved: bool = False) -> str:
    if target not in EXTENSION_TRANSITIONS.get(current, set()):
        raise ExtensionError("INVALID_EXTENSION_TRANSITION")
    if target in {"APPROVED", "ENABLED"} and not (dependencies_valid and compatible and trusted):
        raise ExtensionError("EXTENSION_PRECONDITION_FAILED")
    if target == "ENABLED" and not (healthy and approved):
        raise ExtensionError("EXTENSION_APPROVAL_AND_HEALTH_REQUIRED")
    return target
