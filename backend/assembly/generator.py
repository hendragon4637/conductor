"""Assembly generator — deterministic docker-compose generation from system projects.

File 04 of the system layer.

Generates:
  - docker-compose.yml (service topology)
  - Dockerfile per runnable component project
  - Service descriptors for the assembly project's workspace.json

Every output is deterministic: same system_id + dep_shas → same compose output.
"""

from __future__ import annotations

import json
import logging
import os
import re as _re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Default templates ─────────────────────────────────────────────────────────

_DEFAULT_DOCKERFILE_PYTHON = """\
FROM python:{version}-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE {port}
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
"""

_DEFAULT_DOCKERFILE_NODE = """\
FROM node:{version}-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
EXPOSE {port}
CMD ["node", "index.js"]
"""

_DEFAULT_SERVICE_TEMPLATE: dict[str, Any] = {
    "image": "python:3.12-slim",
    "build": {"context": ".", "dockerfile": "Dockerfile"},
    "port": 8000,
    "expose": [8000],
    "env": {"REQUIRED": ["DATABASE_URL"], "OPTIONAL": []},
    "healthcheck": {
        "test": ["CMD", "curl", "-f", "http://localhost:{port}/health"],
        "interval": "30s",
        "timeout": "10s",
        "retries": 3,
    },
    "depends_on": {"condition": "service_healthy"},
}


# ── Slug derivation ───────────────────────────────────────────────────────────


def _service_slug(project_name: str) -> str:
    """Derive a DNS-safe service name from a project name."""
    s = project_name.strip().lower()
    s = _re.sub(r"[^a-z0-9-]", "-", s)
    s = _re.sub(r"-+", "-", s)
    return s.strip("-") or "svc"


# ── Port collision resolution ─────────────────────────────────────────────────


