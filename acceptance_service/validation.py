"""Validate the frozen evidence schema without trusting a generator's claims."""
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires a timezone")
    return parsed


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    folder = Path(__file__).resolve().parents[1] / "packages/contracts/schemas"
    documents = [json.loads((folder / name).read_text(encoding="utf-8")) for name in ("ArtifactRef.schema.json", "VerificationEvidence.schema.json")]
    registry = Registry().with_resources((item["$id"], Resource.from_contents(item)) for item in documents)
    return Draft202012Validator(documents[-1], registry=registry, format_checker=FormatChecker())


def validate_evidence(evidence: Mapping[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(evidence), key=lambda error: str(error.path))
    if errors:
        raise ValueError(f"invalid evidence: {errors[0].message}")
    if type(evidence["seed"]) is not int:
        raise ValueError("seed must be a uint32 integer")
    if timestamp(evidence["finished_at"]) < timestamp(evidence["started_at"]):
        raise ValueError("finished_at precedes started_at")
    if evidence["status"] == "PASS" and evidence["exit_code"] != 0:
        raise ValueError("PASS requires exit_code 0")
