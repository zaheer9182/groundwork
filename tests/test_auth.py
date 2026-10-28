from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from groundwork.auth import GRAPH_SCOPES, AuthError, OnBehalfOf, Principal, TokenValidator

TENANT = "8f1c2d3e-0000-4000-8000-000000000001"
API = "5a6b7c8d-0000-4000-8000-000000000002"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": KEY.public_key()})


@pytest.fixture
def validator():
    return TokenValidator(TENANT, API, jwks_client=FakeJwks())


def bearer(key=KEY, **override):
    now = int(time.time())
    claims = {"aud": f"api://{API}", "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "tid": TENANT,
              "oid": "0b1c", "scp": "access_as_user User.Read", "name": "Dana", "iat": now, "exp": now + 900}
    claims.update(override)
    claims = {k: v for k, v in claims.items() if v is not None}
    return "Bearer " + jwt.encode(claims, key, algorithm="RS256")


def test_accepts_delegated_user_token(validator):
    p = validator.validate(bearer())
    assert p.oid == "0b1c" and p.display_name == "Dana"
    assert len(p.pseudonym) == 16 and "0b1c" not in p.pseudonym


def test_accepts_v1_issuer_and_bare_client_id_audience(validator):
    assert validator.validate(bearer(iss=f"https://sts.windows.net/{TENANT}/", aud=API)).oid == "0b1c"


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer not-a-jwt"])
def test_rejects_missing_or_malformed(validator, header):
    with pytest.raises(AuthError) as e:
        validator.validate(header)
    assert e.value.status == 401


@pytest.mark.parametrize("override,status", [
    ({"aud": "api://another-api"}, 401),
    ({"iss": "https://login.microsoftonline.com/evil/v2.0"}, 401),
    ({"exp": int(time.time()) - 3600}, 401),
    ({"oid": None}, 401),
    ({"scp": None, "roles": ["Files.Read.All"]}, 403),       # app-only token
    ({"scp": "User.Read"}, 403),                              # token for the wrong scope
])
def test_rejects_bad_claims(validator, override, status):
    with pytest.raises(AuthError) as e:
        validator.validate(bearer(**override))
    assert e.value.status == status


def test_rejects_wrong_signature(validator):
    with pytest.raises(AuthError):
        validator.validate(bearer(key=OTHER_KEY))


class FakeMsal:
    def __init__(self, result):
        self.result, self.calls = result, []

    def acquire_token_on_behalf_of(self, assertion, scopes):
        self.calls.append((assertion, scopes))
        return self.result


PRINCIPAL = Principal("user-assertion", "oid", TENANT, "Dana")


def test_obo_requests_delegated_graph_scopes_with_user_assertion():
    msal = FakeMsal({"access_token": "graph-token"})
    assert OnBehalfOf(TENANT, API, "s", app=msal).graph_token(PRINCIPAL) == "graph-token"
    assertion, scopes = msal.calls[0]
    assert assertion == "user-assertion"
    assert set(scopes) == set(GRAPH_SCOPES)
    assert not any(s.endswith("/.default") for s in scopes), "never fall back to app permissions"


@pytest.mark.parametrize("error,status,code", [
    ("interaction_required", 401, "interaction_required"),
    ("consent_required", 401, "interaction_required"),
    ("invalid_client", 502, "obo_failed"),
])
def test_obo_errors_map_to_actionable_responses(error, status, code):
    with pytest.raises(AuthError) as e:
        OnBehalfOf(TENANT, API, "s", app=FakeMsal({"error": error, "error_description": "AADSTS"})).graph_token(PRINCIPAL)
    assert (e.value.status, e.value.code) == (status, code)
