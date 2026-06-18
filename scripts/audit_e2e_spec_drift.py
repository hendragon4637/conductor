from __future__ import annotations

from pathlib import Path


ROOT = Path("/opt/aipc/conductor/backend")


PATTERNS = {
    "plans.session_id": ["plan[\"session_id\"]", "row.get(\"session_id\")", "SELECT plan_id, project_id, session_id"],
    "plans.status": ["plans.status", "plan[\"status\"]", "row.get(\"status\")"],
    "tasks table": ["FROM tasks", "INTO tasks", "UPDATE tasks", "JOIN tasks", "SELECT * FROM tasks"],
    "traces.task_id": ["t.task_id", "trace[\"task_id\"]", "INSERT INTO traces", "task_id, agent_config_id"],
}


def main() -> None:
    for label, needles in PATTERNS.items():
        print(f"## {label}")
        found = 0
        for path in sorted(ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = [needle for needle in needles if needle in text]
            if not hits:
                continue
            found += 1
            rel = path.relative_to(ROOT.parent)
            joined = ", ".join(hits)
            print(f"- {rel}: {joined}")
        if found == 0:
            print("- none")
        print()


if __name__ == "__main__":
    main()
