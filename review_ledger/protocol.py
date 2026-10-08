"""Immutable, host-neutral procedure shipped as a package resource."""
from importlib.resources import files
import hashlib

VERSION = "2"

def protocol():
    content = files("review_ledger").joinpath("resources/protocol.md").read_text(encoding="utf-8")
    return {"version": VERSION, "sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}
