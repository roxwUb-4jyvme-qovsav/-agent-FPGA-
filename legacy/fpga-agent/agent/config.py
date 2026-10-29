"""Validate frozen Skill v4 and v2.4 execution budgets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def load_agent_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("agent_version") != "2.4" or data.get("schema_version") != 6:
        raise ValueError("Expected Agent v2.4 release configuration")
    for key in ("max_rounds", "max_total_tokens", "max_eda_calls", "min_completion_tokens"):
        if isinstance(data.get(key), bool) or not isinstance(data.get(key), int) or data[key] <= 0:
            raise ValueError(f"Invalid {key}")
    if data["max_rounds"] > 8 or data["min_completion_tokens"] >= data["max_total_tokens"]:
        raise ValueError("Inconsistent Agent budgets")
    if not isinstance(data.get("max_wall_time_s"), (int, float)) or data["max_wall_time_s"] <= 0:
        raise ValueError("Invalid max_wall_time_s")
    skill = (path.parent / data["skill_path"]).resolve()
    if not skill.is_file() or hashlib.sha256(skill.read_bytes()).hexdigest() != data["expected_skill_sha256"]:
        raise ValueError("Frozen Skill v4 SHA256 mismatch")
    data["skill_path"] = str(skill)
    return data
