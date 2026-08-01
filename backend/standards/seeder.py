"""Seed domain_standards with python-backend and react-frontend standards.

This is a one-time seed script that inserts the initial domain standards
into the database. It also links existing capabilities and generates
tool manifests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


SCAFFOLDS_DIR = Path(os.environ.get("SCAFFOLDS_DIR", "/opt/aipc/conductor/scaffolds_store"))
SEED_PACK_DIR = Path(os.environ.get("DOMAIN_SEED_DIR", "/opt/aipc/notes/conductor_domain_seed_v3"))


def _scaffold_dir(slug: str) -> Path:
    """Resolve scaffold directory for a slug.

    Scans scaffolds_store for versioned directories (``{slug}-v*``) and returns
    the highest version.  Falls back to the bare slug directory.
    """
    base = SCAFFOLDS_DIR / slug
    # Look for versioned directories: {slug}-v{int}
    candidates = sorted(
        (d for d in SCAFFOLDS_DIR.iterdir()
         if d.is_dir() and d.name.startswith(f"{slug}-v")),
        key=lambda d: _parse_version_suffix(d.name, slug),
    )
    if candidates:
        return candidates[-1]  # highest version
    # Fallback: bare slug directory (legacy)
    if base.exists():
        return base
    # Fallback: same slug treated as path
    return base


def _parse_version_suffix(dirname: str, slug: str) -> int:
    """Extract version integer from a ``{slug}-v{int}`` directory name."""
    suffix = dirname[len(slug) + 1:]  # strip "slug-"
    if suffix.startswith("v") and suffix[1:].isdigit():
        return int(suffix[1:])
    return 0


def _read_conventions(slug: str) -> str:
    """Read the AGENTS.md conventions file from scaffolds_store."""
    path = _scaffold_dir(slug) / "AGENTS.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _compute_tree_sha(slug: str) -> str:
    """Compute SHA-256 of the scaffold tree (all file paths + contents, sorted)."""
    scaffold_dir = _scaffold_dir(slug)
    if not scaffold_dir.exists():
        return ""
    hasher = hashlib.sha256()
    for path in sorted(scaffold_dir.rglob("*")):
        if path.is_file() and path.name != "AGENTS.md":
            rel = str(path.relative_to(scaffold_dir))
            hasher.update(rel.encode())
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _build_scaffold_tree(slug: str) -> list[dict[str, Any]]:
    """Walk the scaffold directory and build a scaffold_tree JSONB list."""
    scaffold_dir = _scaffold_dir(slug)
    if not scaffold_dir.exists():
        return []

    tree = []
    for path in sorted(scaffold_dir.rglob("*")):
        if path.is_file() and path.name != "AGENTS.md":
            rel = str(path.relative_to(scaffold_dir))
            content = path.read_text(encoding="utf-8")
            tree.append({"path": rel, "content": content, "type": "file"})
    return tree


def _build_artifact_spec(slug: str) -> dict[str, Any]:
    """Derive artifact_spec from scaffold tree patterns."""
    specs = {
        "python-backend": {
            "source_code": ["src/**/*.py"],
            "test": ["tests/**/*.py"],
            "config": ["pyproject.toml", "setup.cfg", "setup.py"],
            "documentation": ["docs/**", "README.md"],
            "delivery_spec": {
                "form": "served_url",
                "check": "uvicorn (or compose) starts and health URL responds",
                "package_cmd": "uvicorn app.main:app / docker compose up",
                "platform_note": "web app: ready = served, not double-click",
            },
        },
        "react-frontend": {
            "source_code": ["src/**/*.{ts,tsx,js,jsx}"],
            "test": ["tests/**/*.{ts,tsx,js,jsx}", "**/*.test.{ts,tsx}"],
            "config": ["package.json", "tsconfig.json", "next.config.*"],
            "public_assets": ["public/**"],
            "styles": ["src/**/*.css", "src/**/*.module.css"],
            "delivery_spec": {
                "form": "served_url",
                "check": "npm run build passes; npm run preview serves the app",
                "package_cmd": "vite build → static dist/ (deployable anywhere)",
                "platform_note": "web app: ready = built + servable dist/",
            },
        },
        "python-gui": {
            "source_code": ["src/**/*.py"],
            "test": ["tests/**/*.py"],
            "config": ["pyproject.toml", "app.spec"],
            "resources": ["src/__PKG__/resources/**"],
            "delivery_spec": {
                "form": "standalone_executable_dir",
                "check": "app.spec builds; packaged executable launches under xvfb and opens its window",
                "package_cmd": "pyinstaller app.spec → dist/",
                "platform_note": "desktop GUI: ready = launchable binary dir, headless smoke via xvfb-run",
            },
        },
        "arduino": {
            "source_code": ["src/**/*.cpp", "include/**/*.h"],
            "test": ["test/**/*.cpp"],
            "config": ["platformio.ini"],
            "lib": ["lib/**"],
            "delivery_spec": {
                "form": "compiled_firmware",
                "check": "platformio build -e uno compiles clean; native tests pass",
                "package_cmd": "pio run -e uno → .pio/build/uno/firmware.hex",
                "platform_note": "firmware: build + test only — no hardware run; L4 verdict skipped_no_hardware",
            },
        },
        "design-layout": {
            "design": ["DESIGN.md"],
            "briefs": ["brief/**/*.md"],
            "work": ["work/**"],
            "exports": ["exports/**"],
            "delivery_spec": {
                "form": "viewable_artifacts",
                "check": "exports/ contains every brief-demanded format, non-empty and openable; RUN.md VIEW describes each",
                "package_cmd": "chromium --headless --print-to-pdf / --screenshot (or open-design export pipeline if installed)",
                "platform_note": "weak-oracle domain: L1 = existence/validity/token-conformance/checklist; aesthetic quality judged by L2 against BRIEF.md + DESIGN.md",
            },
        },
    }
    if slug in specs:
        return specs[slug]
    # Reference seed pack for standards without authored spec entries
    seed_path = SEED_PACK_DIR / f"{slug}-spec.json"
    if seed_path.exists():
        data = json.loads(seed_path.read_text(encoding="utf-8"))
        return data.get("artifact_spec", {})
    return {}


def _build_tool_manifest(slug: str) -> list[dict[str, Any]]:
    """Build tool manifest for the domain — references to vetted catalog tools."""
    # This is a starting seed; it will be enriched as the catalog grows
    manifests = {
        "python-backend": [
            {"name": "pytest", "kind": "cli", "domain": "testing"},
            {"name": "ruff", "kind": "cli", "domain": "linting"},
            {"name": "uv", "kind": "cli", "domain": "package_management"},
        ],
        "react-frontend": [
            {"name": "typescript", "kind": "cli", "domain": "language"},
            {"name": "eslint", "kind": "cli", "domain": "linting"},
            {"name": "prettier", "kind": "cli", "domain": "formatting"},
        ],
    }
    return manifests.get(slug, [])


# ── Runtime templates (File 01, guide 01.8) ────────────────────────────────────
# How L4 runs a component. Tokens (__PKG__, __APP__) are substituted at manifest
# generation; ${PORT}/${SCRIPT} stay for runtime. golang-backend skipped per
# user decision (01.2). Commands aligned with the real scaffolds (guide 01.9).

SERVICE_TEMPLATES: dict[str, dict[str, Any]] = {
    "python-backend": {
        "runnable": True,
        "port": 8000,
        "health": "/health",
        "env_required": ["DATABASE_URL"],
        "env_l4_defaults": {"DATABASE_URL": "sqlite:///./l4.db"},
        "commands": {
            "setup": 'uv venv .venv && uv pip install -e ".[dev]"',
            "run": "uv run uvicorn __PKG__.main:app --host 0.0.0.0 --port ${PORT}",
            "test": "uv run pytest -q",
            "verify": "bash gates.sh",
        },
    },
    "react-frontend": {
        "runnable": True,
        "port": 5173,
        "health": "/",
        "env_required": [],
        "env_l4_defaults": {"VITE_API_URL": "http://localhost:8000"},
        "commands": {
            "setup": "npm install",
            "build": "npm run build",
            "run": "npm run preview -- --port ${PORT} --host 0.0.0.0",
            "test": "npm test -- --run",
            "verify": "bash gates.sh",
        },
    },
    "python-gui": {
        "runnable": False,
        "env_required": [],
        "env_l4_defaults": {},
        "commands": {
            "setup": 'uv venv .venv && uv pip install -e ".[dev]"',
            "build": "uv run pyinstaller --noconfirm app.spec",
            "launch": "xvfb-run -a dist/__APP__/__APP__ --smoke",
            "script": "xvfb-run -a dist/__APP__/__APP__ --script ${SCRIPT}",
            "test": "uv run pytest -q",
            "verify": "bash gates.sh",
        },
    },
    "arduino": {
        "runnable": False,
        "env_required": [],
        "env_l4_defaults": {},
        "commands": {
            "setup": "pip install platformio",
            "build": "pio run -e uno",
            "test": "pio test -e native",
            "verify": "bash gates.sh",
        },
    },
    "design-layout": {
        "runnable": False,
        "env_required": [],
        "env_l4_defaults": {},
        "commands": {
            "setup": "true",
            "verify": "bash gates.sh",
            "view": "ls exports/",
        },
    },
    "cli-tool": {
        "runnable": False,
        "env_required": [],
        "env_l4_defaults": {},
        "commands": {
            "setup": "uv venv .venv && uv pip install -e .",
            "help": "uv run __PKG__ --help",
            "invoke": "uv run __PKG__",
            "test": "uv run pytest -q",
            "verify": "bash gates.sh",
        },
    },
    "python-etl": {
        "runnable": False,
        "profile": "jobs",
        "env_required": [],
        "env_l4_defaults": {},
        "commands": {
            "setup": 'uv venv .venv && uv pip install -e ".[dev]"',
            "run": "uv run python -m __PKG__.pipeline --input data/input/sample.csv --output data/output/out.csv",
            "test": "uv run pytest -q",
            "verify": "bash gates.sh",
        },
    },
    "tech-docs": {
        "runnable": False,
        "env_required": [],
        "env_l4_defaults": {},
        "commands": {
            "setup": "true",
            "verify": "bash gates.sh",
            "view": "ls docs/",
        },
    },
}


# ── Publish manifests (File 01, guide 01.10) ───────────────────────────────────
# What a component contributes to a system. Declared, never inferred.
PUBLISH_MANIFESTS: dict[str, dict[str, Any]] = {
    "python-backend": {
        "image_tag": True,
        "files": ["RUN.md", ".env.example", ".conductor/workspace.json"],
        "artifacts": [],
    },
    "react-frontend": {
        "image_tag": True,
        "files": ["RUN.md", ".env.example", ".conductor/workspace.json"],
        "artifacts": [],
    },
    "python-etl": {
        "image_tag": False,
        "files": ["RUN.md", ".conductor/workspace.json"],
        "artifacts": ["data/output/**"],
    },
    "cli-tool": {
        "image_tag": False,
        "files": ["RUN.md", ".conductor/workspace.json"],
        "artifacts": [],
    },
    "design-layout": {
        "image_tag": False,
        "files": ["RUN.md", "DESIGN.md", ".conductor/workspace.json"],
        "artifacts": ["exports/**"],
    },
    "tech-docs": {
        "image_tag": False,
        "files": ["RUN.md", ".conductor/workspace.json"],
        "artifacts": ["docs/**"],
    },
    "python-gui": {
        "image_tag": False,
        "files": ["RUN.md", ".conductor/workspace.json"],
        "artifacts": [],
    },
    "arduino": {
        "image_tag": False,
        "files": ["RUN.md", ".conductor/workspace.json"],
        "artifacts": [],
    },
}


def seed_standards(db_url: str | None = None) -> list[dict[str, Any]]:
    """Seed domain standards into the database.

    Returns list of inserted standard dicts.
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")

    all_slugs = ["python-backend", "react-frontend", "python-gui", "arduino", "design-layout", "planning", "assembly-compose-v1"]

    standards_data = [
        {
            "slug": "python-backend",
            "name": "Python Backend (FastAPI)",
            "kind": "domain",
            "families": ["software"],
            "conventions_md": _read_conventions("python-backend"),
            "tool_manifest": _build_tool_manifest("python-backend"),
            "artifact_spec": _build_artifact_spec("python-backend"),
            "scaffold_tree": _build_scaffold_tree("python-backend"),
            "scaffold_ref": str(_scaffold_dir("python-backend")),
            "import_ref": _compute_tree_sha("python-backend"),
            "source_repo": "curated",
            "service_template": SERVICE_TEMPLATES["python-backend"],
            "publish_manifest": PUBLISH_MANIFESTS["python-backend"],
        },
        {
            "slug": "react-frontend",
            "name": "React Frontend (Vite)",
            "kind": "domain",
            "families": ["software", "design"],
            "conventions_md": _read_conventions("react-frontend"),
            "tool_manifest": _build_tool_manifest("react-frontend"),
            "artifact_spec": _build_artifact_spec("react-frontend"),
            "scaffold_tree": _build_scaffold_tree("react-frontend"),
            "scaffold_ref": str(_scaffold_dir("react-frontend")),
            "import_ref": _compute_tree_sha("react-frontend"),
            "source_repo": "curated",
            "service_template": SERVICE_TEMPLATES["react-frontend"],
            "publish_manifest": PUBLISH_MANIFESTS["react-frontend"],
        },
        {
            "slug": "python-gui",
            "name": "Python GUI (PySide6)",
            "kind": "domain",
            "families": ["software"],
            "conventions_md": _read_conventions("python-gui"),
            "tool_manifest": _build_tool_manifest("python-gui"),
            "artifact_spec": _build_artifact_spec("python-gui"),
            "scaffold_tree": _build_scaffold_tree("python-gui"),
            "scaffold_ref": str(_scaffold_dir("python-gui")),
            "import_ref": _compute_tree_sha("python-gui"),
            "source_repo": "curated",
            "service_template": SERVICE_TEMPLATES["python-gui"],
            "publish_manifest": PUBLISH_MANIFESTS["python-gui"],
        },
        {
            "slug": "arduino",
            "name": "Arduino Firmware (PlatformIO)",
            "kind": "domain",
            "families": ["software"],
            "conventions_md": _read_conventions("arduino"),
            "tool_manifest": _build_tool_manifest("arduino"),
            "artifact_spec": _build_artifact_spec("arduino"),
            "scaffold_tree": _build_scaffold_tree("arduino"),
            "scaffold_ref": str(_scaffold_dir("arduino")),
            "import_ref": _compute_tree_sha("arduino"),
            "source_repo": "curated",
            "service_template": SERVICE_TEMPLATES["arduino"],
            "publish_manifest": PUBLISH_MANIFESTS["arduino"],
        },
        {
            "slug": "design-layout",
            "name": "Design Layout (open-design)",
            "kind": "domain",
            "families": ["design", "creative"],
            "conventions_md": _read_conventions("design-layout"),
            "tool_manifest": _build_tool_manifest("design-layout"),
            "artifact_spec": _build_artifact_spec("design-layout"),
            "scaffold_tree": _build_scaffold_tree("design-layout"),
            "scaffold_ref": str(_scaffold_dir("design-layout")),
            "import_ref": _compute_tree_sha("design-layout"),
            "source_repo": "curated",
            "service_template": SERVICE_TEMPLATES["design-layout"],
            "publish_manifest": PUBLISH_MANIFESTS["design-layout"],
        },
        {
            "slug": "planning",
            "name": "Planning Standard",
            "kind": "planning",
            "families": ["generic"],
            "conventions_md": "# Planning Standard Conventions\n",
            "tool_manifest": [],
            "artifact_spec": {
                "plan_files": [".plan/index.json", ".plan/nodes/**", ".plan/checks/**"],
            },
            "scaffold_tree": [],
            "scaffold_ref": None,
            "import_ref": "hand/planning@v1",
            "source_repo": "hand",
        },
        {
            "slug": "cli-tool",
            "name": "CLI Tool (Python)",
            "kind": "domain",
            "families": ["software"],
            "conventions_md": _read_conventions("cli-tool"),
            "tool_manifest": [{"name": "pytest", "kind": "cli", "domain": "testing"}],
            "artifact_spec": _build_artifact_spec("cli-tool"),
            "scaffold_tree": _build_scaffold_tree("cli-tool"),
            "scaffold_ref": str(_scaffold_dir("cli-tool")),
            "import_ref": _compute_tree_sha("cli-tool"),
            "source_repo": "curated",
            "service_template": SERVICE_TEMPLATES["cli-tool"],
            "publish_manifest": PUBLISH_MANIFESTS["cli-tool"],
        },
        {
            "slug": "python-etl",
            "name": "Python ETL Pipeline",
            "kind": "domain",
            "families": ["software", "data"],
            "conventions_md": _read_conventions("python-etl"),
            "tool_manifest": [{"name": "pytest", "kind": "cli", "domain": "testing"}],
            "artifact_spec": _build_artifact_spec("python-etl"),
            "scaffold_tree": _build_scaffold_tree("python-etl"),
            "scaffold_ref": str(_scaffold_dir("python-etl")),
            "import_ref": _compute_tree_sha("python-etl"),
            "source_repo": "researched+curated",
            "service_template": SERVICE_TEMPLATES["python-etl"],
            "publish_manifest": PUBLISH_MANIFESTS["python-etl"],
        },
        {
            "slug": "tech-docs",
            "name": "Technical Documentation",
            "kind": "domain",
            "families": ["content", "research"],
            "conventions_md": _read_conventions("tech-docs"),
            "tool_manifest": [{"name": "markdownlint", "kind": "cli", "domain": "linting"}],
            "artifact_spec": _build_artifact_spec("tech-docs"),
            "scaffold_tree": _build_scaffold_tree("tech-docs"),
            "scaffold_ref": str(_scaffold_dir("tech-docs")),
            "import_ref": _compute_tree_sha("tech-docs"),
            "source_repo": "researched+curated",
            "service_template": SERVICE_TEMPLATES["tech-docs"],
            "publish_manifest": PUBLISH_MANIFESTS["tech-docs"],
        },
        {
            "slug": "assembly-compose-v1",
            "name": "Assembly Compose v1",
            "kind": "assembly",
            "families": ["software"],
            "conventions_md": "# Assembly Compose v1\n\nGenerates docker-compose.yml from system projects.\n",
            "tool_manifest": [
                {"name": "docker", "kind": "cli", "domain": "containerization"},
                {"name": "docker-compose", "kind": "cli", "domain": "orchestration"},
            ],
            "artifact_spec": {
                "compose": ["docker-compose.yml"],
                "dockerfiles": ["Dockerfile.*"],
                "service_descriptor": ["workspace.json"],
            },
            "scaffold_tree": [],
            "scaffold_ref": None,
            "import_ref": "hand/assembly-compose-v1@v1",
            "source_repo": "hand",
            "service_template": {
                "version": "3.9",
                "default_image": "python:3.12-slim",
                "default_port": 8000,
                "healthcheck": {
                    "test": ["CMD", "curl", "-f", "http://localhost:8000/health"],
                    "interval": "30s",
                    "timeout": "10s",
                    "retries": 3,
                },
            },
        },
    ]

    results = []
    with psycopg.connect(_db_url) as conn, conn.cursor() as cur:
        for std in standards_data:
            cur.execute(
                """INSERT INTO domain_standards
                   (slug, name, kind, families, conventions_md,
                    tool_manifest, artifact_spec, scaffold_tree,
                    scaffold_ref, import_ref, source_repo,
                    service_template, publish_manifest)
                   VALUES (%s, %s, %s, %s::jsonb, %s,
                           %s::jsonb, %s::jsonb, %s::jsonb,
                           %s, %s, %s, %s::jsonb, %s::jsonb)
                   ON CONFLICT (slug) DO UPDATE SET
                       name = EXCLUDED.name,
                       kind = EXCLUDED.kind,
                       families = EXCLUDED.families,
                       conventions_md = EXCLUDED.conventions_md,
                       tool_manifest = EXCLUDED.tool_manifest,
                       artifact_spec = EXCLUDED.artifact_spec,
                       scaffold_tree = EXCLUDED.scaffold_tree,
                       scaffold_ref = EXCLUDED.scaffold_ref,
                       import_ref = EXCLUDED.import_ref,
                       source_repo = EXCLUDED.source_repo,
                       service_template = EXCLUDED.service_template,
                       publish_manifest = EXCLUDED.publish_manifest,
                       version = domain_standards.version + 1
                   RETURNING id, slug, name""",
                (
                    std["slug"],
                    std["name"],
                    std["kind"],
                    json.dumps(std.get("families", [])),
                    std["conventions_md"],
                    json.dumps(std["tool_manifest"]),
                    json.dumps(std["artifact_spec"]),
                    json.dumps(std["scaffold_tree"]),
                    std["scaffold_ref"],
                    std["import_ref"],
                    std["source_repo"],
                    json.dumps(std.get("service_template")),
                    json.dumps(std.get("publish_manifest")),
                ),
            )
            row = cur.fetchone()
            if row:
                results.append({"id": str(row[0]), "slug": row[1], "name": row[2]})
                logger.info("Seeded standard: %s (id=%s)", row[1], row[0])
        conn.commit()

    return results


