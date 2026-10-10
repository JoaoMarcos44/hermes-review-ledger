"""Immutable, host-neutral procedure shipped as a package resource."""
from importlib.resources import files
import hashlib

VERSION = "3"

def protocol():
    # Resolve against this module's own package, not a bare "review_ledger": the
    # native directory-plugin loader imports it below hermes_plugins.<slug> and
    # never as a top-level package.
    content = files(__package__).joinpath("resources/protocol.md").read_text(encoding="utf-8")
    return {"version": VERSION, "sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}
