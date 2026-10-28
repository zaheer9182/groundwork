"""Inbound token validation and on-behalf-of exchange.

The client (SPA, Teams tab, anything with MSAL) gets a token for this API's
delegated scope and sends it as a Bearer token. We validate it, then trade it
on-behalf-of the user for a delegated Graph token. Graph calls then run as the
user, so Microsoft 365 does the permission trimming.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass

import jwt
import msal

GRAPH_SCOPES = (
    "https://graph.microsoft.com/Files.Read.All",
    "https://graph.microsoft.com/Sites.Read.All",
)


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401, code: str = "unauthorized"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Principal:
    assertion: str      # raw inbound token, the OBO user assertion
    oid: str
    tenant_id: str
    display_name: str

    @property
    def pseudonym(self) -> str:
        """Stable id for logs that cannot be reversed to the user."""
        return hashlib.sha256(f"{self.tenant_id}:{self.oid}".encode()).hexdigest()[:16]


class TokenValidator:
    def __init__(self, tenant_id: str, client_id: str, scope: str = "access_as_user", jwks_client=None):
        self.tenant_id = tenant_id
        self.scope = scope
        self.audiences = [client_id, f"api://{client_id}"]
        self.issuers = [
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://sts.windows.net/{tenant_id}/",
        ]
        self._jwks = jwks_client or jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True, lifespan=3600
        )

    def validate(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError("Missing bearer token")
        token = authorization[7:].strip()
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.audiences, issuer=self.issuers,
                options={"require": ["exp", "iat", "aud", "iss", "oid", "tid"]}, leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"Invalid token ({exc.__class__.__name__})") from exc

        if self.scope not in claims.get("scp", "").split():
            # App-only tokens carry `roles` and no `scp`. This API serves people, not daemons.
            raise AuthError("Delegated scope missing", status=403, code="forbidden")
        if claims["tid"] != self.tenant_id:
            raise AuthError("Token from another tenant", status=403, code="forbidden")
        return Principal(token, claims["oid"], claims["tid"], claims.get("name", ""))


class OnBehalfOf:
    """Wraps MSAL. Its in-memory cache is keyed by assertion, so a warm instance
    reuses the Graph token for the same user until it nears expiry."""

    def __init__(self, tenant_id: str, client_id: str, credential: str | dict, app=None):
        self._app = app or msal.ConfidentialClientApplication(
            client_id, authority=f"https://login.microsoftonline.com/{tenant_id}", client_credential=credential
        )
        self._lock = threading.Lock()

    def graph_token(self, principal: Principal) -> str:
        with self._lock:
            result = self._app.acquire_token_on_behalf_of(principal.assertion, list(GRAPH_SCOPES))
        if "access_token" in result:
            return result["access_token"]
        error = result.get("error", "unknown_error")
        if error in ("interaction_required", "consent_required", "invalid_grant"):
            # The client should run interactive auth (claims challenge / consent) and retry.
            raise AuthError(result.get("error_description", error)[:200], status=401, code="interaction_required")
        raise AuthError(f"On-behalf-of failed: {error}", status=502, code="obo_failed")