def seed_runtime_templates(db_url: str | None = None) -> int:
    """Seed/repair service_template + publish_manifest on all standards.

    File 01 (guide 01.8/01.10). Updates ONLY the two runtime columns by
    slug — never touches conventions/artifact_spec, so existing rows
    (e.g. cli-tool, python-etl, tech-docs seeded from the addendum) are
    not clobbered. Idempotent; returns count of updated rows.
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")

    updated = 0
    with psycopg.connect(_db_url) as conn, conn.cursor() as cur:
        for slug, template in SERVICE_TEMPLATES.items():
            manifest = PUBLISH_MANIFESTS.get(slug, {})
            cur.execute(
                """UPDATE domain_standards
                   SET service_template = %s::jsonb,
                       publish_manifest = %s::jsonb,
                       updated_at = now()
                   WHERE slug = %s""",
                (json.dumps(template), json.dumps(manifest), slug),
            )
            updated += cur.rowcount
        conn.commit()

    logger.info("Seeded runtime templates for %d standards", updated)
    return updated


def link_capabilities(db_url: str | None = None) -> int:
    """Link existing capabilities to domain standards.

    Expects capabilities to have names matching patterns:
    - 'backend*', 'api*' -> python-backend
    - 'frontend*', 'ui*' -> react-frontend

    Returns count of capabilities linked.
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")

    with psycopg.connect(_db_url) as conn, conn.cursor() as cur:
        # Get standard IDs
        cur.execute(
            "SELECT id, slug FROM domain_standards "
            "WHERE slug IN ('python-backend', 'react-frontend', 'python-gui', 'arduino', 'design-layout')"
        )
        standards = {row[1]: row[0] for row in cur.fetchall()}

        # Link capabilities matching patterns
        backend_std = standards.get("python-backend")
        frontend_std = standards.get("react-frontend")
        gui_std = standards.get("python-gui")
        arduino_std = standards.get("arduino")
        design_std = standards.get("design-layout")

        count = 0
        if backend_std:
            cur.execute(
                """UPDATE capabilities SET standard_id = %s
                    WHERE (name ILIKE 'backend%%' OR name ILIKE 'api%%' OR name ILIKE 'data%%' OR name ILIKE 'cli%%')
                      AND (standard_id IS NULL OR standard_id != %s)""",
                (backend_std, backend_std),
            )
            count += cur.rowcount

        if frontend_std:
            cur.execute(
                """UPDATE capabilities SET standard_id = %s
                    WHERE (name ILIKE 'frontend%%' OR name ILIKE 'ui%%' OR name ILIKE 'design%%')
                      AND (standard_id IS NULL OR standard_id != %s)""",
                (frontend_std, frontend_std),
            )
            count += cur.rowcount

        if gui_std:
            cur.execute(
                """UPDATE capabilities SET standard_id = %s
                    WHERE name = 'gui_app'
                      AND (standard_id IS NULL OR standard_id != %s)""",
                (gui_std, gui_std),
            )
            count += cur.rowcount

        if arduino_std:
            cur.execute(
                """UPDATE capabilities SET standard_id = %s
                    WHERE (name ILIKE 'embedded%%' OR name ILIKE 'arm%%')
                      AND (standard_id IS NULL OR standard_id != %s)""",
                (arduino_std, arduino_std),
            )
            count += cur.rowcount

        if design_std:
            cur.execute(
                """UPDATE capabilities SET standard_id = %s
                    WHERE name = 'design_layout'
                      AND (standard_id IS NULL OR standard_id != %s)""",
                (design_std, design_std),
            )
            count += cur.rowcount

        conn.commit()

    return count


