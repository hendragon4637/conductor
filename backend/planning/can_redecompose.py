"""Guard function for re-decomposition depth."""
from __future__ import annotations

from typing import Any

MAX_DEPTH = 4


def can_redecompose(node: dict[str, Any]) -> bool:
    if node.get("node_status") in ("done", "running"):
        return False
    if node.get("depth", 0) >= MAX_DEPTH:
        return False
    return True
