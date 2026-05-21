"""LangGraph wiring."""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END

from backend.graph.state import ConductorState
from backend.graph import nodes


def build_graph():
    g = StateGraph(ConductorState)

    g.add_node("prepare_trace", nodes.prepare_trace)
    g.add_node("record_completion", nodes.record_completion)
    g.add_node("route_next", nodes.route_next)
    g.add_node("finalize_task", nodes.finalize_task)

    # Week 1: linear flow. Spawning happens externally (file 08).
    # The "prepare_trace" node ends the graph for pre-spawn.
    # A separate invocation handles post-completion: record -> route -> finalize.
    g.add_edge(START, "prepare_trace")
    g.add_edge("prepare_trace", END)

    return g.compile()


def build_completion_graph():
    """
    Separate compiled graph for the post-CLI completion sub-flow.
    Called by the adapter (file 07) when a trace completes.
    """
    g = StateGraph(ConductorState)
    g.add_node("record_completion", nodes.record_completion)
    g.add_node("route_next", nodes.route_next)
    g.add_node("finalize_task", nodes.finalize_task)

    g.add_edge(START, "record_completion")
    g.add_edge("record_completion", "route_next")
    g.add_edge("route_next", "finalize_task")
    g.add_edge("finalize_task", END)

    return g.compile()


# Singletons
prepare_graph = build_graph()
completion_graph = build_completion_graph()
