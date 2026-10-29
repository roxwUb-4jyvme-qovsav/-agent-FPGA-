"""OpenAI 兼容协议的显式国产 Provider；不配置即拒绝外发。"""
import json
import time
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Protocol

import httpx

from packages.tool_gateway.policy import PolicyError, utc, validate_endpoint


class ModelProvider(Protocol):
    def generate(self, request: Mapping[str, Any]) -> dict[str, Any]: ...
    def embed(self, request: Mapping[str, Any]) -> dict[str, Any]: ...
    def rerank(self, request: Mapping[str, Any]) -> dict[str, Any]: ...


class OpenAICompatibleProvider:
    def __init__(self, profile: Mapping[str, Any], *, api_key: str | None = None,
                 allowed_hosts: Iterable[str] = (), external_send_allowed: bool = False,
                 transport: httpx.BaseTransport | None = None,
                 reservation_validator: Callable[[str], bool] | None = None,
                 resolver: Callable[[str], Iterable[str]] | None = None,
                 output_validator: Callable[[str, Any], None] | None = None,
                 max_retries: int = 2, sleeper: Callable[[float], None] = time.sleep) -> None:
        self.profile = deepcopy(dict(profile))
        self.api_key = api_key
        self.allowed_hosts = tuple(allowed_hosts)
        self.external_send_allowed = external_send_allowed
        self.transport = transport
        self.reservation_validator = reservation_validator
        self.resolver = resolver
        self.output_validator = output_validator
        self.max_retries = max(0, min(max_retries, 2))
        self.sleeper = sleeper

    def _result(self, started: float, *, status: str = "failed", code: str | None = None, **values: Any) -> dict[str, Any]:
        result = {"schema_version": "1.0", "status": status, "text": "", "requested_model": self.profile.get("model_id", "unconfigured"),
                  "actual_model": None, "provider_request_id": None, "input_tokens": None, "output_tokens": None,
                  "usage_complete": False, "preflight_count_kind": "unavailable", "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                  "error_code": code, "cost_state": "unknown", "actual_cost_cny": None}
        result.update(values)
        return result

    def _preflight(self, request: Mapping[str, Any]) -> str | None:
        if not (self.profile.get("enabled") and self.api_key and self.external_send_allowed):
            return "PROVIDER_NOT_CONFIGURED"
        if "text" not in self.profile.get("capabilities", ()):
            return "CAPABILITY_UNSUPPORTED"
        if self.profile.get("runtime_origin") != "CN" or self.profile.get("provider_kind") not in {"deepseek", "dashscope", "openai_compatible"}:
            return "PROVIDER_ORIGIN_DENIED"
        if request.get("model_profile_version_id") != self.profile.get("id"):
            return "PROFILE_VERSION_MISMATCH"
        if not self.reservation_validator or not self.reservation_validator(request.get("budget_reservation_id", "")):
            return "BUDGET_RESERVATION_REQUIRED"
        if not isinstance(request.get("max_output_tokens"), int) or not 1 <= request["max_output_tokens"] <= self.profile.get("max_output_tokens", 0):
            return "OUTPUT_LIMIT_INVALID"
        if request.get("output_schema_id") and ("json" not in self.profile.get("capabilities", ()) or self.output_validator is None):
            return "OUTPUT_SCHEMA_VALIDATOR_REQUIRED"
        return None

    def generate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        code = self._preflight(request)
        if code:
            return self._result(started, code=code)
        try:
            deadline = utc(request["deadline"])
        except (ValueError, KeyError, TypeError):
            return self._result(started, code="DEADLINE_INVALID")
        body = {"model": self.profile["model_id"], "messages": [{"role": m["role"], "content": m["content"]} for m in request["messages"]], "max_tokens": request["max_output_tokens"], "stream": False}
        if request.get("output_schema_id"):
            body["response_format"] = {"type": "json_object"}
        for attempt in range(self.max_retries + 1):
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                return self._result(started, code="DEADLINE_EXCEEDED")
            try:
                validate_endpoint(self.profile["base_url"], self.allowed_hosts, resolver=self.resolver)
            except PolicyError as exc:
                return self._result(started, code=str(exc))
            try:
                # 禁用重定向，避免允许的 Provider 将数据导向未批准地址。
                with httpx.Client(transport=self.transport, timeout=min(remaining, self.profile.get("timeout_seconds", 60)), follow_redirects=False) as client:
                    response = client.post(self.profile["base_url"].rstrip("/") + "/chat/completions", json=body,
                        headers={"Authorization": "Bearer " + self.api_key, "X-Request-ID": request["request_id"]})
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt < self.max_retries and self._backoff(attempt, deadline):
                    continue
                return self._result(started, code="CONNECTION_FAILED")
            except httpx.TimeoutException:
                # 请求已发送后的超时可能计费，不重发，保留未知消费。
                return self._result(started, status="unknown", code="PROVIDER_TIMEOUT")
            except httpx.HTTPError:
                return self._result(started, status="unknown", code="PROVIDER_INTERRUPTED")
            if datetime.now(timezone.utc) >= deadline:
                return self._result(started, status="unknown", code="DEADLINE_EXCEEDED")
            if response.status_code in {429, 502, 503, 504} and attempt < self.max_retries and self._backoff(attempt, deadline):
                continue
            if response.status_code != 200:
                code = {401: "AUTHENTICATION_FAILED", 403: "PERMISSION_DENIED", 429: "RATE_LIMITED"}.get(response.status_code,
                        "PROVIDER_UNAVAILABLE" if response.status_code >= 500 else "PROVIDER_HTTP_ERROR")
                return self._result(started, code=code, provider_request_id=response.headers.get("x-request-id"))
            return self._decode(response, request, started)
        return self._result(started, code="RETRY_EXHAUSTED")

    def _backoff(self, attempt: int, deadline: datetime) -> bool:
        delay = 0.05 * (2 ** attempt)
        if (deadline - datetime.now(timezone.utc)).total_seconds() <= delay:
            return False
        self.sleeper(delay)
        return True

    def _decode(self, response: httpx.Response, request: Mapping[str, Any], started: float) -> dict[str, Any]:
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            text = message.get("content") or ""
            if not isinstance(text, str):
                raise ValueError("content is not text")
            status, code = "completed", None
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                status, code = "refused", "MODEL_REFUSED"
            elif choice.get("finish_reason") == "length":
                status, code = "incomplete", "OUTPUT_TRUNCATED"
            elif not text.strip():
                status, code = "failed", "EMPTY_OUTPUT"
            elif choice.get("finish_reason") not in {"stop", "tool_calls"}:
                status, code = "incomplete", "FINISH_REASON_UNKNOWN"
            if status == "completed" and request.get("output_schema_id"):
                try:
                    self.output_validator(request["output_schema_id"], json.loads(text))
                except Exception:
                    status, code = "failed", "OUTPUT_SCHEMA_INVALID"
            usage = body.get("usage") or {}
            incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
            complete = all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (incoming, outgoing))
            values: dict[str, Any] = {"text": text, "actual_model": body.get("model") or None, "provider_request_id": body.get("id") or response.headers.get("x-request-id"),
                                      "input_tokens": incoming if complete else None, "output_tokens": outgoing if complete else None, "usage_complete": complete}
            # 只计算服务端总输入/输出，不叠加缓存与推理子项。
            prices = (self.profile.get("price_input_per_million_cny"), self.profile.get("price_output_per_million_cny"))
            if complete and all(price is not None for price in prices):
                cost = (Decimal(incoming) * Decimal(prices[0]) + Decimal(outgoing) * Decimal(prices[1])) / Decimal(1_000_000)
                values.update(cost_state="actual", actual_cost_cny=format(cost.quantize(Decimal("0.000001")), "f"))
            return self._result(started, status=status, code=code, **values)
        except (ValueError, KeyError, TypeError, IndexError):
            return self._result(started, code="PROVIDER_RESPONSE_INVALID")

    def embed(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return {"status": "blocked", "error_code": "EMBEDDING_ADAPTER_NOT_CONFIGURED", "usage": None}

    def rerank(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return {"status": "blocked", "error_code": "RERANK_ADAPTER_NOT_CONFIGURED", "usage": None}
