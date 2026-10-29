"""受控 MCP HTTP JSON 子集；stdio 必须交给独立受管进程服务。"""
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from packages.tool_gateway.policy import PolicyError, ToolPolicy, utc, validate_endpoint


class MCPError(ValueError):
    pass


def tools_schema_hash(tools: Iterable[Mapping[str, Any]]) -> str:
    # 工具发现顺序不是语义的一部分，哈希对工具名排序。
    encoded = json.dumps(sorted((dict(tool) for tool in tools), key=lambda item: item["name"]), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _reject_remote_refs(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef"} and (not isinstance(child, str) or not child.startswith("#")):
                raise MCPError("MCP_REMOTE_SCHEMA_REF_FORBIDDEN")
            _reject_remote_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_remote_refs(child)


class ControlledMCPClient:
    def __init__(self, config: Mapping[str, Any], *, capability_tools: Mapping[str, str],
                 allowed_hosts: Iterable[str] = (), approved_local: bool = False,
                 resolver: Callable[[str], Iterable[str]] | None = None,
                 authorization: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.config = deepcopy(dict(config))
        self.capability_tools = dict(capability_tools)
        self.allowed_hosts = tuple(allowed_hosts)
        self.approved_local, self.resolver = approved_local, resolver
        self.authorization, self.transport = authorization, transport
        self.session_id: str | None = None
        self._sequence = 0
        self._tools: dict[str, dict[str, Any]] = {}
        self._connected = False

    def _rpc(self, method: str, parameters: Mapping[str, Any], *, deadline: datetime,
             notification: bool = False) -> dict[str, Any]:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise MCPError("MCP_DEADLINE_EXCEEDED")
        endpoint = self.config.get("endpoint")
        try:
            validate_endpoint(endpoint, self.allowed_hosts, resolver=self.resolver, approved_local=self.approved_local)
        except (PolicyError, TypeError, ValueError) as exc:
            raise MCPError("MCP_ENDPOINT_DENIED") from exc
        self._sequence += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": dict(parameters)}
        if not notification:
            body["id"] = self._sequence
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-03-26"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            with httpx.Client(transport=self.transport, timeout=min(remaining, self.config.get("timeout_seconds", 30)), follow_redirects=False) as client:
                response = client.post(endpoint, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise MCPError("MCP_TIMEOUT_RESULT_UNKNOWN") from exc
        except httpx.HTTPError as exc:
            raise MCPError("MCP_TRANSPORT_FAILED") from exc
        if notification and response.status_code in {200, 202, 204}:
            return {}
        if response.status_code != 200:
            raise MCPError({401: "MCP_AUTHENTICATION_FAILED", 403: "MCP_PERMISSION_DENIED", 429: "MCP_RATE_LIMITED"}.get(response.status_code, "MCP_HTTP_FAILED"))
        if "text/event-stream" in response.headers.get("content-type", ""):
            raise MCPError("MCP_STREAM_TRANSPORT_NOT_IMPLEMENTED")
        try:
            value = response.json()
            if value.get("jsonrpc") != "2.0" or value.get("id") != body["id"]:
                raise MCPError("MCP_RESPONSE_ID_MISMATCH")
            if "error" in value:
                raise MCPError("MCP_REMOTE_ERROR")
            result = value["result"]
            if not isinstance(result, dict):
                raise MCPError("MCP_RESPONSE_INVALID")
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, MCPError):
                raise
            raise MCPError("MCP_RESPONSE_INVALID") from exc
        if response.headers.get("Mcp-Session-Id"):
            self.session_id = response.headers["Mcp-Session-Id"]
        return result

    def connect(self, context: Mapping[str, Any]) -> dict[str, Any]:
        self._connected = False
        self._tools = {}
        if context.get("manage_connections") is not True:
            raise MCPError("MCP_MANAGEMENT_DENIED")
        if not all(context.get(key) is True for key in ("enabled", "trusted", "healthy")):
            raise MCPError("MCP_CONNECTION_DISABLED")
        if self.config.get("transport") == "stdio":
            raise MCPError("MCP_MANAGED_STDIO_NOT_CONFIGURED")
        if self.config.get("transport") != "streamable_http":
            raise MCPError("MCP_TRANSPORT_UNSUPPORTED")
        from datetime import timedelta
        deadline = datetime.now(timezone.utc) + timedelta(seconds=self.config.get("timeout_seconds", 30))
        initialized = self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "fpga-tool-gateway", "version": "0.1.0"}}, deadline=deadline)
        if initialized.get("protocolVersion") != "2025-03-26":
            raise MCPError("MCP_PROTOCOL_INCOMPATIBLE")
        self._rpc("notifications/initialized", {}, deadline=deadline, notification=True)
        discovered = self._rpc("tools/list", {}, deadline=deadline)
        if discovered.get("nextCursor"):
            raise MCPError("MCP_PAGINATED_DISCOVERY_NOT_IMPLEMENTED")
        tool_list = discovered.get("tools")
        if not isinstance(tool_list, list):
            raise MCPError("MCP_TOOL_SCHEMA_INVALID")
        try:
            for tool in tool_list:
                if not isinstance(tool["name"], str) or tool["name"] in self._tools:
                    raise MCPError("MCP_TOOL_SCHEMA_INVALID")
                _reject_remote_refs(tool["inputSchema"])
                Draft202012Validator.check_schema(tool["inputSchema"])
                self._tools[tool["name"]] = deepcopy(tool)
            digest = tools_schema_hash(tool_list)
        except (KeyError, TypeError, SchemaError) as exc:
            raise MCPError("MCP_TOOL_SCHEMA_INVALID") from exc
        if not self.config.get("schema_sha256") or digest != self.config["schema_sha256"]:
            self._tools = {}
            raise MCPError("MCP_SCHEMA_CHANGED")
        self._connected = True
        return {"status": "CONNECTED", "tools": deepcopy(tool_list), "schema_sha256": digest}

    def call(self, request: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        ToolPolicy().authorize(request, context)
        if not self._connected:
            raise MCPError("MCP_NOT_CONNECTED")
        name = self.capability_tools.get(str(request.get("capability")))
        if name not in self._tools:
            raise MCPError("MCP_TOOL_NOT_REGISTERED")
        try:
            Draft202012Validator(self._tools[name]["inputSchema"]).validate(request.get("input", {}))
        except ValidationError as exc:
            raise MCPError("MCP_TOOL_INPUT_INVALID") from exc
        result = self._rpc("tools/call", {"name": name, "arguments": request.get("input", {})}, deadline=utc(request["deadline"]))
        if not isinstance(result.get("content"), list):
            raise MCPError("MCP_TOOL_OUTPUT_INVALID")
        # isError 是工具自身失败，HTTP 200 不能将它变成成功。
        if result.get("isError") is True:
            raise MCPError("MCP_TOOL_FAILED")
        return deepcopy(result)

    def disconnect(self) -> None:
        """只撤销本平台会话，不宣称停止了第三方服务器。"""
        self._connected, self._tools, self.session_id = False, {}, None
