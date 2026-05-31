"""Configuration loading: model access + the source registry.

Worker agents are *configured dynamically* from source-registry entries
(docs/ARCHITECTURE.md §3.1) so the same code handles any domain.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Repo root = three levels up from this file (src/banking_agents/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no dependency). Populates os.environ if not already set."""
    path = path or REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv()  # make .env secrets available on import


def resolve_secret(key_ref: str) -> str:
    """Resolve a key reference to its value.

    - ``env://VAR``      -> os.environ[VAR]              (local dev)
    - ``kv://path``      -> Key Vault in Fabric; locally falls back to GEMINI/GOOGLE env vars
    - anything else      -> treated as a literal value
    """
    if key_ref.startswith("env://"):
        var = key_ref[len("env://"):]
        if var not in os.environ:
            raise KeyError(f"Env var '{var}' not set (referenced by {key_ref}). Add it to .env.")
        return os.environ[var]
    if key_ref.startswith("kv://"):
        secret_name = key_ref[len("kv://"):].split("/")[-1]
        # In Fabric/Azure: fetch from Key Vault via managed identity (docs/DEPLOYMENT.md §C).
        vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
        if vault_url:
            return _fetch_from_key_vault(vault_url, secret_name)
        # Local dev fallback: well-known env vars.
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            if var in os.environ:
                return os.environ[var]
        raise KeyError(
            f"{key_ref}: set AZURE_KEY_VAULT_URL (Fabric/Azure) or GEMINI_API_KEY in .env (local).")
    return key_ref


def _fetch_from_key_vault(vault_url: str, secret_name: str) -> str:
    """Fetch a secret from Azure Key Vault using the ambient managed identity.

    Requires `azure-identity` + `azure-keyvault-secrets` (installed in the Fabric image,
    not a local dev dependency). Imported lazily so local dev needs neither.
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as e:  # pragma: no cover - exercised only in the Fabric image
        raise RuntimeError(
            "Key Vault resolution needs azure-identity + azure-keyvault-secrets "
            "(present in the Fabric runtime). For local dev use env:// instead."
        ) from e
    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    return client.get_secret(secret_name).value


@dataclass(frozen=True)
class ModelConfig:
    name: str
    provider: str
    key_ref: str
    region: str
    max_tokens: int = 4096
    temperature: float = 0.3

    @classmethod
    def load(cls, path: Path | None = None) -> "ModelConfig":
        path = path or CONFIG_DIR / "model.yaml"
        data = yaml.safe_load(path.read_text())["model"]
        return cls(
            name=data["name"],
            provider=data["provider"],
            key_ref=data["key_ref"],
            region=data.get("region", "westeurope"),
            max_tokens=int(data.get("max_tokens", 4096)),
            temperature=float(data.get("temperature", 0.3)),
        )

    def resolve_key(self) -> str:
        return resolve_secret(self.key_ref)


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    domain: str
    connector: str
    read_mode: str
    watermark: str
    target_bronze: str
    pii_columns: list[str] = field(default_factory=list)
    contract: str | None = None
    synthetic: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        return cls(
            source_id=data["source_id"],
            domain=data["domain"],
            connector=data["connector"],
            read_mode=data["read_mode"],
            watermark=data["watermark"],
            target_bronze=data["target_bronze"],
            pii_columns=list(data.get("pii_columns", [])),
            contract=data.get("contract"),
            synthetic=dict(data.get("synthetic", {})),
        )


def load_source_registry(sources_dir: Path | None = None) -> dict[str, SourceConfig]:
    """Load every config/sources/*.yaml into a {source_id: SourceConfig} map."""
    sources_dir = sources_dir or CONFIG_DIR / "sources"
    registry: dict[str, SourceConfig] = {}
    for path in sorted(sources_dir.glob("*.yaml")):
        cfg = SourceConfig.from_dict(yaml.safe_load(path.read_text()))
        registry[cfg.source_id] = cfg
    return registry
