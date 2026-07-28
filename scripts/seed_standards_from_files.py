#!/usr/bin/env python3
"""Seed domain_standards + capability links + dim upgrades from the seed pack.

Reads AGENTS_*.md (conventions), spec_*.json (artifact_spec + scaffold_tree),
and conventions.md (planning standard) from the seed directory and writes them
into the conductor application database.

Idempotent — safe to re-run.

Usage:
    uv run python scripts/seed_standards_from_files.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import psycopg

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SEED_DIR = Path("/opt/aipc/notes/conductor_roster_v2_seed")
SCAFFOLDS_DIR = Path("/opt/aipc/conductor/scaffolds_store")

# ── Standard definitions ─────────────────────────────────────────────────────


def _slug_to_name(slug: str) -> str:
    return {
        "python-backend": "Python Backend (FastAPI)",
        "react-frontend": "React Frontend (Vite)",
        "planning": "Planning Standard",
        "python-gui": "Python GUI (PySide6)",
        "arduino": "Arduino Firmware (PlatformIO)",
        "design-layout": "Design Layout (open-design)",
    }.get(slug, slug)


def _parse_version_suffix(dirname: str, slug: str) -> int:
    """Extract version integer from a ``{slug}-v{int}`` directory name."""
    suffix = dirname[len(slug) + 1:]
    if suffix.startswith("v") and suffix[1:].isdigit():
        return int(suffix[1:])
    return 0


def _scaffold_dir(slug: str) -> Path:
    """Resolve scaffold directory by scanning for versioned dirs (``{slug}-v*``)."""
    base = SCAFFOLDS_DIR / slug
    candidates = sorted(
        (d for d in SCAFFOLDS_DIR.iterdir()
         if d.is_dir() and d.name.startswith(f"{slug}-v")),
        key=lambda d: _parse_version_suffix(d.name, slug),
    )
    if candidates:
        return candidates[-1]
    return base if base.exists() else base


def _compute_import_ref(slug: str) -> str:
    """Return a descriptive import_ref from the scaffold dir name."""
    d = _scaffold_dir(slug)
    # Extract version suffix: "python-backend-v1" → "@v1"
    version_tag = ""
    if d.name != slug:
        suffix = d.name[len(slug) + 1:]
        if suffix.startswith("v"):
            version_tag = f"@{suffix}"
    return f"scaffold/{slug}{version_tag}"


STANDARDS: list[dict] = [
    {
        "slug": "python-backend",
        "kind": "domain",
        "families": ["software"],
        "scaffold_ref": str(_scaffold_dir("python-backend")),
        "import_ref": _compute_import_ref("python-backend"),
        "agents_file": "AGENTS_python-backend-v1.md",
        "spec_file": "spec_python-backend-v1.json",
        "source_repo": "curated",
    },
    {
        "slug": "react-frontend",
        "kind": "domain",
        "families": ["software", "design"],
        "scaffold_ref": str(_scaffold_dir("react-frontend")),
        "import_ref": _compute_import_ref("react-frontend"),
        "agents_file": "AGENTS_react-frontend-v1.md",
        "spec_file": "spec_react-frontend-v1.json",
        "source_repo": "curated",
    },
    {
        "slug": "planning",
        "kind": "planning",
        "families": ["generic"],
        "scaffold_ref": None,
        "import_ref": "hand/planning@v1",
        "agents_file": "conventions.md",
        "spec_file": None,  # planning uses conventions.md only, artifact_spec hand-crafted
        "source_repo": "hand",
    },
    {
        "slug": "python-gui",
        "kind": "domain",
        "families": ["software"],
        "scaffold_ref": str(_scaffold_dir("python-gui")),
        "import_ref": _compute_import_ref("python-gui"),
        "agents_file": "AGENTS_python-gui-v1.md",
        "spec_file": "spec_python-gui-v1.json",
        "source_repo": "researched+curated",
    },
    {
        "slug": "arduino",
        "kind": "domain",
        "families": ["software"],
        "scaffold_ref": str(_scaffold_dir("arduino")),
        "import_ref": _compute_import_ref("arduino"),
        "agents_file": "AGENTS_arduino-firmware-v1.md",
        "spec_file": "spec_arduino-firmware-v1.json",
        "source_repo": "researched+curated",
    },
    {
        "slug": "design-layout",
        "kind": "domain",
        "families": ["design", "creative"],
        "scaffold_ref": str(_scaffold_dir("design-layout")),
        "import_ref": _compute_import_ref("design-layout"),
        "agents_file": "AGENTS_design-layout-v1.md",
        "spec_file": "spec_design-layout-v1.json",
        "source_repo": "researched+curated",
    },
]

# ── Capability links ─────────────────────────────────────────────────────────

CAP_LINKS: dict[str, list[str]] = {
    "python-backend": [
        "backend_api", "cli_tool", "tests_suite", "data_pipeline", "deployment_iac",
    ],
    "react-frontend": ["frontend"],
    "planning": [],
    "python-gui": [],
    "arduino": [],
    "design-layout": ["design_layout"],
}

# ── Delivery spec patches (appended to artifact_spec) ────────────────────────

DELIVERY_SPECS: dict[str, dict] = {
    "python-backend": {
        "delivery_spec": {
            "form": "served_url",
            "check": "uvicorn (or compose) starts and health URL responds",
            "package_cmd": "uvicorn app.main:app / docker compose up",
            "platform_note": "web app: ready = served, not double-click",
        }
    },
    "react-frontend": {
        "delivery_spec": {
            "form": "served_url",
            "check": "npm run build passes; npm run preview serves the app",
            "package_cmd": "vite build → static dist/ (deployable anywhere)",
            "platform_note": "web app: ready = built + servable dist/",
        }
    },
}

# ── Dimension upgrades (idempotent via NOT LIKE guard) ────────────────────────

DIM_UPGRADES: dict[str, list[dict]] = {
    "backend_api": [
        {"id": "structure_conforms",
         "dimension": "repo follows the standard structure (src layout, tests/, pyproject, RUN.md)",
         "kind": "objective"},
        {"id": "lint_clean",
         "dimension": "ruff check passes with project config",
         "kind": "objective"},
    ],
    "frontend": [
        {"id": "structure_conforms",
         "dimension": "follows the standard structure (src/, components/, api/ layer, tests)",
         "kind": "objective"},
        {"id": "build_pass",
         "dimension": "npm run build exits 0 (type-clean)",
         "kind": "objective"},
        {"id": "a11y_minimum",
         "dimension": "labels on inputs, alt on images, real interactive elements",
         "kind": "subjective"},
    ],
    "tests_suite": [
        {"id": "coverage_of_criteria",
         "dimension": "tests cover each acceptance criterion incl. one invalid-input case per endpoint/component",
         "kind": "subjective"},
    ],
    "cli_tool": [
        {"id": "structure_conforms",
         "dimension": "follows the python standard layout (src/, pyproject, tests/)",
         "kind": "objective"},
    ],
    "deployment_iac": [
        {"id": "secrets_hygiene",
         "dimension": "no hardcoded secrets; env-based config",
         "kind": "subjective"},
    ],
    "design_layout": [
        {"id": "exports_valid",
         "dimension": "exports/ contains brief-demanded formats, non-empty and valid",
         "kind": "objective"},
        {"id": "brief_satisfied",
         "dimension": "artifact satisfies brief/BRIEF.md point-by-point",
         "kind": "subjective"},
        {"id": "token_conformance",
         "dimension": "only DESIGN.md tokens used (palette/type/spacing)",
         "kind": "subjective"},
    ],
}

# ── New capabilities (gui_app, embedded_firmware) ────────────────────────────

NEW_CAPABILITIES: list[dict] = [
    {
        "name": "gui_app",
        "family": ["software"],
        "description": "desktop GUI application, delivered as a standalone executable",
        "required_tools": ["write_file", "shell"],
        "quality_dimensions": [
            {"id": "launches_standalone",
             "dimension": "packaged app (dist/) launches with no env setup (--smoke exits 0)",
             "kind": "objective"},
            {"id": "build_reproducible",
             "dimension": "pyinstaller spec committed; build passes",
             "kind": "objective"},
            {"id": "core_tested",
             "dimension": "Qt-free core logic unit-tested headless",
             "kind": "objective"},
            {"id": "ui_responsive",
             "dimension": "long work off the UI thread; app stays responsive",
             "kind": "subjective"},
            {"id": "ux_clarity",
             "dimension": "controls labeled, errors as dialogs, sensible layout",
             "kind": "subjective"},
        ],
        "standard_slug": "python-gui",
    },
    {
        "name": "embedded_firmware",
        "family": ["software"],
        "description": "microcontroller firmware (PlatformIO), delivered as compiled binary",
        "required_tools": ["write_file", "shell"],
        "quality_dimensions": [
            {"id": "compiles",
             "dimension": "pio run exits 0; firmware artifact exists",
             "kind": "objective"},
            {"id": "native_tests_pass",
             "dimension": "pio test -e native exits 0 (logic verified without hardware)",
             "kind": "objective"},
            {"id": "static_clean",
             "dimension": "pio check: no new high-severity defects",
             "kind": "objective"},
            {"id": "nonblocking_design",
             "dimension": "loop is non-blocking (millis scheduling), config in constexpr",
             "kind": "subjective"},
            {"id": "module_separation",
             "dimension": "hardware I/O separated from testable logic",
             "kind": "subjective"},
        ],
        "standard_slug": "arduino",
    },
]

# ── Planning artifact_spec (hard-coded, not from a spec file) ────────────────

PLANNING_ARTIFACT_SPEC = {
    "required_paths": [
        ".plan/index.json",
        ".plan/nodes/",
        ".plan/checks/",
    ],
}


# ── helpers ───────────────────────────────────────────────────────────────────


def read_conventions(slug: str, entry: dict) -> str:
    """Read AGENTS.md / conventions.md from the seed directory."""
    filename = entry["agents_file"]
    path = SEED_DIR / filename
    if not path.exists():
        logger.warning("Conventions file not found: %s", path)
        return ""
    text = path.read_text(encoding="utf-8")
    logger.info("  conventions: read %d bytes from %s", len(text), filename)
    return text


def read_spec(slug: str, entry: dict) -> tuple[dict, list]:
    """Read artifact_spec and scaffold_tree from spec_*.json."""
    spec_file = entry.get("spec_file")
    if not spec_file:
        return {}, []
    path = SEED_DIR / spec_file
    if not path.exists():
        logger.warning("Spec file not found: %s", path)
        return {}, []
    data = json.loads(path.read_text(encoding="utf-8"))
    artifact_spec = data.get("artifact_spec", {})
    scaffold_tree = data.get("scaffold_tree", [])
    logger.info("  spec: read %s (%d paths, %d tree items)", spec_file,
                len(artifact_spec.get("required_paths", [])), len(scaffold_tree))
    return artifact_spec, scaffold_tree


def get_db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        logger.error("DATABASE_URL is not set")
        sys.exit(1)
    return url


# ── seed steps ────────────────────────────────────────────────────────────────


def seed_standards(conn: psycopg.Connection) -> int:
    """Insert or update domain_standards rows from STANDARDS definitions."""
    count = 0
    with conn.cursor() as cur:
        for entry in STANDARDS:
            slug = entry["slug"]
            kind = entry["kind"]
            name = _slug_to_name(slug)
            families = json.dumps(entry["families"])
            active = True
            scaffold_ref = entry["scaffold_ref"]
            import_ref = entry["import_ref"]
            source_repo = entry["source_repo"]

            conventions_md = read_conventions(slug, entry)
            artifact_spec, scaffold_tree = read_spec(slug, entry)

            if slug == "planning":
                artifact_spec = PLANNING_ARTIFACT_SPEC

            if slug in DELIVERY_SPECS:
                artifact_spec.update(DELIVERY_SPECS[slug])

            cur.execute("SELECT id FROM domain_standards WHERE slug = %s", (slug,))
            row = cur.fetchone()

            if row:
                cur.execute(
                    """UPDATE domain_standards
                          SET name = %s, kind = %s, families = %s::jsonb,
                              active = %s, conventions_md = %s,
                              artifact_spec = %s::jsonb,
                              scaffold_tree = %s::jsonb,
                              scaffold_ref = %s, import_ref = %s,
                              source_repo = %s
                        WHERE slug = %s""",
                    (name, kind, families, active, conventions_md,
                     json.dumps(artifact_spec), json.dumps(scaffold_tree),
                     scaffold_ref, import_ref, source_repo, slug),
                )
                logger.info("  UPDATED domain_standard slug=%s", slug)
            else:
                cur.execute(
                    """INSERT INTO domain_standards
                          (slug, name, kind, families, active,
                           conventions_md, artifact_spec, scaffold_tree,
                           scaffold_ref, import_ref, source_repo)
                       VALUES (%s, %s, %s, %s::jsonb, %s,
                               %s, %s::jsonb, %s::jsonb,
                               %s, %s, %s)""",
                    (slug, name, kind, families, active,
                     conventions_md, json.dumps(artifact_spec), json.dumps(scaffold_tree),
                     scaffold_ref, import_ref, source_repo),
                )
                logger.info("  INSERTED domain_standard slug=%s", slug)
            count += 1

    conn.commit()
    logger.info("Seeded %d domain_standards", count)
    return count


def link_capabilities(conn: psycopg.Connection) -> int:
    """Link capabilities to standards via slug-based UUID lookup."""
    count = 0
    with conn.cursor() as cur:
        for slug, cap_names in CAP_LINKS.items():
            if not cap_names:
                continue
            cur.execute(
                """UPDATE capabilities
                      SET standard_id = (SELECT id FROM domain_standards WHERE slug = %s)
                    WHERE name = ANY(%s)""",
                (slug, cap_names),
            )
            linked = cur.rowcount if cur.rowcount else 0
            if linked:
                logger.info("  Linked %d caps to slug=%s: %s", linked, slug, cap_names)
            count += linked
    conn.commit()
    logger.info("Linked %d capabilities total", count)
    return count


def create_new_capabilities(conn: psycopg.Connection) -> int:
    """Create new capabilities that don't exist yet (gui_app, embedded_firmware)."""
    count = 0
    with conn.cursor() as cur:
        for cap in NEW_CAPABILITIES:
            standard_slug = cap["standard_slug"]
            cur.execute(
                "SELECT id FROM domain_standards WHERE slug = %s",
                (standard_slug,),
            )
            std_row = cur.fetchone()
            standard_id = str(std_row[0]) if std_row else None

            cur.execute("SELECT name FROM capabilities WHERE name = %s", (cap["name"],))
            if cur.fetchone():
                logger.info("  Capability '%s' already exists — skipping", cap["name"])
                continue

            cur.execute(
                """INSERT INTO capabilities
                      (name, family, description, required_tools,
                       quality_dimensions, source, standard_id, golden_ref_count)
                   VALUES (%s, %s::jsonb, %s, %s::jsonb,
                           %s::jsonb, 'curated', %s::uuid, 0)""",
                (cap["name"], json.dumps(cap["family"]), cap["description"],
                 json.dumps(cap["required_tools"]),
                 json.dumps(cap["quality_dimensions"]),
                 standard_id),
            )
            logger.info("  INSERTED new capability '%s' (standard=%s)",
                        cap["name"], standard_slug)
            count += 1
    conn.commit()
    logger.info("Created %d new capabilities", count)
    return count


