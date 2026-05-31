"""Tests for the agentic decision layer (Supervisor + Initiator integration).

Uses a scripted fake provider so each decision branch is deterministic and offline.
A CorruptingConnector forces a real Bronze QA hard-fail (duplicate txn_id) so the
Supervisor is actually consulted.
"""
from datetime import date

from banking_agents.agents.supervisor import Decision, SupervisorAgent
from banking_agents.config import ModelConfig, load_source_registry
from banking_agents.connectors import SyntheticConnector
from banking_agents.mcp import LocalFilesystemMCP
from banking_agents.model import ModelProvider, ModelResponse
from banking_agents.orchestration import RunManifest
from banking_agents.orchestration.initiator import Initiator

D1 = date(2026, 5, 28)


class ScriptedProvider(ModelProvider):
    """Returns a fixed JSON decision (or a queue of them) — no network."""
    def __init__(self, *responses: str):
        super().__init__(ModelConfig(name="scripted", provider="stub", key_ref="none", region="eu"))
        self._responses = list(responses)

    def complete(self, system, messages):
        text = self._responses.pop(0) if self._responses else '{"action":"halt","reason":"default"}'
        return ModelResponse(text=text)


class CorruptingConnector(SyntheticConnector):
    def read(self, source_cfg, as_of):
        df = super().read(source_cfg, as_of)
        if source_cfg.domain == "transactions":
            df = df.copy()
            df.loc[df.index[1], "txn_id"] = df.loc[df.index[0], "txn_id"]  # duplicate -> hard fail
        return df


def _initiator(tmp_path, provider):
    mcp = LocalFilesystemMCP(tmp_path / "lake")
    reg = load_source_registry()
    init = Initiator(mcp, reg, model=provider, seed=42)
    init.connector = CorruptingConnector(reg, seed=42)
    return init, mcp


# -- unit: the Supervisor itself ------------------------------------------------
def _manifest():
    return RunManifest.new(trigger="t", window_from=D1, window_to=D1, sources=[], stages=["x"])


class _FailRes:
    def __init__(self, check, detail): self.check, self.detail, self.outcome = check, detail, "fail"


def test_supervisor_parses_each_action():
    for act in ("halt", "retry", "proceed", "escalate"):
        s = SupervisorAgent(ScriptedProvider(f'{{"action":"{act}","reason":"x"}}'))
        d = s.decide(_manifest(), "bronze", [_FailRes("unique[id]", "1 dup")])
        assert d.action == act


def test_supervisor_falls_back_to_halt_without_model():
    s = SupervisorAgent(model=None)
    assert s.decide(_manifest(), "bronze", [_FailRes("x", "y")]).action == "halt"


def test_supervisor_falls_back_on_garbage_response():
    s = SupervisorAgent(ScriptedProvider("I think you should maybe retry?"))
    assert s.decide(_manifest(), "bronze", [_FailRes("x", "y")]).action == "halt"


def test_supervisor_caps_retries():
    s = SupervisorAgent(ScriptedProvider('{"action":"retry","reason":"again"}'))
    d = s.decide(_manifest(), "bronze", [_FailRes("x", "y")], retries_so_far=1)
    assert d.action == "escalate"  # retry budget exhausted


# -- integration: Initiator acts on the decision -------------------------------
def test_halt_decision_stops_the_run(tmp_path):
    init, mcp = _initiator(tmp_path, ScriptedProvider('{"action":"halt","reason":"integrity"}'))
    m = init.run(as_of=D1)
    assert m.status == "failed"
    assert m.stages["extract"] == "failed" and m.stages["transform"] == "pending"
    assert not any(t.startswith("silver.") for t in mcp.list_tables())


def test_escalate_decision_records_flag(tmp_path):
    init, mcp = _initiator(tmp_path, ScriptedProvider('{"action":"escalate","reason":"systemic"}'))
    m = init.run(as_of=D1)
    assert m.status == "failed"
    assert any(a.action == "escalate" for a in m.audit)


def test_retry_then_recover_proceeds(tmp_path):
    """First gate fails (corrupt), supervisor says retry; we 'fix' the source before the
    re-run so the second gate passes and the pipeline proceeds."""
    init, mcp = _initiator(tmp_path, ScriptedProvider('{"action":"retry","reason":"transient"}'))
    # After the supervisor asks to retry, swap the corrupting connector for a clean one so
    # the re-run lands valid data (simulates a transient issue clearing).
    clean = SyntheticConnector(load_source_registry(), seed=42)
    orig = init.connector.read
    def read_then_heal(cfg, as_of):
        init.connector.read = clean.read   # heal after the first (corrupt) extract
        return orig(cfg, as_of)
    init.connector.read = read_then_heal

    m = init.run(as_of=D1)
    assert m.status == "success"
    assert m.stages["extract"] == "passed"
    assert mcp.table_exists("gold.kpi_daily")


def test_no_model_keeps_deterministic_halt(tmp_path):
    init, mcp = _initiator(tmp_path, None)  # no model -> deterministic
    m = init.run(as_of=D1)
    assert m.status == "failed"
    assert m.stages["transform"] == "pending"