def stamp_run_standard(run_id: str, plan_domain: str, db_url: str | None = None) -> None:
    """Stamp a run with the relevant standard_ids based on its domain."""
    _db_url = db_url or os.environ.get("DATABASE_URL", "")

    slug_map = {
        # From goal_formulator.infer_domain()
        "software_app": "python-backend",
        "api_service": "python-backend",
        "cli_script": "python-backend",
        "data_pipeline": "python-backend",
        # From capability selector _infer_domain()
        "backend": "python-backend",
        "software": "python-backend",
        "frontend": "react-frontend",
        "design": "react-frontend",
        # New standards
        "gui_app": "python-gui",
        "embedded_firmware": "arduino",
        "design_layout": "design-layout",
    }
    slug = slug_map.get(plan_domain)
    if not slug:
        return

    with psycopg.connect(_db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET standard_ids = ARRAY(SELECT id FROM domain_standards WHERE slug = %s) WHERE id = %s",
            (slug, run_id),
        )
        conn.commit()


def append_standard_dimensions(db_url: str | None = None) -> int:
    """Append spec-conformance quality_dimension entries to capabilities linked to domain standards.

    For each domain_standard with kind='domain', derives objective quality_dimension entries
    from the standard's artifact_spec and appends them to the linked capability's
    quality_dimensions JSONB array, avoiding duplicates by id.

    Returns count of appended entries.
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")
    total = 0

    with psycopg.connect(_db_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, slug, artifact_spec FROM domain_standards WHERE kind = 'domain'")
        standards = cur.fetchall()

        for std_id, slug, artifact_spec_raw in standards:
            artifact_spec = artifact_spec_raw if isinstance(artifact_spec_raw, dict) else {}

            cur.execute(
                "SELECT name, quality_dimensions FROM capabilities WHERE standard_id = %s",
                (std_id,),
            )
            capabilities = cur.fetchall()

            for cap_name, existing_dims_raw in capabilities:
                existing_dims = existing_dims_raw if isinstance(existing_dims_raw, list) else []
                existing_ids = {d.get("id") for d in existing_dims if isinstance(d, dict)}

                new_entries = []
                for artifact_type in artifact_spec.keys():
                    dim_id = f"spec_{slug}_{artifact_type}"
                    if dim_id not in existing_ids:
                        new_entries.append({
                            "id": dim_id,
                            "dimension": f"Required {artifact_type} artifacts exist per {slug} standard",
                            "kind": "objective",
                        })

                if new_entries:
                    cur.execute(
                        """UPDATE capabilities
                           SET quality_dimensions = COALESCE(quality_dimensions, '[]'::jsonb) || %s::jsonb
                           WHERE name = %s""",
                        (json.dumps(new_entries), cap_name),
                    )
                    total += len(new_entries)

        conn.commit()

    logger.info("Appended %d quality_dimension entries from standard artifact specs", total)
    return total


if __name__ == "__main__":
    db_url = os.environ.get("DATABASE_URL", "")
    seed_standards(db_url)
    seed_runtime_templates(db_url)
    link_capabilities(db_url)
    append_standard_dimensions(db_url)
    print("Seeding complete.")