def append_dim_upgrades(conn: psycopg.Connection) -> int:
    """Append quality dimensions using idempotent NOT LIKE guard."""
    total = 0
    with conn.cursor() as cur:
        for cap_name, dims in DIM_UPGRADES.items():
            guard_id = dims[0]["id"]
            cur.execute(
                """UPDATE capabilities
                      SET quality_dimensions = quality_dimensions || %s::jsonb
                    WHERE name = %s
                      AND NOT quality_dimensions::text LIKE %s""",
                (json.dumps(dims), cap_name, f"%{guard_id}%"),
            )
            updated = cur.rowcount if cur.rowcount else 0
            if updated:
                logger.info("  Appended %d dims to '%s' (guard=%s)",
                            len(dims), cap_name, guard_id)
            total += updated * len(dims)
    conn.commit()
    logger.info("Appended %d quality dimension entries", total)
    return total


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    db_url = get_db_url()
    logger.info("Connecting to database …")
    conn = psycopg.connect(db_url)

    try:
        logger.info("── Seeding domain_standards ──")
        seed_standards(conn)

        logger.info("── Linking capabilities ──")
        link_capabilities(conn)

        logger.info("── Creating new capabilities ──")
        create_new_capabilities(conn)

        logger.info("── Appending dimension upgrades ──")
        append_dim_upgrades(conn)

        logger.info("── Done ──")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
