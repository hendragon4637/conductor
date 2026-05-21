# Architecture

See `/mnt/user-data/outputs/aipc_data_model_preview_v2.html` for the visual model.

Key invariants:
- Project = git repo. Session = git branch. Task = flexible work unit. Trace = one CLI invocation = one "room".
- Routing rules in agent_configs.routing_rules (JSONB).
- LangGraph orchestration is generic; patterns are data.
- Native desktop is the work surface; UI is read-mostly.
