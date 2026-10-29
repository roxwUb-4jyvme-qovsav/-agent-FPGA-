"""适配器返回值的宿主校验；未知代码不进入 API 主进程。"""
from copy import deepcopy
from typing import Any, Callable, Mapping

from packages.contracts.validation import ContractError, validate_payload


class SDKError(ValueError):
    pass


def validate_adapter_response(response: Mapping[str, Any], *, expected_invocation_id: str,
                              expected_output_schema: str,
                              output_validator: Callable[[str, Any], None]) -> dict[str, Any]:
    try:
        validate_payload("AdapterResponse", response)
    except (ContractError, TypeError, KeyError) as exc:
        raise SDKError("ADAPTER_RESPONSE_INVALID") from exc
    if response["invocation_id"] != expected_invocation_id:
        raise SDKError("ADAPTER_INVOCATION_MISMATCH")
    if response["output_schema"] != expected_output_schema:
        raise SDKError("ADAPTER_OUTPUT_SCHEMA_MISMATCH")
    if response["status"] == "completed":
        if response["output"] is None:
            raise SDKError("ADAPTER_COMPLETED_WITHOUT_OUTPUT")
        try:
            output_validator(expected_output_schema, response["output"])
        except Exception as exc:
            raise SDKError("ADAPTER_OUTPUT_INVALID") from exc
    return deepcopy(dict(response))
