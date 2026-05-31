"""Tests for the runnable parts of the Fabric deployment wiring (no network/Fabric needed)."""
import pytest

from banking_agents.config import resolve_secret
from banking_agents.mcp import MCPClient, RemoteMCP
from banking_agents.model import GeminiProvider, get_provider
from banking_agents.config import ModelConfig


def test_resolve_secret_env_and_literal(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "shhh")
    assert resolve_secret("env://MY_SECRET") == "shhh"
    assert resolve_secret("literal-value") == "literal-value"


def test_resolve_secret_missing_env_is_clear(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(KeyError):
        resolve_secret("env://NOPE")


def test_kv_ref_falls_back_to_env_locally(monkeypatch):
    monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "local-key")
    assert resolve_secret("kv://banking-ai/model-api-key") == "local-key"


def test_gemini_provider_is_registered_and_lazy(monkeypatch):
    # Construction must NOT require the key (ETL runs offline); only complete() needs it.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    p = get_provider(ModelConfig(name="gemini-2.5-flash", provider="gemini",
                                 key_ref="env://GEMINI_API_KEY", region="eu"))
    assert isinstance(p, GeminiProvider)


def test_remotemcp_satisfies_the_mcp_interface(monkeypatch):
    monkeypatch.setenv("FABRIC_MCP_TOKEN", "t")
    client = RemoteMCP(base_url="https://example.invalid", token_ref="env://FABRIC_MCP_TOKEN")
    assert isinstance(client, MCPClient)          # same seam as LocalFilesystemMCP
    assert hasattr(client, "write") and hasattr(client, "read")
