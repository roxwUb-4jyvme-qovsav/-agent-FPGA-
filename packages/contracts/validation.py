"""离线解析 JSON Schema，结构错误与领域规则分别报告。"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent


class ContractError(ValueError):
    def __init__(self, fields: list[dict[str, str]]):
        self.fields = fields
        super().__init__("输入不符合冻结契约")


@lru_cache(maxsize=1)
def schema_registry() -> tuple[dict[str, Any], Registry]:
    schemas = {
        p.stem.removesuffix(".schema"): json.loads(p.read_text("utf-8"))
        for p in (ROOT / "schemas").glob("*.json")
    }
    registry = Registry().with_resources(
        (value["$id"], Resource.from_contents(value)) for value in schemas.values()
    )
    return schemas, registry


def validate_payload(name: str, payload: Any) -> None:
    schemas, registry = schema_registry()
    validator = Draft202012Validator(
        schemas[name], registry=registry, format_checker=FormatChecker()
    )
    errors = sorted(validator.iter_errors(payload), key=lambda e: str(list(e.path)))
    if errors:
        raise ContractError(
            [{"field": ".".join(map(str, e.path)), "message": e.message} for e in errors[:20]]
        )


def reference_json(path: str) -> Any:
    """只允许代码内固定相对引用，HTTP 输入不能传入这个入口。"""
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError("契约路径越界")
    return json.loads(target.read_text("utf-8"))


def validate_api_schema(name: str, payload: Any) -> None:
    document = reference_json("api/openapi.json")
    schema = {"$ref": "#/components/schemas/" + name, "components": document["components"]}
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
    if errors:
        raise ContractError(
            [{"field": ".".join(map(str, e.path)), "message": e.message} for e in errors[:20]]
        )
