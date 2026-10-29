"""网关权限由宿主上下文裁决，插件声明不能扩大权限。"""
import ipaddress
import socket
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit


class PolicyError(ValueError):
    pass


REGISTERED_CAPABILITIES = frozenset({"project.read_artifact", "rtl.lint", "rtl.simulate", "rtl.synthesize",
    "cad.instantiate_template", "cad.export_netlist", "cad.validate", "quote.lookup", "report.read", "job.cancel"})


def utc(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise PolicyError("TIMEZONE_REQUIRED")
    return instant.astimezone(timezone.utc)


def _resolve(host: str) -> list[str]:
    return list({entry[4][0] for entry in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


def validate_endpoint(endpoint: str, allowed_hosts: Iterable[str], *,
                      resolver: Callable[[str], Iterable[str]] | None = None,
                      approved_local: bool = False) -> tuple[str, ...]:
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or not host or parsed.fragment:
        raise PolicyError("INVALID_ENDPOINT")
    if host not in {value.lower() for value in allowed_hosts}:
        raise PolicyError("HOST_NOT_ALLOWED")
    if parsed.scheme != "https" and not (approved_local and parsed.scheme == "http"):
        raise PolicyError("HTTPS_REQUIRED")
    try:
        addresses = tuple(str(ipaddress.ip_address(value)) for value in (resolver or _resolve)(host))
    except (ValueError, OSError) as exc:
        raise PolicyError("DNS_RESOLUTION_FAILED") from exc
    if not addresses:
        raise PolicyError("DNS_RESOLUTION_FAILED")
    for value in addresses:
        address = ipaddress.ip_address(value)
        # 元数据、链路本地、未指定和组播地址永远禁止，本地批准也不能越过。
        if address.is_link_local or address.is_unspecified or address.is_multicast:
            raise PolicyError("ADDRESS_FORBIDDEN")
        if not address.is_global and not (approved_local and (address.is_private or address.is_loopback)):
            raise PolicyError("PRIVATE_ADDRESS_NOT_APPROVED")
    return addresses


def _check_input(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"shell", "command", "cmd", "argv", "script", "executable", "cwd", "host_path"}:
                raise PolicyError("ARBITRARY_EXECUTION_FORBIDDEN")
            _check_input(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_input(item)
    elif isinstance(value, str):
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or ".." in value.replace("\\", "/").split("/"):
            raise PolicyError("HOST_PATH_FORBIDDEN")


class ToolPolicy:
    def authorize(self, request: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        if not context.get("member") or request.get("project_id") != context.get("project_id"):
            raise PolicyError("PROJECT_ACCESS_DENIED")
        if request.get("revision_id") != context.get("revision_id"):
            raise PolicyError("STALE_REVISION")
        for field in ("enabled", "trusted", "healthy"):
            if context.get(field) is not True:
                raise PolicyError(f"EXTENSION_{field.upper()}_REQUIRED")
        capability = request.get("capability")
        if capability not in REGISTERED_CAPABILITIES or capability not in context.get("capabilities", ()):
            raise PolicyError("CAPABILITY_DENIED")
        if utc(request["deadline"]) <= datetime.now(timezone.utc):
            raise PolicyError("DEADLINE_EXCEEDED")
        required = set(context.get("required_approval_ids", ()))
        if not required.issubset(set(request.get("approval_ids", ())) & set(context.get("valid_approval_ids", ()))):
            raise PolicyError("APPROVAL_REQUIRED")
        _check_input(request.get("input", {}))
