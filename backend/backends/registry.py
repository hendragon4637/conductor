"""Backend registry — taxonomy of all execution backends Conductor supports.

Each backend is classified as:
  - **class-a (self-orchestrating)** — the tool IS its own team/leader. No
    Conductor/AionUi orchestrator is spawned; the goal goes directly to the
    tool which self-routes internally.
  - **class-b (non-self-orchestrating/single-agent)** — needs an AionUi Leader
    ("orchestrator always spawns") to coordinate single-agent CLIs.

The registry is the single source of truth for backend metadata. Switching
a node's ``backend`` flips its run-mode automatically without any other
code change.

Usage::

    from backend.backends.registry import BACKENDS, BackendClass, select_run

    b = BACKENDS[node.backend]
    if b["class"] == BackendClass.SELF_ORCHESTRATING:
        run_self_orchestrating(node.backend, goal=brief)
    else:
        spawn_aionui_leader(members=node.members)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal


class BackendClass(str, Enum):
    """Taxonomy of backend execution classes.

    - ``SELF_ORCHESTRATING`` (a): tool is its own leader — no orchestrator
    - ``SINGLE_AGENT`` (b): needs an AionUi Leader orchestrator
    """

    SELF_ORCHESTRATING = "a"
    SINGLE_AGENT = "b"


#: Run-mode strings used by the spawn layer.
RunMode = Literal["direct_or_member", "aionui_member", "aionui_team"]


#: Registry: backend key → metadata dict.
#:
#: Each entry has:
#:   - ``class``: "a" (self-orchestrating) or "b" (single-agent)
#:   - ``run``: how Conductor drives it
#:   - ``self_routes``: bool — does the tool decompose internally?
#:
#: Extend this dict to register new backends.
BACKENDS: dict[str, dict[str, Any]] = {
    # ── class-a: self-orchestrating ────────────────────────────────────────
    "hermes": {
        "class": BackendClass.SELF_ORCHESTRATING,
        "run": "direct_or_member",
        "self_routes": True,
        "label": "Hermes (self-routing)",
    },
    "opencode_omo": {
        "class": BackendClass.SELF_ORCHESTRATING,
        "run": "direct_or_member",
        "self_routes": True,
        "label": "OpenCode+OMO (self-routing)",
    },
    "openclaw": {
        "class": BackendClass.SELF_ORCHESTRATING,
        "run": "direct_or_member",
        "self_routes": True,
        "label": "OpenClaw (self-routing, future)",
    },
    "paperclip": {
        "class": BackendClass.SELF_ORCHESTRATING,
        "run": "direct_or_member",
        "self_routes": True,
        "label": "Paperclip (self-routing, future)",
    },
    # ── class-b: single-agent (needs orchestrator) ─────────────────────────
    "opencode": {
        "class": BackendClass.SINGLE_AGENT,
        "run": "aionui_member",
        "self_routes": False,
        "label": "OpenCode (plain, single-agent)",
    },
    "claude_code": {
        "class": BackendClass.SINGLE_AGENT,
        "run": "aionui_member",
        "self_routes": False,
        "label": "Claude Code (single-agent)",
    },
    "codex": {
        "class": BackendClass.SINGLE_AGENT,
        "run": "aionui_member",
        "self_routes": False,
        "label": "Codex (single-agent)",
    },
    "gemini": {
        "class": BackendClass.SINGLE_AGENT,
        "run": "aionui_member",
        "self_routes": False,
        "label": "Gemini (single-agent)",
    },
    # ── class-b: native team ───────────────────────────────────────────────
    "aionui": {
        "class": BackendClass.SINGLE_AGENT,
        "run": "aionui_team",
        "self_routes": False,
        "label": "AionUi (native team)",
    },
}


def get_backend(key: str) -> dict[str, Any]:
    """Look up a backend by key; raises ``KeyError`` if unknown."""
    if key not in BACKENDS:
        valid = ", ".join(sorted(BACKENDS))
        raise KeyError(f"Unknown backend {key!r}. Valid: {valid}")
    return BACKENDS[key]


def is_self_orchestrating(key: str) -> bool:
    """Return True if *key* is a class-a (self-orchestrating) backend."""
    return get_backend(key)["class"] == BackendClass.SELF_ORCHESTRATING


def is_single_agent(key: str) -> bool:
    """Return True if *key* is a class-b (single-agent) backend."""
    return get_backend(key)["class"] == BackendClass.SINGLE_AGENT


def run_mode_for(key: str) -> RunMode:
    """Return the run-mode string for *key*."""
    return get_backend(key)["run"]


def select_run(
    backend_key: str,
    members: list[str] | None = None,
    prefer_direct: bool = True,
) -> dict[str, Any]:
    """Given a backend key and optional members, decide how to run.

    Returns a dict with:
      - ``orchestrator``: bool — whether an AionUi Leader orchestrator spawns
      - ``members``: list of team members (for class-b) or [backend_key] (class-a)
      - ``mode``: run-mode string
      - ``note``: explanation for debugging

    This is the central selector called by the orchestrator runner.

    Args:
        backend_key: One of the keys in ``BACKENDS``.
        members: For class-b nodes, the agent list to spawn under the Leader.
        prefer_direct: For class-a, prefer direct API call over AionUi member.

    Returns:
        Run-mode decision dict.
    """
    b = get_backend(backend_key)
    bc = b["class"]

    if bc == BackendClass.SELF_ORCHESTRATING:
        # Class-a: NO orchestrator, tool self-routes.
        return {
            "orchestrator": False,
            "members": [backend_key],
            "mode": "direct_or_member",
            "note": "Self-orchestrating: no AionUi orchestrator; tool self-routes.",
        }

    # Class-b: orchestrator always spawns as AionUi Leader.
    node_members = members or [backend_key]
    return {
        "orchestrator": True,
        "members": node_members,
        "mode": "aionui_team",
        "note": "Orchestrator + {} member(s) under AionUi Leader.".format(len(node_members)),
    }


# ── Convenience grouping for UI dropdowns ─────────────────────────────────

def grouped_backends() -> dict[str, list[dict[str, str]]]:
    """Return backends grouped by class for UI dropdown rendering.

    Returns::

        {
            "Self-orchestrating (a)": [
                {"key": "hermes", "label": "Hermes (self-routing)"},
                ...
            ],
            "Single-agent (b)": [
                {"key": "opencode", "label": "OpenCode (plain, single-agent)"},
                ...
            ],
        }
    """
    groups: dict[str, list[dict[str, str]]] = {
        "Self-orchestrating (a) · no orchestrator": [],
        "Single-agent (b) · orchestrator + members": [],
        "Team": [],
    }
    for key, meta in BACKENDS.items():
        bc = meta["class"]
        label = meta.get("label", key)
        entry = {"key": key, "label": label}
        if bc == BackendClass.SELF_ORCHESTRATING:
            groups["Self-orchestrating (a) · no orchestrator"].append(entry)
        elif meta.get("run") == "aionui_team":
            groups["Team"].append(entry)
        else:
            groups["Single-agent (b) · orchestrator + members"].append(entry)
    return groups
