"""System goal decomposition: ONE structured LLM call.

Receives a system-level goal (e.g. "build an e-commerce platform"),
decomposes it into a SystemPlan describing:
  - projects to create (component / assembly)
  - their dependencies
  - per-project initial goals

No harness spawn, no DAG assembly — just a single ``extract_system_plan()``
LLM call returning validated ``SystemPlan`` JSON.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# ── Pydantic contracts ──────────────────────────────────────────────────────


class ProjectDef(BaseModel):
    """A single project within a system."""
    name: str = Field(description="Human-facing project name, unique within the system")
    kind: str = Field(default="component", description="component or assembly")
    domain: str = Field(default="general", description="Domain for purpose/standard selection")
    description: str = Field(default="", description="One-line summary")
    depends_on: list[str] = Field(default_factory=list, description="Names of projects this depends on")
    first_goal: str = Field(default="", description="Initial goal for this project's first plan")


class SystemPlan(BaseModel):
    """Output from the system-decompose LLM call."""
    system_name: str = Field(description="Name for the system")
    system_description: str = Field(default="", description="One-line system description")
    glossary: dict[str, str] = Field(default_factory=dict, description="Domain glossary {term: definition}")
    projects: list[ProjectDef] = Field(min_length=1, description="Projects to create")

    @model_validator(mode="after")
    def _check_deps(self) -> "SystemPlan":
        names = {p.name for p in self.projects}
        for p in self.projects:
            for dep in p.depends_on:
                if dep not in names:
                    raise ValueError(
                        f"Project '{p.name}' depends on '{dep}' which is not defined. "
                        f"Available: {names}"
                    )
        return self


# ── Validation ────────────────────────────────────────────────────────────────


def validate_system_plan(plan: SystemPlan) -> list[str]:
    """Run 5 deterministic checks on a SystemPlan.

    Returns a list of error/warning strings (empty = all pass).
    Does NOT raise — the LLM output may have minor issues the human can
    fix during ratification.
    """
    errors: list[str] = []

    # 1. Unique names
    names = [p.name for p in plan.projects]
    seen: set[str] = set()
    for name in names:
        if name in seen:
            errors.append(f"Duplicate project name: '{name}'")
        seen.add(name)

    # 2. DAG acyclic — DFS cycle detection on depends_on
    adj = {p.name: p.depends_on for p in plan.projects}
    visited: set[str] = set()
    stack: set[str] = set()

    def _dfs(name: str) -> bool:
        if name in stack:
            return True
        if name in visited:
            return False
        visited.add(name)
        stack.add(name)
        for dep in adj.get(name, []):
            if _dfs(dep):
                return True
        stack.remove(name)
        return False

    for p in plan.projects:
        if p.name not in visited:
            if _dfs(p.name):
                errors.append(
                    f"Cycle detected in project dependencies involving '{p.name}'"
                )
                break  # one cycle error is enough

    # 3. first_goal foundational (non-empty, >= 10 chars)
    for p in plan.projects:
        if not p.first_goal or not p.first_goal.strip():
            errors.append(f"Project '{p.name}' has no first_goal")
        elif len(p.first_goal.strip()) < 10:
            errors.append(
                f"Project '{p.name}' first_goal is too short "
                f"({len(p.first_goal.strip())} chars, minimum 10)"
            )

    # 4. Project count reasonable (3-7)
    count = len(plan.projects)
    if count < 3:
        errors.append(
            f"System has only {count} project(s); minimum recommended is 3"
        )
    elif count > 7:
        errors.append(
            f"System has {count} projects; maximum recommended is 7"
        )

    # 5. Deps reference valid names — mirror of _check_deps
    names_set = set(names)
    for p in plan.projects:
        for dep in p.depends_on:
            if dep not in names_set:
                errors.append(
                    f"Project '{p.name}' depends on '{dep}' which is not defined"
                )

    return errors


# ── Standards menu ──────────────────────────────────────────────────────────


def standards_menu(families: list[str] | None = None) -> list[dict[str, Any]]:
    """Return domain_standards with non-null service_template for the decompose prompt.

    Args:
        families: Optional family filter (e.g. ``["software"]``). Returns all if None.

    Returns:
        List of ``{slug, name, kind, families, service_template}`` dicts.
    """
    import psycopg
    from psycopg.rows import dict_row
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return []
    try:
        with psycopg.connect(db_url, row_factory=dict_row) as c:
            with c.cursor() as cur:
                if families:
                    cur.execute(
                        """SELECT slug, name, kind, families, service_template
                           FROM domain_standards
                           WHERE active = true
                             AND service_template IS NOT NULL
                             AND families ?| %s
                           ORDER BY name""",
                        (families,),
                    )
                else:
                    cur.execute(
                        """SELECT slug, name, kind, families, service_template
                           FROM domain_standards
                           WHERE active = true AND service_template IS NOT NULL
                           ORDER BY name"""
                    )
                rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("service_template"), str):
                try:
                    d["service_template"] = json.loads(d["service_template"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(d)
        return results
    except Exception:
        logger.exception("Failed to fetch standards menu")
        return []


# ── LLM call ────────────────────────────────────────────────────────────────


DECOMPOSE_SYSTEM_PROMPT = """\
You are a system-architecture decomposer. Given a high-level goal, break it
into a coherent SystemPlan with projects, their dependencies, and initial goals.

