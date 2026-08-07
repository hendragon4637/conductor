"""Component registry — maps component names to their schemas and modules."""
from __future__ import annotations

from typing import Any, Callable

from services.ratchet.components.formulator import schema as formulator_schema
from services.ratchet.components.formulator import replay as formulator_replay
from services.ratchet.components.formulator import metrics as formulator_metrics


class Component:
    def __init__(self, name: str, schemas: tuple[Any, Any],
                 replay_fn: Callable, metrics: Any):
        self.name = name
        self.schemas = schemas
        self.replay_fn = replay_fn
        self.metrics = metrics

    def input_schema(self):
        return self.schemas[0]

    def expected_schema(self):
        return self.schemas[1]


_REGISTRY: dict[str, Component] = {
    "formulator": Component(
        name="formulator",
        schemas=(formulator_schema.FormulatorInput, formulator_schema.FormulatorExpected),
        replay_fn=formulator_replay.replay,
        metrics=formulator_metrics,
    ),
}


def get_component(name: str) -> Component:
    if name not in _REGISTRY:
        raise KeyError(f"unknown component '{name}' — registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def list_components() -> list[dict[str, Any]]:
    out = []
    for name, comp in _REGISTRY.items():
        out.append({
            "name": name,
            "target": comp.metrics.TARGET,
            "guards": comp.metrics.GUARDS,
        })
    return out
