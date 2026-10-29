from copy import deepcopy
from uuid import UUID

import pytest


def uid(number):
    return str(UUID(int=number))


def evidence():
    return {"schema_version": "1.0", "id": uid(1), "project_id": uid(2),
            "revision_id": uid(3), "task_id": uid(4), "test_id": "public-protocol",
            "input_sha256": "a" * 64, "suite_sha256": "b" * 64,
            "environment_sha256": "c" * 64, "tool_version": "pytest-public",
            "template_id": "public", "seed": 4294967295,
            "status": "PASS", "mode": "static", "exit_code": 0,
            "started_at": "2026-10-29T00:00:00Z", "finished_at": "2026-10-29T00:01:00Z",
            "report": {"id": uid(5), "sha256": "d" * 64, "kind": "report"}, "issuer_id": uid(6)}


def credentials():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from acceptance_service.verification import ApprovedKey

    private = Ed25519PrivateKey.generate()
    return private, [ApprovedKey(uid(6), "public-key-1", private.public_key())]


def test_jcs_utf16_key_order_and_ecmascript_number_format():
    from acceptance_service.signing import canonical_bytes

    assert canonical_bytes({"\ue000": -0.0, "😀": 1e-7, "a": 1e20}) == '{"a":100000000000000000000,"😀":1e-7,"\ue000":0}'.encode()
    with pytest.raises(ValueError):
        canonical_bytes({"n": float("nan")})


def test_ed25519_signing_is_detached_from_auth_tag_and_never_mutates_input():
    from acceptance_service.signing import sign_evidence
    from acceptance_service.verification import verify_evidence

    private, keys = credentials()
    original = evidence()
    signed = sign_evidence(original, private, "public-key-1")
    assert "auth_tag" not in original
    assert signed["auth_tag"]["algorithm"] == "Ed25519"
    assert len(signed["auth_tag"]["signature"]) == 86 and "=" not in signed["auth_tag"]["signature"]
    verify_evidence(signed, keys)
    reversed_fields = dict(reversed(list(signed.items())))
    verify_evidence(reversed_fields, keys)


@pytest.mark.parametrize("field,value", [("status", "FAIL"), ("seed", 1), ("revision_id", uid(22)), ("report", {"id": uid(5), "sha256": "e" * 64, "kind": "report"})])
def test_signed_evidence_rejects_tampering_of_every_bound_fact(field, value):
    from acceptance_service.signing import sign_evidence
    from acceptance_service.verification import EvidenceError, verify_evidence

    private, keys = credentials()
    signed = sign_evidence(evidence(), private, "public-key-1")
    signed[field] = value
    with pytest.raises(EvidenceError):
        verify_evidence(signed, keys)


def test_unapproved_key_revocation_and_expected_binding_rejected():
    from acceptance_service.signing import sign_evidence
    from acceptance_service.verification import ApprovedKey, EvidenceError, verify_evidence

    private, keys = credentials()
    signed = sign_evidence(evidence(), private, "public-key-1")
    with pytest.raises(EvidenceError):
        verify_evidence(signed, [])
    revoked = [ApprovedKey(uid(6), "public-key-1", private.public_key(), revoked_at="2026-10-28T00:00:00Z")]
    with pytest.raises(EvidenceError):
        verify_evidence(signed, revoked)
    verify_evidence(signed, keys, expected={"task_id": uid(4), "report_sha256": "d" * 64})
    with pytest.raises(EvidenceError):
        verify_evidence(signed, keys, expected={"input_sha256": "e" * 64})


@pytest.mark.parametrize("seed", [-1, 4294967296, True, 1.5])
def test_signing_rejects_non_uint32_seed(seed):
    from acceptance_service.signing import sign_evidence

    private, _ = credentials()
    original = evidence()
    original["seed"] = seed
    with pytest.raises(ValueError):
        sign_evidence(original, private, "public-key-1")


def test_projection_marks_dependency_change_stale_without_rewriting_signed_pass():
    from acceptance_service.signing import sign_evidence
    from acceptance_service.verification import evaluate_validity

    private, keys = credentials()
    signed = sign_evidence(evidence(), private, "public-key-1")
    before = deepcopy(signed)
    valid = evaluate_validity(signed, keys, current_revision_id=uid(3), expected={"input_sha256": "a" * 64})
    assert valid["effective_status"] == "PASS" and valid["signature_valid"]
    stale = evaluate_validity(signed, keys, current_revision_id=uid(7), expected={"input_sha256": "e" * 64})
    assert stale["effective_status"] == "STALE" and stale["signature_valid"]
    assert stale["stale_reason"] and signed == before
    signed["status"] = "FAIL"
    invalid = evaluate_validity(signed, keys, current_revision_id=uid(3))
    assert not invalid["signature_valid"] and invalid["effective_status"] == "INDETERMINATE"


def test_mock_or_replay_pass_cannot_become_release_pass():
    from acceptance_service.signing import sign_evidence
    from acceptance_service.verification import evaluate_validity

    private, keys = credentials()
    for mode in ("mock", "replay"):
        original = evidence()
        original["mode"] = mode
        signed = sign_evidence(original, private, "public-key-1")
        result = evaluate_validity(signed, keys, current_revision_id=uid(3))
        assert result["effective_status"] == "INDETERMINATE"


def test_immutable_evidence_registry_deduplicates_exact_content_and_rejects_id_reuse():
    from acceptance_service.signing import sign_evidence
    from acceptance_service.verification import EvidenceError, EvidenceRegistry

    private, keys = credentials()
    signed = sign_evidence(evidence(), private, "public-key-1")
    registry = EvidenceRegistry()
    registry.register(signed, keys)
    registry.register(deepcopy(signed), keys)
    returned = registry.get(signed["id"])
    returned["status"] = "FAIL"
    assert registry.get(signed["id"])["status"] == "PASS"
    different = evidence()
    different["seed"] = 2
    with pytest.raises(EvidenceError):
        registry.register(sign_evidence(different, private, "public-key-1"), keys)


def test_reuse_requires_complete_matching_dependency_binding_and_explicit_approval():
    from acceptance_service.signing import sign_evidence
    from acceptance_service.verification import evaluate_validity

    private, keys = credentials()
    original = evidence()
    signed = sign_evidence(original, private, "public-key-1")
    incomplete = evaluate_validity(signed, keys, current_revision_id=uid(7), reuse_approved=True)
    assert incomplete["effective_status"] == "STALE"
    expected = {name: original[name] for name in ("input_sha256", "suite_sha256", "environment_sha256", "tool_version", "template_id")}
    expected["report_sha256"] = "d" * 64
    complete = evaluate_validity(signed, keys, current_revision_id=uid(7), expected=expected, reuse_approved=True)
    assert complete["effective_status"] == "PASS"


def test_key_rotation_preserves_historical_evidence_before_revocation():
    from acceptance_service.signing import sign_evidence
    from acceptance_service.verification import ApprovedKey, verify_evidence

    private, _ = credentials()
    signed = sign_evidence(evidence(), private, "public-key-1")
    keys = [ApprovedKey(uid(6), "public-key-1", private.public_key(), revoked_at="2026-10-30T00:00:00Z")]
    verify_evidence(signed, keys)
