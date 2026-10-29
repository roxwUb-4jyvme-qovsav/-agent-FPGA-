"""Represent the official RTL L0-L3 ladder without confusing self-checks with L2."""
from __future__ import annotations

COEFFICIENT = {0: 0.0, 1: 0.2, 2: 0.7, 3: 1.0}
ENVIRONMENT_VERDICTS = frozenset({
    "LICENSE_ERROR", "PART_NOT_LICENSED", "FIXTURE_ERROR", "JUDGE_ERROR",
})


def official_result(verdict: dict | None = None, *, candidate_checks: dict | None = None) -> dict:
    """Only a trusted official-compatible judge verdict can establish L2/L3.

    Candidate-only compile, self-test and synthesis are development evidence,
    never a substitute for the organizer's functional testbench.
    """
    checks = candidate_checks or {}
    result = {
        "level": None, "coefficient": None, "source": "unscored_candidate_checks",
        "stages": {"compile": None, "simulate": None, "synth": None},
        "candidate_evidence": checks,
        "functional_pass_proven": False,
        "note": "EDA_PASS and SYNTH_PASS do not imply FUNCTION_PASS",
    }
    if verdict is None:
        return result
    if verdict.get("verdict") in ENVIRONMENT_VERDICTS or verdict.get("tool_error"):
        result.update(source="official_judge", tool_error=verdict.get("verdict") or verdict["tool_error"])
        return result
    level = verdict.get("level")
    if isinstance(level, bool) or level not in COEFFICIENT:
        raise ValueError("Official judge verdict requires level 0..3")
    result.update(level=level, coefficient=COEFFICIENT[level], source="official_judge",
                  stages={"compile": level >= 1, "simulate": level >= 2,
                          "synth": level >= 3}, functional_pass_proven=level >= 2)
    return result
