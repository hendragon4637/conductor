"""Path/env configuration with defaults — never hardcoded in modules."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    input_path: str
    output_path: str
    min_qty: int = 1

    @classmethod
    def from_env(cls, input_path: str, output_path: str) -> Config:
        return cls(
            input_path=input_path,
            output_path=output_path,
            min_qty=int(os.environ.get("MIN_QTY", "1")),
        )
