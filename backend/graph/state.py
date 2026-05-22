"""LangGraph state — passed between nodes."""
from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, Field
from uuid import UUID


class ConductorState(BaseModel):
    """State threaded through the graph for one trace lifecycle."""

    # identifiers
    task_id: UUID
    trace_id: Optional[UUID] = None
    project_id: str
    session_id: str
    agent_config_id: str

    # spec envelope
    input_spec: dict[str, Any] = Field(default_factory=dict)
    output_spec: Optional[dict[str, Any]] = None

    # links
    preceding_trace_id: Optional[UUID] = None

    # runtime
    status: str = "pending"      # pending|spawned|complete|failed
    skill_snapshot_hash: Optional[str] = None
    skill_path: Optional[str] = None
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    ended_reason: Optional[str] = None

    # routing decision (set by route_next node)
    next_agent_config_id: Optional[str] = None
    terminates_task: bool = False

    # diagnostics
    errors: list[str] = Field(default_factory=list)
