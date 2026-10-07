"""Fail-closed adapter for the tested public Hermes LLM contract.

Hermes 0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4 exposes completion but
no public pre-dispatch proof of the effective destinations and fallback policy.
Post-response provider/model fields cannot undo unauthorized transmission.
Consequently this adapter refuses before accessing ctx.llm or credentials.
It intentionally has no HTTP/SDK fallback or configurable bypass. A future
host integration requires a separately reviewed public route contract.
"""
from __future__ import annotations

from .models import LedgerError

TESTED_HERMES_REVISION = "0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4"


class HermesCriticProvider:
    def __init__(self, ctx, config):
        # Do not retain or serialize the host context, config, auth objects or secrets.
        pass

    def preflight(self):
        raise LedgerError(
            "critic_route_unverifiable",
            "The tested public Hermes ctx.llm API cannot establish all effective "
            "destinations and fallback routes before dispatch. Criticism remains "
            "blocked without sending context; normal review and local critic "
            "preparation/history remain available. No alternative client is used.",
        )

    def evaluate(self, packet):
        self.preflight()