def _resolve_ports(
    services: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Assign unique host ports to each service with collision resolution.

    Each service dict must have a ``template`` key with an optional ``port``.
    Returns the same list with ``assigned_host_port`` added to each entry.
    """
    used_ports: set[int] = set()
    resolved: list[dict[str, Any]] = []

    for svc in services:
        template = svc.get("template", {})
        container_port = template.get("port", 8000)
        host_port = container_port

        # Shift until unique
        while host_port in used_ports:
            host_port += 1

        used_ports.add(host_port)
        resolved.append({**svc, "assigned_host_port": host_port})

    return resolved


# ── Dockerfile generation ─────────────────────────────────────────────────────


def _generate_dockerfile(
    project_name: str,
    template: dict[str, Any],
    deps_dir: str | None = None,
) -> str:
    """Generate a Dockerfile for a service based on its service_template.

    Args:
        project_name: Project name (used for fallback detection).
        template: The service_template JSONB dict from domain_standards.
        deps_dir: Optional path to deps/ directory for COPY instructions.

    Returns:
        Dockerfile content as a string.
    """
    port = template.get("port", 8000)
    image = template.get("image", "")

    # Detect language from image or project name
    _img_lower = image.lower() if image else ""
    if "node" in _img_lower:
        tmpl = _DEFAULT_DOCKERFILE_NODE
        version = _re.search(r"(\d+)", image)
        ver = version.group(1) if version else "20"
    else:
        tmpl = _DEFAULT_DOCKERFILE_PYTHON
        version = _re.search(r"(\d+\.\d+)", image)
        ver = version.group(1) if version else "3.12"

    dockerfile = tmpl.format(version=ver, port=port)

    # Inject deps/ COPY if provided
    if deps_dir:
        dep_copy = f"\n# Dependency references\nCOPY {deps_dir} {deps_dir}\n"
        dockerfile += dep_copy

    return dockerfile


# ── Core assembly generator ───────────────────────────────────────────────────


def generate_assembly(system_id: str, workspace_root: str | None = None) -> dict[str, Any]:
    """Generate a deterministic docker-compose assembly for a system.

    Steps:
    1. Query all component/assembly projects in the system.
    2. For each project, resolve its service_template via domain_standards.
    3. Resolve inter-service dependencies from project_dependencies.
    4. Resolve port collisions.
    5. Generate docker-compose.yml content.
    6. Generate Dockerfiles per runnable project.
    7. Write files to the assembly project workspace (or return as dict).

    Args:
        system_id: The system to assemble.
        workspace_root: If provided, files are written to disk under
            ``{workspace_root}/{assembly_project}/``.

    Returns:
        Dict with keys:
        - ``compose_yaml``: docker-compose.yml content (str)
        - ``dockerfiles``: dict of ``{project_name: Dockerfile_content}``
        - ``services``: list of service dicts (for workspace.json)
        - ``errors``: list of error messages (empty = clean)
    """
    errors: list[str] = []
    import psycopg
    from psycopg.rows import dict_row

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return {
            "compose_yaml": "",
            "dockerfiles": {},
            "services": [],
            "errors": ["DATABASE_URL not set"],
        }

    try:
        with psycopg.connect(db_url, row_factory=dict_row) as c:
            with c.cursor() as cur:
                # 1. Fetch projects in the system (ordered by dependency level)
                cur.execute(
                    """WITH RECURSIVE dep_order AS (
                           SELECT project_id, name, kind, description, 0 AS level
                           FROM projects
                           WHERE system_id = %s
                             AND NOT EXISTS (
                               SELECT 1 FROM project_dependencies
                               WHERE project_id = projects.project_id
                             )
                           UNION
                           SELECT p.project_id, p.name, p.kind, p.description, d.level + 1
                           FROM projects p
                           JOIN project_dependencies pd ON pd.project_id = p.project_id
                           JOIN dep_order d ON d.project_id = pd.depends_on_project_id
                       )
                       SELECT DISTINCT project_id, name, kind, description, level
                        FROM dep_order
                        ORDER BY level, name""",
                    (system_id,),
                )
                projects = cur.fetchall()

                if not projects:
                    return {
                        "compose_yaml": "",
                        "dockerfiles": {},
                        "services": [],
                        "errors": [f"No projects found in system {system_id}"],
                        "env_required": [],
                        "env_example": "",
                    }

                # 2. Fetch inter-service dependency edges
                cur.execute(
                    """SELECT pd.project_id, pd.depends_on_project_id,
                              p2.name AS depends_on_name
                       FROM project_dependencies pd
                       JOIN projects p ON p.project_id = pd.project_id
                       JOIN projects p2 ON p2.project_id = pd.depends_on_project_id
                       WHERE p.system_id = %s""",
                    (system_id,),
                )
                dep_edges = cur.fetchall()

                # 3. For each project, resolve service_template
                services: list[dict[str, Any]] = []
                for proj in projects:
                    pid = proj["project_id"]
                    pname = proj["name"]
                    kind = proj.get("kind", "component")

                    # Get the latest run's standard → service_template
                    cur.execute(
                        """SELECT ds.slug, ds.service_template
                           FROM domain_standards ds
                           JOIN runs r ON r.standard_ids @> ARRAY[ds.id]
                           WHERE r.project_id = %s
                             AND ds.service_template IS NOT NULL
                           ORDER BY r.created_at DESC
                           LIMIT 1""",
                        (pid,),
                    )
                    std_row = cur.fetchone()

                    template: dict[str, Any] = dict(_DEFAULT_SERVICE_TEMPLATE)
                    std_slug = ""
                    if std_row:
                        raw = std_row["service_template"]
                        if isinstance(raw, str):
                            try:
                                raw = json.loads(raw)
                            except (json.JSONDecodeError, TypeError):
                                raw = {}
                        if isinstance(raw, dict):
                            template = {**template, **raw}
                        std_slug = std_row["slug"] or ""

                    # Get dep_shas from latest merged run
                    cur.execute(
                        """SELECT dep_shas FROM runs
                           WHERE project_id = %s
                             AND worktree_status = 'merged'
                           ORDER BY created_at DESC
                           LIMIT 1""",
                        (pid,),
                    )
                    dep_row = cur.fetchone()
                    dep_shas: dict[str, str] = {}
                    if dep_row and dep_row["dep_shas"]:
                        raw = dep_row["dep_shas"]
                        if isinstance(raw, str):
                            try:
                                raw = json.loads(raw)
                            except (json.JSONDecodeError, TypeError):
                                raw = {}
                        if isinstance(raw, dict):
                            dep_shas = raw

                                # Pre-built image tag from dep_shas
                    image_tag = std_slug
                    if dep_shas:
                        _sha_values = [v[:12] for v in dep_shas.values() if v]
                        if _sha_values:
                            image_tag = f"{std_slug}:{'-'.join(_sha_values)[:64]}"

                    # Build dependency list for this service
                    depends_on: list[dict[str, str]] = []
                    for edge in dep_edges:
                        if edge["project_id"] == pid:
                            depends_on.append({
                                "service": _service_slug(edge["depends_on_name"]),
                                "condition": template.get("depends_on", {}).get(
                                    "condition", "service_started"
                                ),
                            })

                    services.append({
                        "project_id": pid,
                        "name": pname,
                        "slug": _service_slug(pname),
                        "kind": kind,
                        "template": template,
                        "dep_shas": dep_shas,
                        "depends_on": depends_on,
                        "standard_slug": std_slug,
                        "image_tag": image_tag,
                    })

    except Exception as exc:
        logger.exception("Failed to generate assembly for system %s", system_id)
        return {
            "compose_yaml": "",
            "dockerfiles": {},
            "services": [],
            "errors": [f"Assembly generation failed: {exc}"],
        }

    # 4. Resolve port collisions
    services = _resolve_ports(services)

    # 5. Build docker-compose.yml
    compose = _build_compose_yaml(services)

    # 6. Collect required env vars across all services (04.2 — agent fills values)
    env_required: set[str] = set()
    env_example_lines: list[str] = []
    for svc in services:
        tpl_keys = svc.get("template", {}).get("env_required", [])
        if isinstance(tpl_keys, list):
            for k in tpl_keys:
                if k and k not in env_required:
                    env_required.add(k)
                    env_example_lines.append(f"# {svc['slug']}")
                    env_example_lines.append(f"{k}=")
    env_example = "\n".join(env_example_lines) if env_example_lines else ""

    # 7. Generate Dockerfiles
    dockerfiles: dict[str, str] = {}
    for svc in services:
        if svc["kind"] == "component":
            df = _generate_dockerfile(
                svc["name"],
                svc["template"],
                deps_dir="deps" if svc.get("dep_shas") else None,
            )
            dockerfiles[svc["slug"]] = df

    # 8. Write to disk if workspace_root provided
    if workspace_root:
        assembly_projects = [p for p in projects if p.get("kind") == "assembly"]
        if assembly_projects:
            ap = assembly_projects[0]
            out_dir = Path(workspace_root) / ap["project_id"]
            out_dir.mkdir(parents=True, exist_ok=True)

            (out_dir / "docker-compose.yml").write_text(compose)
            for slug, df_content in dockerfiles.items():
                (out_dir / f"Dockerfile.{slug}").write_text(df_content)

            # .env.example for cross-service env values
            if env_example:
                (out_dir / ".env.example").write_text("# Cross-service environment variables\n"
                                                       "# Agent: fill in the values below\n\n"
                                                       + env_example + "\n")
                logger.info("Wrote .env.example with %d env vars", len(env_required))

            # Service descriptor
            svc_desc = json.dumps(
                {
                    "system_id": system_id,
                    "services": [
                        {
                            "name": s["name"],
                            "slug": s["slug"],
                            "port": s["assigned_host_port"],
                            "dep_shas": s.get("dep_shas", {}),
                            "depends_on": s.get("depends_on", []),
                        }
                        for s in services
                    ],
                },
                indent=2,
            )
            (out_dir / "workspace.json").write_text(svc_desc)
            logger.info(
                "Wrote assembly for system %s to %s (%d services, %d Dockerfiles)",
                system_id, out_dir, len(services), len(dockerfiles),
            )

    return {
        "compose_yaml": compose,
        "dockerfiles": dockerfiles,
        "services": services,
        "errors": errors,
        "env_required": sorted(env_required),
        "env_example": env_example,
    }


def _build_compose_yaml(services: list[dict[str, Any]]) -> str:
    """Build docker-compose.yml content from resolved services."""
    lines: list[str] = [
        "# Generated by Conductor Assembly Generator",
        "# Do not edit manually — changes will be overwritten",
        "",
        'version: "3.9"',
        "",
        "services:",
    ]

    for svc in services:
        slug = svc["slug"]
        template = svc["template"]
        host_port = svc["assigned_host_port"]
        container_port = template.get("port", 8000)

        lines.append(f"  {slug}:")
        image = template.get("image", "")
        build_conf = template.get("build", {})

        if build_conf and build_conf.get("context"):
            lines.append(f'    build:')
            lines.append(f'      context: {build_conf["context"]}')
            df = build_conf.get("dockerfile", "")
            if df:
                lines.append(f'      dockerfile: {df}')
        elif image:
            lines.append(f'    image: {image}')
        else:
            lines.append(f'    build: .')

        lines.append(f'    ports:')
        lines.append(f'      - "{host_port}:{container_port}"')

        # Environment variables
        env_conf = template.get("env", {})
        required_env = env_conf.get("REQUIRED", [])
        optional_env = env_conf.get("OPTIONAL", [])
        if required_env or optional_env:
            lines.append(f'    environment:')
            for var in required_env:
                lines.append(f'      {var}: "${{{var}}}")' if "{" not in str(var) else f'      - {var}')
            for var in optional_env:
                lines.append(f'      # {var} (optional)')

        # Depends on
        depends_on = svc.get("depends_on", [])
        if depends_on:
            lines.append(f'    depends_on:')
            for dep in depends_on:
                cond = dep.get("condition", "service_started")
                lines.append(f'      {dep["service"]}:')
                lines.append(f'        condition: {cond}')

        # Healthcheck
        hc = template.get("healthcheck", {})
        if hc.get("test"):
            lines.append(f'    healthcheck:')
            test_cmd = json.dumps(hc["test"]) if isinstance(hc["test"], list) else f'["{hc["test"]}"]'
            lines.append(f'      test: {test_cmd}')
            lines.append(f'      interval: {hc.get("interval", "30s")}')
            lines.append(f'      timeout: {hc.get("timeout", "10s")}')
            lines.append(f'      retries: {hc.get("retries", 3)}')

        # Volumes
        volumes = template.get("volumes", [])
        if volumes:
            lines.append(f'    volumes:')
            for vol in volumes:
                lines.append(f'      - {vol}')

        lines.append("")

    return "\n".join(lines)


# ── Assembly gates ─────────────────────────────────────────────────────────────


def check_compose_valid(compose_yaml: str) -> bool:
    """Gate: validate that the generated compose YAML is parseable."""
    if not compose_yaml or not compose_yaml.strip():
        return False
    try:
        import yaml
        data = yaml.safe_load(compose_yaml)
        return isinstance(data, dict) and "services" in data
    except Exception:
        return False


def check_compose_up(timeout: int = 60) -> bool:
    """Gate: run ``docker compose up --wait`` and check exit code.

    Runs in the assembly project directory.  Requires ``docker`` CLI.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "--wait", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.warning("compose up --wait failed: %s", exc)
        return False


def check_e2e_pass(endpoint: str = "http://localhost:8000/health") -> bool:
    """Gate: hit the health endpoint of the assembled system."""
    import urllib.request
    try:
        resp = urllib.request.urlopen(endpoint, timeout=30)
        return resp.status == 200
    except Exception:
        return False


def check_compose_down() -> bool:
    """Gate: tear down the composed system cleanly."""
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "compose", "down", "-v"],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Assembly eligibility guard ─────────────────────────────────────────────────


_last_assembly_at: dict[str, float] = {}
_MIN_ASSEMBLY_INTERVAL = 300.0  # 5 minutes


def is_assembly_eligible(system_id: str) -> tuple[bool, str]:
    """Check if a system is eligible for a new assembly (debounce).

    Returns:
        ``(eligible: bool, reason: str)``
    """
    import time

    last = _last_assembly_at.get(system_id, 0.0)
    elapsed = time.time() - last
    if elapsed < _MIN_ASSEMBLY_INTERVAL:
        remaining = int(_MIN_ASSEMBLY_INTERVAL - elapsed)
        return False, f"Assembly debounced: wait {remaining}s (last was {int(elapsed)}s ago)"

    _last_assembly_at[system_id] = time.time()
    return True, "eligible"
