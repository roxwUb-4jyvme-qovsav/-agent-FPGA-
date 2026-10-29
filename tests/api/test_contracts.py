from .test_security import open_client, setup, write


def test_invalid_request_error_matches_frozen_api_contract(tmp_path):
    from packages.contracts.validation import validate_payload

    with open_client(tmp_path) as client:
        csrf = setup(client)
        response = write(client, "post", "/projects", {}, csrf)
        assert response.status_code == 422
        validate_payload("ApiError", response.json())