SYSTEM PLAN STRUCTURE:
  - system_name: short label for the whole effort
  - system_description: one-line summary
  - glossary: domain terms that projects in this system should share
  - projects: the decomposition output

PROJECT RULES:
  1. Each project is "component" (builds a thing) or "assembly" (composes
     components).  Most projects are components.
  2. Project names must be unique within the system.
  3. Dependencies must form a DAG (no cycles).
  4. first_goal is the initial goal for that project's first plan — a short,
     actionable sentence the development agent can start from.
  5. The first_project's goal should be foundational (scaffold, core types).
  6. Keep the number of projects reasonable (3-7 typical).

Available service standards (use service_template fields to guide architecture):
{standards}

Goal:
{raw_input}

Now produce the SystemPlan JSON."""


def extract_system_plan(
    raw_input: str,
    families: list[str] | None = None,
) -> SystemPlan:
    """Call the LLM to decompose a system-level goal, with up to 3 retries.

    Each retry appends the previous validation errors to the prompt so the
    LLM can fix them.  After 3 failed attempts the last (best-effort) plan
    is returned with a warning — escalation is handled by the caller.

    Args:
        raw_input: The user's high-level goal for the system.
        families: Optional family filter for service templates.

    Returns:
        Validated SystemPlan (or last best-effort plan if all retries fail).
    """
    from backend.planning.meta_planner.llm import call_llm_structured

    menu = standards_menu(families)
    menu_str = json.dumps(menu, indent=2) if menu else "(none — no standards available)"

    errors_history: list[str] = []
    last_plan: SystemPlan | None = None

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        prompt = DECOMPOSE_SYSTEM_PROMPT.format(
            raw_input=raw_input,
            standards=menu_str,
        )

        if errors_history:
            prompt += (
                "\n\nPREVIOUS ATTEMPT ERRORS (fix these in your new response):\n"
                + "\n".join(f"- {e}" for e in errors_history)
            )

        plan = call_llm_structured(prompt, schema=SystemPlan, role="meta_planner")
        last_plan = plan

        validation_errors = validate_system_plan(plan)
        if not validation_errors:
            logger.info("SystemPlan validated on attempt %d/%d", attempt, max_attempts)
            return plan

        errors_history = validation_errors
        for err in validation_errors:
            logger.warning(
                "SystemPlan validation (attempt %d/%d): %s",
                attempt, max_attempts, err,
            )

    # All attempts exhausted — escalate by returning the last best-effort plan
    logger.error(
        "SystemPlan failed validation after %d attempts. "
        "Returning last best-effort plan. Errors: %s",
        max_attempts, "; ".join(errors_history),
    )
    if last_plan is not None:
        return last_plan
    raise RuntimeError(
        f"SystemPlan failed validation after {max_attempts} attempts: "
        f"{'; '.join(errors_history)}"
    )


# ── System proposals CRUD ───────────────────────────────────────────────────


def save_proposal(
    raw_input: str,
    proposal: dict,
    edited: dict | None = None,
) -> int:
    """Insert a system_proposal row.

    Returns:
        The new proposal id.
    """
    import psycopg
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO system_proposals
                   (raw_input, proposal, edited, status, created_at)
                   VALUES (%s, %s, %s, 'proposed', now())
                   RETURNING id""",
                (raw_input, json.dumps(proposal),
                 json.dumps(edited) if edited else None),
            )
            row = cur.fetchone()
        c.commit()
    return row[0] if row else 0


def get_proposal(proposal_id: int) -> dict | None:
    """Fetch a system_proposal row."""
    import psycopg
    from psycopg.rows import dict_row
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM system_proposals WHERE id = %s", (proposal_id,)
            )
            return cur.fetchone()


