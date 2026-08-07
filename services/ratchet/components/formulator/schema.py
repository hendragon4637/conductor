"""Formulator component schemas — the golden item payload contract.

``FormulatorExpected.standards`` carries BASE names (version suffix and
``@subdir``/``[variant]`` stripped) — ``design-layout`` equals
``design-layout-v2`` by the settled scoring rule.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FormulatorInput(BaseModel):
    raw_input: str
    project_id: str | None = None
    origin: str | None = None


class FormulatorExpected(BaseModel):
    standards: list[str] = Field(description="Base standard names, no version, no @subdir")
    subdirs: list[str] = Field(default_factory=list)
    clarify: bool
    nodes_min: int
    nodes_max: int
