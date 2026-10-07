"""Strict optional-tool routing, no live providers or private profile access."""
from types import SimpleNamespace
import json

import pytest

from review_ledger import tools
from review_ledger.critic import CriticConfig


class Context:
    def __init__(self, directory, **config):
        self.state = SimpleNamespace(data_dir=directory)
        self.config = config
        self.llm_accesses = 0
        self.registered = []

    def get_config(self, name, default=None):
        return self.config.get(name, default)

    @property
    def llm(self):
        self.llm_accesses += 1
        raise AssertionError("Optional host LLM must not be accessed")

    def register_tool(self, **kwargs):
        self.registered.append(kwargs)

    def register_skill(self, *args):
        pass

    def register_cli_command(self, **kwargs):
        pass


@pytest.fixture
def context(tmp_path, monkeypatch, ledger):
    context = Context(tmp_path)
    monkeypatch.setattr(tools, "ledger_for_context", lambda ctx: ledger)
    return context


def invoke(context, args, actor):
    return json.loads(tools.handle(context, "ledger_critic", args, session_id=actor.session_id))


def test_registration_adds_exactly_one_tool_without_llm(tmp_path):
    context = Context(tmp_path)
    tools.register(context)
    assert len(context.registered) == 8
    assert {tool["name"] for tool in context.registered} == set(tools.TOOL_NAMES)
    assert context.llm_accesses == 0
    assert len(tools.SCHEMAS["ledger_critic"]["parameters"]["oneOf"]) == 4


def test_config_defaults_match_manifest(context):
    config = CriticConfig.from_context(context)
    assert config.enabled is False
    assert not config.authorized_repositories
    assert config.provider == config.model == ""
    assert (config.max_input_chars, config.max_output_tokens, config.timeout_seconds,
            config.max_calls_per_run) == (18000, 2000, 60, 3)
    assert context.llm_accesses == 0


def test_disabled_run_never_initializes_host(context, opened, actor):
    result = invoke(context, {"action": "run", "repository": "synthetic/example",
                    "run_id": opened["id"], "generation": opened["generation"],
                    "request_key": "disabled", "critic_run_id": "no-call"}, actor)
    assert result["error"]["code"] == "critic_disabled"
    assert context.llm_accesses == 0


def test_normal_review_remains_available_without_llm(context, opened, actor):
    result = json.loads(tools.handle(context, "ledger_status",
                        {"repository": "synthetic/example", "run_id": opened["id"]},
                        session_id=actor.session_id))
    assert result["state"] != "error"
    assert context.llm_accesses == 0


@pytest.mark.parametrize("action,extra", [
    ("status", {"generation": 1}), ("status", {"request_key": "forbidden"}),
    ("status", {"finding_ids": ["f"]}), ("prepare", {"critic_run_id": "c"}),
    ("run", {"observation_ids": []}), ("assess", {"assessment_ids": []}),
])
def test_inapplicable_fields_rejected_before_domain(context, opened, actor, action, extra):
    from review_ledger.tools import CRITIC_ACTION_SCHEMAS
    values = {"repository": "synthetic/example", "run_id": opened["id"],
              "action": action, "generation": opened["generation"], "request_key": "x",
              "finding_ids": ["f"], "critic_run_id": "c", "objection_id": "o",
              "state": "pending", "basis": "none", "rationale": "Synthetic",
              "limitations": "Synthetic", "observation_ids": []}
    args = {key: values[key] for key in CRITIC_ACTION_SCHEMAS[action]["required"]}
    args.update(extra)
    result = invoke(context, args, actor)
    assert result["error"]["code"] == "invalid_input"
    assert context.llm_accesses == 0


@pytest.mark.parametrize("forbidden", ["prompt", "provider", "model", "url", "approved", "session_id"])
def test_no_agent_route_or_permission_overrides(context, opened, actor, forbidden):
    result = invoke(context, {"repository": "synthetic/example", "run_id": opened["id"],
                             "action": "status", forbidden: "forbidden"}, actor)
    assert result["error"]["code"] == "invalid_input"
    assert context.llm_accesses == 0


def test_prepare_and_status_work_without_inference(context, ledger, opened, actor):
    finding = ledger.record("synthetic/example", opened["id"], actor, opened["generation"],
                            "finding", {"claim": "Synthetic bounded claim"}, "finding")
    result = invoke(context, {"repository": "synthetic/example", "run_id": opened["id"],
                             "action": "prepare", "generation": opened["generation"],
                             "request_key": "prepare", "finding_ids": [finding["finding_id"]]}, actor)
    assert result["state"] != "error", result
    status = invoke(context, {"repository": "synthetic/example", "run_id": opened["id"],
                             "action": "status"}, actor)
    assert status["state"] != "error", status
    assert context.llm_accesses == 0


def test_trusted_session_required(context):
    result = json.loads(tools.handle(context, "ledger_critic", {
        "repository": "synthetic/example", "run_id": "r", "action": "status"}))
    assert result["error"]["code"] == "trusted_session_required"


@pytest.mark.parametrize("config,code", [
    ({"critic_enabled": True}, "critic_consent_required"),
    ({"critic_enabled": True, "critic_authorized_repositories": ["synthetic/example"]}, "critic_route_required"),
    ({"critic_enabled": True, "critic_authorized_repositories": ["synthetic/example"],
      "critic_provider": "synthetic-provider", "critic_model": "synthetic-model"}, "critic_route_unverifiable"),
])
def test_enabled_but_unauthorized_or_unverifiable_never_accesses_llm(context, opened, actor, config, code):
    context.config.update(config)
    result = invoke(context, {"action": "run", "repository": "synthetic/example",
                    "run_id": opened["id"], "generation": opened["generation"],
                    "request_key": "refused", "critic_run_id": "no-call"}, actor)
    assert result["error"]["code"] == code, result
    assert context.llm_accesses == 0