def list_proposals(status: str | None = None) -> list[dict]:
    """List system_proposals, newest first."""
    import psycopg
    from psycopg.rows import dict_row
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT * FROM system_proposals WHERE status = %s ORDER BY id DESC",
                    (status,),
                )
            else:
                cur.execute(
                    "SELECT * FROM system_proposals ORDER BY id DESC"
                )
            return cur.fetchall()


def update_proposal_status(proposal_id: int, status: str, system_id: str | None = None) -> None:
    """Update system_proposal status (ratified/rejected)."""
    import psycopg
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            if system_id:
                cur.execute(
                    "UPDATE system_proposals SET status = %s, system_id = %s WHERE id = %s",
                    (status, system_id, proposal_id),
                )
            else:
                cur.execute(
                    "UPDATE system_proposals SET status = %s WHERE id = %s",
                    (status, proposal_id),
                )
        c.commit()


# ── Ratification: edit + apply ──────────────────────────────────────────────


def ratify_system(
    proposal_id: int,
    edited: dict | None = None,
    persona_id: str = "default",
) -> str:
    """Ratify a system proposal — transactional creation of system + projects.

    Args:
        proposal_id: The proposal to ratify.
        edited: Optional human edits to the proposal body (replaces proposal).
        persona_id: persona_id for all created projects.

    Returns:
        The new system_id.

    Raises:
        ValueError: If proposal not found or already ratified/rejected.
        RuntimeError: On DB failure.
    """
    import psycopg
    from psycopg.rows import dict_row

    proposal = get_proposal(proposal_id)
    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found")
    if proposal["status"] != "proposed":
        raise ValueError(
            f"Proposal {proposal_id} is '{proposal['status']}', not 'proposed'"
        )

    payload = edited or proposal["proposal"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    # Build system_id
    from shared.models import _slug
    system_id = _slug(payload.get("system_name", "system"))
    if not system_id:
        system_id = f"sys_{os.urandom(4).hex()}"

    db_url = os.environ["DATABASE_URL"]

    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            # 1. Create or find system
            cur.execute(
                "SELECT system_id FROM systems WHERE system_id = %s", (system_id,)
            )
            existing = cur.fetchone()
            if existing:
                system_id = f"{system_id}-{os.urandom(2).hex()}"

            cur.execute(
                """INSERT INTO systems
                   (system_id, name, description, glossary, persona_id, goal, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, now())
                   RETURNING system_id""",
                (
                    system_id,
                    payload.get("system_name", system_id),
                    payload.get("system_description", ""),
                    json.dumps(payload.get("glossary", {})),
                    persona_id,
                    proposal.get("raw_input"),
                ),
            )
            sys_row = cur.fetchone()
            system_id = sys_row["system_id"]

            # 2. Create projects in dependency order
            projects = payload.get("projects", [])
            project_ids: dict[str, str] = {}

            for p in projects:
                pid = f"{system_id}-{_slug(p.get('name', ''))}"
                kind = p.get("kind", "component")
                name = p.get("name", pid)

                cur.execute(
                    "SELECT project_id FROM projects WHERE project_id = %s", (pid,)
                )
                if cur.fetchone():
                    pid = f"{pid}-{os.urandom(2).hex()}"

                cur.execute(
                    """INSERT INTO projects
                       (project_id, system_id, name, kind, persona_id, status,
                        description, repo_path, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, now(), now())
                       RETURNING project_id""",
                    (
                        pid, system_id, name, kind, persona_id,
                        p.get("description", ""),
                        pid,
                    ),
                )
                row = cur.fetchone()
                project_ids[name] = row["project_id"]

            # 3. Create dependency edges + queue pending_goals for this project
                for dep_name in p.get("depends_on", []):
                    dep_id = project_ids.get(dep_name)
                    if dep_id:
                        cur.execute(
                            """INSERT INTO project_dependencies
                               (project_id, depends_on_project_id, dep_name, created_at)
                               VALUES (%s, %s, %s, now())
                               ON CONFLICT DO NOTHING""",
                            (project_ids[p["name"]], dep_id, dep_name),
                        )

                if not p.get("first_goal"):
                    continue
                pid = project_ids[p["name"]]
                dep_ids = [
                    project_ids[d] for d in p.get("depends_on", [])
                    if d in project_ids
                ]
                wait_for = json.dumps(dep_ids) if dep_ids else None
                if wait_for:
                    cur.execute(
                        """INSERT INTO pending_goals
                           (project_id, raw_input, origin, status, wait_for, created_at, updated_at)
                           VALUES (%s, %s, %s, 'pending', %s, now(), now())""",
                        (pid, p["first_goal"], "system_goal", wait_for),
                    )
                else:
                    cur.execute(
                        """INSERT INTO pending_goals
                           (project_id, raw_input, origin, status, created_at, updated_at)
                           VALUES (%s, %s, %s, 'pending', now(), now())""",
                        (pid, p["first_goal"], "system_goal"),
                    )

        c.commit()

    # Mark proposal as ratified
    update_proposal_status(proposal_id, "ratified", system_id)

    logger.info(
        "Ratified proposal %d → system=%s with %d projects",
        proposal_id, system_id, len(projects),
    )
    # Fire drain in a background thread so the ratification response is
    # not blocked by drain_pending's self-call to POST /goal on the same
    # process.
    import threading
    _t = threading.Thread(target=drain_pending, daemon=True)
    _t.start()

    return system_id


# ── Dep SHAs recording ──────────────────────────────────────────────────────


def record_dep_shas(project_id: str, run_id: str) -> dict[str, str]:
    """Record git SHAs of all dependency projects into ``runs.dep_shas``.

    Called after worktree creation to capture the exact dependency versions
    consumed by this run.

    Returns:
        Dict of ``{dep_project_id: sha}``.
    """
    import subprocess
    import psycopg
    from psycopg.rows import dict_row
    db_url = os.environ["DATABASE_URL"]
    workspace_root = os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")
    dep_shas: dict[str, str] = {}

    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT pd.dep_name, pd.depends_on_project_id, p.name, p.repo_path
                   FROM project_dependencies pd
                   JOIN projects p ON pd.depends_on_project_id = p.project_id
                   WHERE pd.project_id = %s""",
                (project_id,),
            )
            deps = cur.fetchall()

        for dep in deps:
            dep_id = dep["depends_on_project_id"]
            dep_path = dep.get("repo_path") or dep_id
            repo_dir = f"{workspace_root}/{dep_path}"

            try:
                result = subprocess.run(
                    ["git", "-C", repo_dir, "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    dep_shas[dep_id] = result.stdout.strip()
                else:
                    dep_shas[dep_id] = "unknown"
            except Exception:
                dep_shas[dep_id] = "unknown"

        with c.cursor() as cur:
            if dep_shas:
                cur.execute(
                    "UPDATE runs SET dep_shas = %s WHERE id = %s",
                    (json.dumps(dep_shas), run_id),
                )
        c.commit()

    return dep_shas


# ── Pending goal lifecycle ──────────────────────────────────────────────────


def queue_first_goals(project_ids: list[str], raw_input: str, origin: str = "system_goal") -> int:
    """Queue goals for projects into pending_goals.

    Returns:
        Number of goals queued.
    """
    import psycopg
    db_url = os.environ["DATABASE_URL"]
    count = 0
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            for pid in project_ids:
                cur.execute(
                    """INSERT INTO pending_goals
                       (project_id, raw_input, origin, status, created_at, updated_at)
                       VALUES (%s, %s, %s, 'pending', now(), now())""",
                    (pid, raw_input, origin),
                )
                count += 1
        c.commit()
    return count


def has_merged_master(project_id: str) -> bool:
    """Check if a project has ever had a successful merge to master."""
    import psycopg
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM runs
                   WHERE project_id = %s
                     AND worktree_status = 'merged'
                   LIMIT 1""",
                (project_id,),
            )
            return cur.fetchone() is not None


