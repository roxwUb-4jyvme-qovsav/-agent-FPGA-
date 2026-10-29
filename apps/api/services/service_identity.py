"""本机只读服务登记；每次请求重读，使禁用与权限收回立即生效。"""

import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import TaskRecord
from ..security import token_hash


@dataclass(frozen=True)
class ServicePrincipal:
    id: str
    role: str = "worker"
    worker_id: str | None = None
    capabilities: tuple[str, ...] = ("contract.validate",)
    project_ids: tuple[str, ...] = ("*",)
    issuer_id: str | None = None


def _uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("noncanonical uuid")
    return value


def _registry(path: Path) -> list[dict[str, Any]]:
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("registry too large")
        document = json.loads(path.read_text(encoding="utf-8"))
        if set(document) != {"schema_version", "principals"} or document["schema_version"] != "1.0":
            raise ValueError("invalid registry version")
        entries = document["principals"]
        if not isinstance(entries, list) or len(entries) > 1000:
            raise ValueError("invalid principal list")
        identities, tokens = set(), set()
        for item in entries:
            if set(item) != {
                "worker_id",
                "role",
                "enabled",
                "token_sha256",
                "project_ids",
                "capabilities",
                "issuer_id",
            }:
                raise ValueError("invalid principal fields")
            identity = _uuid(item["worker_id"])
            digest = item["token_sha256"]
            if (
                not re.fullmatch(r"[0-9a-f]{64}", digest)
                or identity in identities
                or digest in tokens
            ):
                raise ValueError("duplicate identity/token or invalid digest")
            identities.add(identity)
            tokens.add(digest)
            if type(item["enabled"]) is not bool or item["role"] not in ("worker", "verifier"):
                raise ValueError("invalid role/enabled")
            allowed = ["contract.validate"] if item["role"] == "worker" else ["acceptance.verify"]
            if item["capabilities"] != allowed:
                raise ValueError("unsupported registered capabilities")
            projects = item["project_ids"]
            if (
                not isinstance(projects, list)
                or not projects
                or len(set(projects)) != len(projects)
            ):
                raise ValueError("invalid projects")
            for project in projects:
                if project != "*":
                    _uuid(project)
            if item["role"] == "verifier":
                _uuid(item["issuer_id"])
            elif item["issuer_id"] is not None:
                raise ValueError("generation worker cannot be an issuer")
        return entries
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # 不把文件内容或凭据写进错误响应，登记异常时禁止退回共享令牌。
        raise ApiError(
            503, "SERVICE_REGISTRY_INVALID", "服务身份登记不可用，请检查本机配置"
        ) from exc


def authorize_task(principal: ServicePrincipal, task: TaskRecord) -> None:
    if (
        task.capability not in principal.capabilities
        or ("*" not in principal.project_ids and task.project_id not in principal.project_ids)
        or (principal.worker_id is not None and task.worker_id != principal.worker_id)
    ):
        raise ApiError(403, "SERVICE_SCOPE_DENIED", "服务身份无权访问该任务")


def worker_auth(db: Session, request: Request, *, write: bool = False) -> ServicePrincipal:
    settings = request.app.state.settings
    supplied = request.headers.get("authorization", "")
    principal = None
    if settings.service_principals is not None:
        entries = _registry(settings.service_principals)
        digest = token_hash(supplied.removeprefix("Bearer "))
        for item in entries:
            if (
                supplied.startswith("Bearer ")
                and secrets.compare_digest(digest, item["token_sha256"])
                and item["enabled"]
            ):
                principal = ServicePrincipal(
                    "service:" + token_hash(item["worker_id"])[:24],
                    item["role"],
                    item["worker_id"],
                    tuple(item["capabilities"]),
                    tuple(item["project_ids"]),
                    item["issuer_id"],
                )
    elif settings.worker_token and secrets.compare_digest(
        supplied, "Bearer " + settings.worker_token
    ):
        principal = ServicePrincipal("worker:" + token_hash(settings.worker_token)[:24])
    if principal is None:
        raise ApiError(401, "WORKER_UNAUTHORIZED", "服务凭据无效、已撤销或尚未配置")
    job_id = request.path_params.get("job_id")
    if job_id:
        task = db.get(TaskRecord, job_id)
        if task is not None:
            authorize_task(principal, task)
    return principal


def verifier_auth(db: Session, request: Request, *, write: bool = False) -> ServicePrincipal:
    principal = worker_auth(db, request, write=write)
    if principal.role != "verifier":
        raise ApiError(403, "VERIFIER_REQUIRED", "该入口只接受登记的独立验收身份")
    return principal


def authorize_claim(principal: ServicePrincipal, body: dict) -> None:
    if (principal.worker_id is not None and body["worker_id"] != principal.worker_id) or not set(
        body["capabilities"]
    ) <= set(principal.capabilities):
        raise ApiError(403, "SERVICE_SCOPE_DENIED", "领取身份或能力超出服务登记")
