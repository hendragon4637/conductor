"""
Sync YAML agent_configs into Postgres.

Run after schema migration (file 03) and after editing any YAML.
Idempotent: upserts by agent_config_id.
"""
from __future__ import annotations
import os
import sys
import json
from pathlib import Path

import yaml
import psycopg
from dotenv import load_dotenv

# Local import — ensure backend in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.services.schema_validator import validate  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DB_URL = os.environ["DATABASE_URL"]
CONFIGS_DIR = Path(__file__).resolve().parent.parent / "agent_configs"


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def upsert_config(conn: psycopg.Connection, cfg: dict) -> None:
    # Validate routing_rules against the meta-schema
    routing = cfg.get("routing_rules", {}) or {}
    errs = validate("routing_rules", routing)
    if errs:
        raise ValueError(
            f"Invalid routing_rules in {cfg.get('agent_config_id')}:\n  "
            + "\n  ".join(errs)
        )

    sql = """
    INSERT INTO agent_configs (
      agent_config_id, cli, domain, role, pattern,
      input_spec_schema, output_spec_schema,
      routing_rules,
      skill_path, system_prompt, allowed_tools, permission_policy,
      model_preference, active, version
    )
    VALUES (
      %(agent_config_id)s, %(cli)s, %(domain)s, %(role)s, %(pattern)s,
      %(input_spec_schema)s, %(output_spec_schema)s,
      %(routing_rules)s::jsonb,
      %(skill_path)s, %(system_prompt)s, %(allowed_tools)s, %(permission_policy)s::jsonb,
      %(model_preference)s, %(active)s, %(version)s
    )
    ON CONFLICT (agent_config_id) DO UPDATE SET
      cli = EXCLUDED.cli,
      domain = EXCLUDED.domain,
      role = EXCLUDED.role,
      pattern = EXCLUDED.pattern,
      input_spec_schema = EXCLUDED.input_spec_schema,
      output_spec_schema = EXCLUDED.output_spec_schema,
      routing_rules = EXCLUDED.routing_rules,
      skill_path = EXCLUDED.skill_path,
      system_prompt = EXCLUDED.system_prompt,
      allowed_tools = EXCLUDED.allowed_tools,
      permission_policy = EXCLUDED.permission_policy,
      model_preference = EXCLUDED.model_preference,
      active = EXCLUDED.active,
      version = EXCLUDED.version,
      updated_at = now();
    """

    params = {
        "agent_config_id": cfg["agent_config_id"],
        "cli": cfg["cli"],
        "domain": cfg["domain"],
        "role": cfg["role"],
        "pattern": cfg["pattern"],
        "input_spec_schema": cfg.get("input_spec_schema"),
        "output_spec_schema": cfg.get("output_spec_schema"),
        "routing_rules": json.dumps(routing),
        "skill_path": cfg.get("skill_path"),
        "system_prompt": cfg.get("system_prompt"),
        "allowed_tools": cfg.get("allowed_tools") or [],
        "permission_policy": json.dumps(cfg.get("permission_policy") or {}),
        "model_preference": cfg.get("model_preference"),
        "active": cfg.get("active", True),
        "version": cfg.get("version", 1),
    }

    with conn.cursor() as cur:
        cur.execute(sql, params)


def main() -> int:
    if not CONFIGS_DIR.exists():
        print(f"Configs dir not found: {CONFIGS_DIR}", file=sys.stderr)
        return 1

    yaml_files = sorted(CONFIGS_DIR.glob("*.yaml"))
    if not yaml_files:
        print("No YAML configs found.", file=sys.stderr)
        return 1

    print(f"Found {len(yaml_files)} config(s) to sync.")

    with psycopg.connect(DB_URL) as conn:
        for path in yaml_files:
            print(f"  Loading {path.name}...")
            cfg = load_yaml(path)
            try:
                upsert_config(conn, cfg)
                print(f"    ✓ Upserted {cfg['agent_config_id']}")
            except Exception as e:
                print(f"    ✗ FAILED: {e}", file=sys.stderr)
                conn.rollback()
                return 2
        conn.commit()

    print("All configs synced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
