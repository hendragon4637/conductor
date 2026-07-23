"""Shared deepeval judge model — routes all deepeval metric calls through the
LiteLLM gateway using the evaluation API key.

Usage:
    from shared.eval_models import JUDGE

    metric = GEval(model=JUDGE, ...)

The JUDGE model is configured to:
  - Route through the conductor's LiteLLM gateway (litellm:4000)
  - Use the evaluation API key (LITELLM_KEY_EVALUATION) and model group (judge)
  - Use temperature=0 for deterministic output
  - Never use OpenAI defaults or Confident AI cloud
"""

from __future__ import annotations

import os

from deepeval.models import LiteLLMModel

JUDGE = LiteLLMModel(
    model="openai/judge",
    base_url=(os.environ.get("LITELLM_BASE") or "http://litellm:4000/v1"),
    api_key=(os.environ.get("LITELLM_KEY_EVALUATION") or ""),
    temperature=0,
    max_tokens=16386,
)