def drain_pending() -> int:
    """Emit ``sys.goal_queued`` events for pending goals that are ready.

    For each pending goal with empty ``wait_for``:
      1. Write ``sys.goal_queued`` to the transactional outbox.
      2. Mark status = ``in_progress``.

    For each pending goal with ``wait_for`` projects:
      - Check if all depended-on projects have finished a run (no active run).
      - If yes, clear ``wait_for`` (next drain cycle picks it up).

    Intake-svc consumes the outbox events and handles the full
    ``/goal`` → clarify → ratify lifecycle.
    """
    import psycopg
    from psycopg.rows import dict_row
    db_url = os.environ["DATABASE_URL"]
    submitted = 0

    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            # Advisory lock — retry a few times on transient contention
            import time as _time
            _acquired = False
            for _attempt in range(3):
                cur.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext('drain_pending')) AS acquired"
                )
                lock_row = cur.fetchone()
                if lock_row and lock_row.get("acquired"):
                    _acquired = True
                    break
                if _attempt < 2:
                    _time.sleep(1)
            if not _acquired:
                logger.info("drain_pending: could not acquire lock after 3 attempts — skipping")
                return 0

            cur.execute(
                """SELECT * FROM pending_goals
                   WHERE status = 'pending'
                   ORDER BY
                     CASE WHEN wait_for IS NULL OR wait_for = '[]'::jsonb THEN 0 ELSE 1 END,
                     id ASC
                   LIMIT 10
                   FOR UPDATE SKIP LOCKED"""
            )
            rows = cur.fetchall()

            for row in rows:
                wait_for = row.get("wait_for")
                if isinstance(wait_for, str):
                    try:
                        wait_for = json.loads(wait_for)
                    except (json.JSONDecodeError, TypeError):
                        wait_for = []
                if not isinstance(wait_for, list):
                    wait_for = []

                if wait_for:
                    # Check each wait_for project — has a terminal run?
                    ready = True
                    for wf in wait_for:
                        cur.execute(
                            """SELECT 1 FROM runs
                               WHERE project_id = %s
                                 AND state IN ('done','failed','cancelled')
                               LIMIT 1""",
                            (wf,),
                        )
                        if not cur.fetchone():
                            ready = False
                            break
                    if not ready:
                        continue
                    # Clear wait_for — next cycle picks it up
                    cur.execute(
                        "UPDATE pending_goals SET wait_for = '[]' WHERE id = %s",
                        (row["id"],),
                    )
                    continue

                # Emit event for intake — it will POST /goal and handle lifecycle
                try:
                    import time as _time
                    cur.execute(
                        """INSERT INTO outbox
                           (routing_key, payload, contracts_version, created_at)
                           VALUES (%s, %s, %s, now())""",
                        (
                            "sys.goal_queued",
                            json.dumps({
                                "project_id": row["project_id"],
                                "raw_input": row["raw_input"],
                                "origin": row.get("origin", "system_goal"),
                                "ts": _time.time(),
                            }),
                            "1.0",
                        ),
                    )
                    cur.execute(
                        """UPDATE pending_goals
                           SET status = 'in_progress', updated_at = now()
                           WHERE id = %s""",
                        (row["id"],),
                    )
                    submitted += 1
                except Exception as exc:
                    logger.warning("Drain failed for pending_goal %s: %s", row["id"], exc)
                    cur.execute(
                        """UPDATE pending_goals
                           SET last_error = %s, updated_at = now()
                           WHERE id = %s""",
                        (str(exc)[:500], row["id"]),
                    )
        c.commit()

    return submitted


