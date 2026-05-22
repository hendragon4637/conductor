# Orchestration Patterns

Reference YAMLs for the canonical orchestration patterns in Conductor.

These are **documentation only** -- they show the *shape* of routing_rules for each
pattern. To use, copy to `/opt/aipc/conductor/agent_configs/<id>.yaml`, edit, and
run the bootstrap script.

## Patterns

| File | Pattern | Use case |
|------|---------|----------|
| `standalone.yaml`      | standalone        | Single CLI, no chain (week 1 default) |
| `pipeline.yaml`        | pipeline          | PEV, sequential stages |
| `supervisor-worker.yaml` | supervisor-worker | Task decomposition + aggregation (week 5+) |
| `fan-out-fan-in.yaml`  | fan-out-fan-in    | Parallel consensus (week 5+) |
| `critic-verifier.yaml` | critic-verifier   | Designer-critic, producer-reviewer loops |
| `reflection.yaml`      | reflection        | Self-improving single agent |
| `custom.yaml`          | custom            | Escape hatch -- manual routing logic |

## Choosing a pattern

- Code work where tests give clean pass/fail: **pipeline** (PEV)
- Creative work with no deterministic verifier: **critic-verifier**
- Research / read-only with no chain needed: **standalone** or **pipeline** (searcher -> synthesizer)
- High blast radius (infra, finance, legal): **pipeline** with an extra auditor role
- Pure code that fails repeatedly with same mode: **reflection**

Don't reach for **supervisor-worker** or **fan-out-fan-in** until you have a real
case for parallelism and the orchestrator support (week 5+).
