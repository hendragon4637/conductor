"""Per-backend tool profiles for realizability checks.

Each entry lists the tools a backend type actually provides.
This is the source of truth for the staffing gate's realizability
set-check: capability.required_tools ⊆ backend.tools.
"""
from __future__ import annotations

HARNESS_PROFILES: dict[str, dict[str, list[str]]] = {
    "opencode": {
        "tools": [
            "write_file",
            "read_file",
            "shell",
            "browser",
            "http",
            "read_data",
            "read_web",
            "search",
            "grep",
            "glob",
        ],
    },
    "hermes": {
        "tools": [
            "write_file",
            "read_file",
            "shell",
            "http",
            "read_data",
            "read_web",
            "search",
        ],
    },
    "opencode_omo": {
        "tools": [
            "write_file",
            "read_file",
            "shell",
            "browser",
            "http",
            "read_data",
            "read_web",
            "search",
        ],
    },
    "claude_code": {
        "tools": [
            "write_file",
            "read_file",
            "shell",
            "http",
            "read_data",
            "read_web",
        ],
    },
}
