"""Native Hermes integration: trusted invocation context stays at this edge."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import __version__
from .github import GitHubClient, GitHubError
from .learning import Learning
from .models import Actor, LedgerError, fields, integer, strings, text
from .reports import export
from .service import Ledger
from .storage import Store

SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "review-ledger" / "SKILL.md"


def ledger_for_context(ctx) -> Ledger:
    """Resolve public, profile-scoped state on every invocation; never cache a connection."""
    try:
        data_dir = Path(ctx.state.data_dir)
    except (AttributeError, TypeError, ValueError) as exc:
        raise LedgerError("unsupported_host_context", "Hermes public ctx.state.data_dir is required") from exc
    if not data_dir.is_absolute():
        raise LedgerError("unsupported_host_context", "Hermes data directory must be absolute")
    profile_key = hashlib.sha256(str(data_dir.resolve()).encode()).hexdigest()
    allowed = ctx.get_config("authorized_repositories", default=[])
    allowed = strings(allowed, "configured authorized_repositories", 100, 200)
    budget = integer(ctx.get_config("context_budget", default=6000), "configured context_budget", 500, 20000)
    return Ledger(
        Store(data_dir, profile_key), allowed,
        skill_version=__version__,
        skill_hash=hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest(),
        relevant_config={"context_budget": budget, "comparison": "github_pr"},
    )


def handle(ctx, name: str, arguments: dict, **kwargs) -> str:
    try:
        # session_id is supplied separately by Hermes model dispatch; a model field is rejected.
        session = kwargs.get("session_id")
        if not isinstance(session, str) or not session.strip():
            raise LedgerError("trusted_session_required", "A trusted Hermes-injected session_id is required; model/task IDs are not accepted")
        actor = Actor(session)
        parameters = SCHEMAS[name]["parameters"]
        args = fields(arguments, set(parameters["properties"]), set(parameters["required"]))
        ledger = ledger_for_context(ctx)
        repository = text(args["repository"], "repository", 200)
        ledger.authorize(repository)
        if name == "ledger_open":
            result = _open_review(ctx, ledger, repository, actor, args)
        elif name == "ledger_status":
            if ("run_id" in args) == ("pull_number" in args):
                raise LedgerError("invalid_input", "Select exactly one run_id or pull_number for status")
            if "pull_number" in args:
                if any(key in args for key in ("history_offset", "detail_collection", "detail_id")):
                    raise LedgerError("invalid_input", "PR history uses offset without history_offset or detail selectors")
                result = ledger.history(repository, args["pull_number"],
                                        limit=args.get("limit", 10), offset=args.get("offset", 0),
                                        max_chars=args.get("max_chars", 24_000))
            elif "detail_collection" in args:
                if any(key in args for key in ("limit", "history_offset")):
                    raise LedgerError("invalid_input", "Detail retrieval uses a character offset without list or history window fields")
                if args["detail_collection"] == "run" and "detail_id" in args:
                    raise LedgerError("invalid_input", "Run detail uses run_id without detail_id")
                result = ledger.status_detail(
                    repository, args["run_id"], actor, detail_collection=args["detail_collection"],
                    detail_id=args.get("detail_id"), offset=args.get("offset", 0),
                    max_chars=args.get("max_chars", 24_000),
                )
            else:
                if "detail_id" in args:
                    raise LedgerError("invalid_input", "detail_id requires detail_collection")
                result = ledger.status(
                    repository, args["run_id"], actor,
                    limit=args.get("limit", 10), offset=args.get("offset", 0),
                    history_offset=args.get("history_offset", 0),
                    max_chars=args.get("max_chars", 24_000),
                )
        elif name == "ledger_run":
            result = ledger.run_action(
                repository, args["run_id"], actor, args["generation"],
                args["action"], args["request_key"], args.get("note", ""),
            )
        elif name == "ledger_record":
            result = ledger.record(
                repository, args["run_id"], actor, args["generation"],
                args["action"], args["data"], args["request_key"],
            )
        elif name == "ledger_recall":
            requested_budget = integer(args.get("context_budget", ledger.config["context_budget"]),
                                       "context_budget", 500, 20000)
            budget = min(requested_budget, ledger.config["context_budget"])
            learning, scope = Learning(ledger.store), ledger.scope(repository)
            if "version_id" in args:
                if any(key in args for key in ("terms", "tags", "symbols", "limit", "result_offset")):
                    raise LedgerError("invalid_input", "Detail retrieval cannot also specify search or result-window fields")
                result = learning.detail(scope, args["run_id"], args["version_id"],
                                         offset=args.get("offset", 0), context_budget=budget)
            else:
                result = learning.recall(
                    scope, args["run_id"],
                    terms=args.get("terms"), tags=args.get("tags"), symbols=args.get("symbols"),
                    limit=args.get("limit", 5), offset=args.get("offset", 0),
                    result_offset=args.get("result_offset", 0), context_budget=budget,
                )
        elif name == "ledger_critic":
            result = _critic_action(ctx, ledger, repository, actor, args)
        elif name == "ledger_lesson":
            result = _record_lesson(ledger, repository, actor, args)
        else:
            result = export(
                ledger, repository, args["run_id"], actor, format=args["format"],
                limit=args.get("limit", 25), offset=args.get("offset", 0),
                max_chars=args.get("max_chars", 100000),
            )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (LedgerError, GitHubError) as exc:
        return json.dumps({"state": "error", "error": {"code": exc.code, "message": str(exc)}}, separators=(",", ":"))
    except (TypeError, ValueError, KeyError):
        return json.dumps({"state": "error", "error": {"code": "invalid_input", "message": "Invalid or missing operation fields"}}, separators=(",", ":"))
    except Exception:
        # No raw exception/HTTP body/configuration in model-visible responses.
        return json.dumps({"state": "error", "error": {"code": "internal_error", "message": "Ledger operation failed; no clean-review result is available"}}, separators=(",", ":"))


def _open_review(ctx, ledger: Ledger, repository: str, actor: Actor, args: dict) -> dict:
    request_key = text(args["request_key"], "request_key", 128)
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise LedgerError("invalid_input", "Repository must be owner/name")
    number = integer(args["pull_number"], "pull_number", 1, 2**31 - 1)
    try:
        from agent.secret_scope import UnscopedSecretError, get_secret
    except ImportError:
        raise LedgerError("unsupported_host_context", "Hermes profile-scoped credential resolution is required") from None
    client = GitHubClient(
        allowed_repositories=sorted(ledger.authorized),
        token_env=ctx.get_config("github_token_env", default="REVIEW_LEDGER_GITHUB_TOKEN"),
        token_resolver=get_secret,
        timeout=10, max_pages=3, max_files=200, max_patch_chars=4000,
    )
    generation = ledger.open_generation(repository, number)
    try:
        snapshot = client.fetch_snapshot(owner, name, number).as_dict()
    except UnscopedSecretError:
        raise LedgerError("secret_scope_required", "Hermes profile secret scope is required to read the configured GitHub credential") from None
    result = ledger.open(
        snapshot, actor, request_key, expected_open_generation=generation,
    )
    result["recall"] = Learning(ledger.store).recall(
        ledger.scope(repository), result["run"]["id"],
        context_budget=ledger.config["context_budget"],
    )
    # Patches are untrusted reference data; host tools perform any inspection.
    retained_names = {item["filename"] for item in result["run"]["snapshot"]["files"]}
    result["files"] = [item for item in snapshot["files"] if item["filename"] in retained_names][:5]
    result["files_returned"] = len(result["files"])
    result["files_omitted_from_response"] = max(0, len(snapshot["files"]) - len(result["files"]))
    result["files_not_retained_in_snapshot"] = sum(item["filename"] not in retained_names for item in snapshot["files"])
    result["patch_notice"] = "GitHub patches may be missing or truncated; no code was executed."
    return result



def _critic_action(ctx, ledger: Ledger, repository: str, actor: Actor, args: dict) -> dict:
    """Validate the exact action before constructing optional inference surfaces."""
    action = args["action"]
    if action not in CRITIC_ACTION_SCHEMAS:
        raise LedgerError("invalid_input", "Unknown critic action")
    selected = CRITIC_ACTION_SCHEMAS[action]
    fields(args, set(selected["properties"]), set(selected["required"]))
    from .critic import Critic, CriticConfig
    config = CriticConfig.from_context(ctx)
    if action == "status":
        return Critic(ledger, config).status(
            repository, args["run_id"], critic_run_id=args.get("critic_run_id"),
            offset=args.get("offset", 0), max_chars=args.get("max_chars", 24_000),
        )
    common = (repository, args["run_id"], actor, args["generation"], args["request_key"])
    if action == "prepare":
        return Critic(ledger, config).prepare(
            *common, finding_ids=args["finding_ids"],
            observation_ids=args.get("observation_ids", []),
            assessment_ids=args.get("assessment_ids", []),
        )
    if action == "run":
        # Disabled mode must not even construct a host provider or access ctx.llm.
        if not config.enabled:
            raise LedgerError("critic_disabled", "The optional critic is disabled")
        from .critic_hermes import HermesCriticProvider
        return Critic(ledger, config, provider=HermesCriticProvider(ctx, config)).run(
            *common, critic_run_id=args["critic_run_id"],
        )
    return Critic(ledger, config).assess(
        *common, critic_run_id=args["critic_run_id"], objection_id=args["objection_id"],
        state=args["state"], basis=args["basis"], rationale=args["rationale"],
        limitations=args["limitations"], observation_ids=args["observation_ids"],
    )


def _record_lesson(ledger: Ledger, repository: str, actor: Actor, args: dict) -> dict:
    learning = Learning(ledger.store)
    scope = ledger.scope(repository)
    data, action = args["data"], args["action"]
    common = (scope, args["run_id"], actor, args["generation"])
    request_key = args["request_key"]
    if action in ("propose", "revise"):
        if action == "revise" and not data.get("previous_version_id"):
            raise LedgerError("invalid_input", "A revision requires previous_version_id")
        return learning.propose(*common, data, request_key)
    if action == "use":
        required = {"version_id", "applicability", "explanation"}
        fields(data, required, required)
        return learning.use(
            *common, data["version_id"], data["applicability"], data["explanation"], request_key,
        )
    if action == "result":
        required = {"use_id", "usefulness", "behavioral_result", "execution_block", "explanation"}
        fields(data, required, required)
        outcome = {key: value for key, value in data.items() if key != "use_id"}
        return learning.result(*common, data["use_id"], outcome, request_key)
    raise LedgerError("invalid_input", "Lesson tools may propose, revise, use or record results; approval is operator-only")


S = {"type": "string"}
I = {"type": "integer"}
BASE = {"repository": {"type": "string", "description": "Explicitly authorized GitHub owner/name"}, "run_id": S}
WRITE = {**BASE, "generation": I, "request_key": {"type": "string", "description": "Stable retry key; reuse only with identical content"}}


def schema(name, description, properties, required):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}


SCHEMAS = {
    "ledger_open": schema("ledger_open", "Read the current authorized GitHub PR snapshot and atomically open or accompany its investigation. No code execution.",
                          {"repository": BASE["repository"], "pull_number": I, "request_key": WRITE["request_key"]}, ["repository", "pull_number", "request_key"]),
    "ledger_status": schema("ledger_status", "Read run evidence or PR history within a serialized character budget. Oversized items return explicit detail references. Select exactly one run_id or pull_number. With run_id and detail_collection, retrieve complete record JSON in bounded pages and verify the assembled digest; offset is then a character position, and limit/history_offset are forbidden. No session takeover.",
                            {**BASE, "pull_number": I, "limit": I, "offset": I, "history_offset": I,
                             "max_chars": {"type": "integer", "minimum": 4000, "maximum": 64000},
                             "detail_collection": {"type": "string", "enum": ["run", "observation", "assessment", "finding"]},
                             "detail_id": {"type": "string", "description": "Required for observation, assessment or finding detail; omit for run detail"}}, ["repository"]),
    "ledger_run": schema("ledger_run", "Acquire an unowned run or release, pause, complete the current owned generation. Never transfer another session's ownership.",
                         {**WRITE, "action": {"type": "string", "enum": ["acquire", "release", "pause", "complete"]}, "note": S}, [*WRITE, "action"]),
    "ledger_record": schema("ledger_record", "Record agent-reported evidence, propose a finding, assess it for this snapshot, or invalidate an owned-run observation. Read the skill for action-specific data fields.",
                            {**WRITE, "action": {"type": "string", "enum": ["observation", "finding", "assessment", "invalidate_observation"]}, "data": {"type": "object"}}, [*WRITE, "action", "data"]),
    "ledger_recall": schema("ledger_recall", "Read eligible same-repository lessons or budgeted references. With version_id, retrieve complete lesson JSON in bounded pages; reassemble before use. Search uses result_offset within its candidate window; detail uses offset as a character position. Similarity is not proof.",
                            {**BASE, "terms": {"type": "array", "items": S}, "tags": {"type": "array", "items": S}, "symbols": {"type": "array", "items": S}, "version_id": S,
                             "limit": I, "context_budget": I, "offset": I, "result_offset": I}, ["repository", "run_id"]),
    "ledger_lesson": schema("ledger_lesson", "Propose or revise candidate lessons; record exact-version use and separate applicability, usefulness, behavior, and blocking. Cannot approve lessons.",
                            {**WRITE, "action": {"type": "string", "enum": ["propose", "revise", "use", "result"]}, "data": {"type": "object"}}, [*WRITE, "action", "data"]),
    "ledger_export": schema("ledger_export", "Generate a bounded Markdown/JSON snapshot; checks lesson revocations and missing artifacts. Does not publish or import.",
                            {**BASE, "format": {"type": "string", "enum": ["markdown", "json"]}, "limit": I, "offset": I, "max_chars": I}, ["repository", "run_id", "format"]),
}


_CRITIC_COMMON = {**BASE, "action": {"type": "string", "enum": ["prepare", "run", "status", "assess"]}}
_IDS = {"type": "array", "items": S, "uniqueItems": True}
CRITIC_ACTION_SCHEMAS = {}
for _action, _properties, _required in (
    ("prepare", {**WRITE, "finding_ids": {**_IDS, "minItems": 1, "maxItems": 3},
                 "observation_ids": _IDS, "assessment_ids": _IDS},
     [*WRITE, "finding_ids"]),
    ("run", {**WRITE, "critic_run_id": S}, [*WRITE, "critic_run_id"]),
    ("status", {**BASE, "critic_run_id": S, "offset": I, "max_chars": I}, [*BASE]),
    ("assess", {**WRITE, "critic_run_id": S, "objection_id": S,
                "state": {"type": "string", "enum": ["pending", "supported", "refuted", "inconclusive", "not_applicable"]},
                "basis": {"type": "string", "enum": ["inspection", "behavior", "none"]},
                "rationale": S, "limitations": S, "observation_ids": _IDS},
     [*WRITE, "critic_run_id", "objection_id", "state", "basis", "rationale", "limitations", "observation_ids"]),
):
    CRITIC_ACTION_SCHEMAS[_action] = {
        "type": "object", "properties": {**_properties, "action": {"type": "string", "enum": [_action]}},
        "required": [*_required, "action"], "additionalProperties": False,
    }
_critic_properties = dict(_CRITIC_COMMON)
for _action_schema in CRITIC_ACTION_SCHEMAS.values():
    _critic_properties.update({key: value for key, value in _action_schema["properties"].items() if key != "action"})
SCHEMAS["ledger_critic"] = schema(
    "ledger_critic",
    "Explicit optional claim criticism. prepare freezes selected ledger IDs without inference; run needs operator-configured consent and an authorized route; status reads bounded pages; assess records checks of objections. Opinions never confirm findings. No free-form prompt or route overrides.",
    _critic_properties, [*BASE, "action"],
)
SCHEMAS["ledger_critic"]["parameters"]["oneOf"] = list(CRITIC_ACTION_SCHEMAS.values())


TOOL_NAMES = tuple(SCHEMAS)

def register(ctx):
    """Only register supported surfaces. No database, network, installs, or background work."""
    from .operator import configure_parser, dispatch
    for name in TOOL_NAMES:
        def handler(args, _name=name, **kwargs):
            return handle(ctx, _name, args, **kwargs)
        ctx.register_tool(name=name, toolset="review_ledger", schema=SCHEMAS[name], handler=handler)
    ctx.register_skill("review-ledger", SKILL_PATH)
    ctx.register_cli_command(name="review-ledger", help="Review Ledger local operator actions",
                             setup_fn=configure_parser, handler_fn=lambda args: dispatch(ctx, args))
