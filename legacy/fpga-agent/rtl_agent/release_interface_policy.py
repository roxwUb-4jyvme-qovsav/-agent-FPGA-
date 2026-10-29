"""Competition policy: prompt-derived port lists are advisory, not an oracle.

Only this release copy applies the policy. The development v2.3 and Skill v4
remain byte-for-byte frozen. A public prose prompt can contain an intentionally
broken example header or an erroneous port direction; the official example
records such a case. Missing/duplicate top and explicitly required submodules
remain blocking structural errors.
"""
from __future__ import annotations

from .interface_contract import check_interface_contract as _strict_check

PORT_CODES = frozenset({
    "PORT_MISSING", "PORT_UNEXPECTED", "PORT_DIRECTION_MISMATCH",
    "PORT_WIDTH_MISMATCH", "PORT_SIGNED_MISMATCH",
})


def check_interface_contract(contract: dict, candidate: str) -> dict:
    report = _strict_check(contract, candidate)
    if (contract.get("source") != "public_spec_only" or
            contract.get("port_source_format") != "port_bullets"):
        return report
    advisory = [issue for issue in report["issues"] if issue["code"] in PORT_CODES]
    if not advisory:
        return report
    report["issues"] = [issue for issue in report["issues"] if issue["code"] not in PORT_CODES]
    report["advisories"] = advisory
    report["skipped_checks"].append(
        "Port differences inferred from public prose are advisory; no hidden interface oracle was used")
    report["status"] = "FAIL" if report["issues"] else "INDETERMINATE"
    return report
