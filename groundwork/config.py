"""Settings, read once from the environment.

In Azure, secrets are Key Vault references in app settings, so code only
ever sees environment variables and never talks to Key Vault directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # Identity
    tenant_id: str = ""
    api_client_id: str = ""
    api_client_secret: str = ""
    api_scope: str = "access_as_user"

    # Where documents come from: "graph" in Azure, "local" for development
    source: str = "graph"
    local_root: str = "evals/corpus"
    # Optional KQL appended to every Graph search, e.g. path:"https://contoso.sharepoint.com/sites/HR"
    search_scope: str = ""
    max_documents: int = 8

    # Retrieval
    top_k: int = 6
    min_coverage: float = 0.34    # below this share of query terms in the best chunk, refuse without calling the model
    chunk_chars: int = 1200
    chunk_overlap: int = 200

    # Model
    provider: str = "anthropic"          # anthropic | azure_openai
    model: str = "claude-sonnet-5-5"
    max_output_tokens: int = 800
    history_turns: int = 6

    # Assistant identity, so one deployment can serve HR, IT, Finance...
    assistant_name: str = "the company knowledge assistant"
    refusal_text: str = "I could not find this in the documents you have access to."

    allowed_origins: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ.get
        return cls(
            tenant_id=e("GW_TENANT_ID", ""),
            api_client_id=e("GW_API_CLIENT_ID", ""),
            api_client_secret=e("GW_API_CLIENT_SECRET", ""),
            api_scope=e("GW_API_SCOPE", "access_as_user"),
            source=e("GW_SOURCE", "graph"),
            local_root=e("GW_LOCAL_ROOT", "evals/corpus"),
            search_scope=e("GW_SEARCH_SCOPE", ""),
            max_documents=_int("GW_MAX_DOCUMENTS", 8),
            top_k=_int("GW_TOP_K", 6),
            min_coverage=_float("GW_MIN_COVERAGE", 0.34),
            chunk_chars=_int("GW_CHUNK_CHARS", 1200),
            chunk_overlap=_int("GW_CHUNK_OVERLAP", 200),
            provider=e("GW_PROVIDER", "anthropic"),
            model=e("GW_MODEL", "claude-sonnet-5-5"),
            max_output_tokens=_int("GW_MAX_OUTPUT_TOKENS", 800),
            history_turns=_int("GW_HISTORY_TURNS", 6),
            assistant_name=e("GW_ASSISTANT_NAME", "the company knowledge assistant"),
            refusal_text=e("GW_REFUSAL_TEXT", "I could not find this in the documents you have access to."),
            allowed_origins=[o.strip() for o in e("GW_ALLOWED_ORIGINS", "").split(",") if o.strip()],
        )
