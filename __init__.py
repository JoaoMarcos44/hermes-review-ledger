"""Directory-plugin entry point for Hermes' namespaced plugin loader."""
def register(ctx):
    from .review_ledger.tools import register as register_plugin
    return register_plugin(ctx)

__all__ = ["register"]
