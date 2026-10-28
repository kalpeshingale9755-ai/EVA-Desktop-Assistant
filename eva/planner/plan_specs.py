"""Capability specs for the Stage 6a web capabilities.

Merge these with the specs of EVA's real capabilities (open_app, close_app, open_file...).
"""
from __future__ import annotations

from typing import Dict

from eva.brain.specs import ArgSpec, CapabilitySpec


def web_specs() -> Dict[str, CapabilitySpec]:
    # allow_special=True because real web addresses and search words contain & ? = etc.
    # This is safe: web_actions re-checks every address and never uses a shell.
    specs = [
        CapabilitySpec(
            name="open_url",
            description="Open a website in the browser. Needs the full https address",
            args={"url": ArgSpec(type="str", max_len=500, allow_special=True,
                                 description="full https address")},
        ),
        CapabilitySpec(
            name="web_search",
            description="Search the web in the browser for some words",
            args={"query": ArgSpec(type="str", max_len=200, allow_special=True,
                                   description="the words to search for")},
        ),
    ]
    return {s.name: s for s in specs}
