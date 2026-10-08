"""Explicit local operator surface. No approval operation is a model tool."""
from __future__ import annotations

import json

from .learning import Learning
from .models import Actor, LedgerError


def configure_parser(parser):
    sub = parser.add_subparsers(dest="ledger_operator_action", required=True)
    command = sub.add_parser("automation-status", help="Inspect effective repository-local lesson policy")
    command.add_argument("repository")
    command = sub.add_parser("automation", help="Set audited automatic/manual mode; no retroactive activation")
    command.add_argument("repository")
    command.add_argument("mode", choices=["automatic", "manual"])
    command.add_argument("--reason", required=True)
    command.add_argument("--request-key", required=True)
    for action in ("approve", "suspend", "restrict", "inspect", "rollback"):
        command = sub.add_parser(action, help=f"{action.capitalize()} an exact lesson version")
        command.add_argument("repository")
        command.add_argument("version_id")
        if action != "inspect":
            command.add_argument("--reason", required=True)
            command.add_argument("--request-key", required=True)
    for action in ("transfer", "release"):
        command = sub.add_parser(action, help="Explicitly recover ownership; never based on elapsed time")
        command.add_argument("repository")
        command.add_argument("run_id")
        command.add_argument("--generation", required=True, type=int)
        if action == "transfer":
            command.add_argument("--session", required=True)
        command.add_argument("--reason", required=True)
        command.add_argument("--request-key", required=True)
    command = sub.add_parser("invalidate", help="Invalidate a source and suspend dependent active lessons")
    command.add_argument("repository")
    command.add_argument("observation_id")
    command.add_argument("--reason", required=True)
    command.add_argument("--request-key", required=True)
    for action in ("skill-add", "skill-update"):
        command = sub.add_parser(action, help="Approve a chosen local text package; no code is executed")
        command.add_argument("repository")
        command.add_argument("qualified_id")
        command.add_argument("package_path")
        command.add_argument("--reference", action="append", default=[])
        command.add_argument("--approve-import", action="store_true", required=True)
    for action in ("skill-enable", "skill-disable", "skill-inspect"):
        command = sub.add_parser(action)
        command.add_argument("repository")
        command.add_argument("version_id")
        if action != "skill-inspect":
            command.add_argument("--reason", required=True)
            command.add_argument("--request-key", required=True)
    command = sub.add_parser("skill-list")
    command.add_argument("repository")
    command = sub.add_parser("improvement-inspect")
    command.add_argument("repository")
    command.add_argument("improvement_id")
    command = sub.add_parser("improvement-list")
    command.add_argument("repository")
    command.add_argument("run_id")
    command = sub.add_parser("usage")
    command.add_argument("repository")
    command.add_argument("--run-id")
    for action in ("critic-abandon", "critic-assess"):
        command = sub.add_parser(action, help="Explicit local operator critic action; remote work is never cancelled")
        command.add_argument("repository")
        command.add_argument("run_id")
        command.add_argument("critic_run_id")
        command.add_argument("--request-key", required=True)
        if action == "critic-abandon":
            command.add_argument("--reason", required=True)
        else:
            command.add_argument("--generation", required=True, type=int)
            command.add_argument("--objection-id", required=True)
            command.add_argument("--state", required=True, choices=["pending", "supported", "refuted", "inconclusive", "not_applicable"])
            command.add_argument("--basis", required=True, choices=["none", "inspection", "behavior"])
            command.add_argument("--rationale", required=True)
            command.add_argument("--limitations", required=True)
            command.add_argument("--observation-id", action="append", default=[])
    sub.add_parser("backup", help="Create SQLite API backup and verify a temporary restored copy")


def dispatch(ctx, args):
    from .tools import ledger_for_context
    try:
        ledger = ledger_for_context(ctx)
        action = args.ledger_operator_action
        if action == "backup":
            result = ledger.store.backup()
        elif action.startswith("skill-"):
            from .skills import Skills
            registry, scope = Skills(ledger.store), ledger.scope(args.repository)
            if action in ("skill-add", "skill-update"):
                method = registry.register if action == "skill-add" else registry.update
                result = method(scope, package_path=args.package_path, qualified_id=args.qualified_id,
                                references=args.reference, approved=args.approve_import)
            elif action == "skill-list":
                result = registry.list(scope)
            elif action == "skill-inspect":
                result = registry.inspect(scope, args.version_id)
            else:
                method = registry.enable if action == "skill-enable" else registry.disable
                result = method(scope, args.version_id, reason=args.reason, request_key=args.request_key)
        elif action in ("improvement-inspect", "improvement-list"):
            from .improvements import Improvements
            improvements, scope = Improvements(ledger.store), ledger.scope(args.repository)
            result = (improvements.inspect(scope, args.improvement_id) if action == "improvement-inspect"
                      else improvements.list(scope, args.run_id))
        elif action == "usage":
            from .usage import Usage
            result = Usage(ledger.store).report(ledger.scope(args.repository), args.run_id)
        elif action in ("automation", "automation-status"):
            learning = Learning(ledger.store, automation_mode=getattr(ledger, "automation_mode", "manual"))
            scope = ledger.scope(args.repository)
            result = (learning.automation_status(scope) if action == "automation-status" else
                      learning.automation_policy(scope, args.mode, args.reason, args.request_key))
        elif action in ("approve", "suspend", "restrict", "inspect", "rollback"):
            scope = ledger.scope(args.repository)
            learning = Learning(ledger.store)
            if action == "inspect":
                with ledger.store.connect() as conn:
                    result = {"state": "ok", "lesson": learning.version(conn, scope, args.version_id)}
            elif action == "rollback":
                result = learning.rollback(scope, args.version_id, args.reason, args.request_key)
            else:
                result = learning.operator(scope, args.version_id, action, args.reason, args.request_key)
        elif action in ("transfer", "release"):
            result = ledger.operator_transfer(args.repository, args.run_id, args.session if action == "transfer" else None,
                                              args.generation, args.reason, args.request_key)
        elif action == "critic-abandon":
            from .critic import Critic
            result = Critic(ledger).abandon(args.repository, args.run_id, args.critic_run_id, args.reason, args.request_key)
        elif action == "critic-assess":
            from .critic import Critic
            result = Critic(ledger).assess(args.repository, args.run_id, Actor("local_operator"), args.generation,
                                          args.request_key, args.critic_run_id, args.objection_id, args.state,
                                          args.basis, args.rationale, args.limitations, args.observation_id, operator=True)
        elif action == "invalidate":
            result = ledger.operator_invalidate(args.repository, args.observation_id, args.reason, args.request_key)
        else:
            raise LedgerError("invalid_input", "Unknown operator action")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except LedgerError as exc:
        print(json.dumps({"state": "error", "error": {"code": exc.code, "message": str(exc)}}))
        return 1