def get_system_queue(system_id: str, status: str | None = None) -> list[dict]:
    """Return pending_goals for all projects in a system.

    Used by ``GET /system/{id}/queue``.
    """
    import psycopg
    from psycopg.rows import dict_row
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            if status:
                cur.execute(
                    """SELECT pg.* FROM pending_goals pg
                       JOIN projects p ON pg.project_id = p.project_id
                       WHERE p.system_id = %s AND pg.status = %s
                       ORDER BY pg.id""",
                    (system_id, status),
                )
            else:
                cur.execute(
                    """SELECT pg.* FROM pending_goals pg
                       JOIN projects p ON pg.project_id = p.project_id
                       WHERE p.system_id = %s
                       ORDER BY pg.id""",
                    (system_id,),
                )
            return cur.fetchall()


def slug(name: str, max_len: int = 63) -> str:
    """Derive a URL-safe project/system ID from a name.

    Conversion rules:
    1. Lowercase and strip whitespace.
    2. Replace runs of non-alphanumeric characters (except hyphens) with a hyphen.
    3. Collapse consecutive hyphens.
    4. Strip leading/trailing hyphens.
    5. Truncate to *max_len* (default 63 — typical PostgreSQL identifier limit).

    Examples:
        ``slug("My Project")`` → ``"my-project"``
        ``slug("Hello   World!!!")`` → ``"hello-world"``
        ``slug("alpha/1.0-beta")`` → ``"alpha10-beta"``
    """
    import re as _re

    s = name.strip().lower()
    s = _re.sub(r"[^a-z0-9-]", "-", s)
    s = _re.sub(r"-+", "-", s)
    s = s.strip("-")
    if max_len > 0:
        s = s[:max_len].strip("-")
    return s or "untitled"
