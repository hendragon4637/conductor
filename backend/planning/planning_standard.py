"""Planning Standard — gate rubric items for plan evaluation."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_gate_rubric_item() -> dict[str, Any]:
    """Get additional L2 rubric item for the plan gate that checks reasoning quality."""
    return {
        "id": "justifies_decomposition",
        "rubric_item": "Does the plan justify the decomposition coherently? Each node's existence should be traceable to the overall goal.",
        "weight": 1.5,
    }
