"""Planning worktree lifecycle, scoped opencode.json, brief generation,
and scaffold workspace generator.

Flow:
  1. ``create_planning_worktree()`` — pre-create worktree from master (or fresh);
     for greenfield projects the scaffold workspace is generated and committed
     to master BEFORE the planning worktree is created.
  2. ``planning_brief()`` — build dynamic goal/spec/caps brief (static ref in NODE_BRIEF.md)
  3. ``retry_brief()`` — append file-targeted feedback for re-spawn
  4. ``on_planning_failed()`` — rm worktree after bounded attempts

Generator (greenfield only — zero LLM, deterministic):
  - ``generate_workspace()`` — copy scaffold → substitute tokens → manifest → commit
  - ``pkg_slug()`` — project name → safe Python/npm identifier
  - ``substitute_tokens()`` — replace ``__APP__``/``__PKG__``/``__PROJECT__`` in contents + paths
  - ``is_greenfield()`` — detect whether a project needs scaffolding
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_PLANNING_ATTEMPTS = 20

# ── Scaffold generator (greenfield only, zero LLM, deterministic) ──────────

SCAFFOLDS_DIR = Path(os.environ.get("SCAFFOLDS_DIR", "/opt/aipc/conductor/scaffolds_store"))


def is_greenfield(project_dir: str | Path) -> bool:
    """Check if a project is greenfield (no scaffold committed yet).

    A project is greenfield when:
    - The directory does not exist.
    - OR the directory exists but has no ``.conductor/workspace.json``
      (meaning no scaffold has been generated for it).
    """
    project_dir = Path(project_dir)
    if not project_dir.exists():
        return True
    manifest = project_dir / ".conductor" / "workspace.json"
    return not manifest.exists()


def pkg_slug(name: str) -> str:
    """Convert a project name into a valid Python/npm package identifier (snake_case)."""
    s = name.strip().replace("-", "_").replace(" ", "_")
    s = re.sub(r"[^a-zA-Z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower() or "_"


def _display_name(project_id: str) -> str:
    """Derive a human-readable display name from a project ID."""
    return project_id.replace("-", " ").replace("_", " ").strip()


def _get_active_standard(slug: str) -> dict[str, Any] | None:
    """Fetch an active domain_standard row by slug."""
    from backend.db.queries import conn as db_conn

    with db_conn() as c:
        row = c.execute(
            """SELECT id, slug, name, conventions_md,
                      scaffold_ref, artifact_spec, version
               FROM domain_standards
               WHERE slug = %s AND active = true""",
            (slug,),
        ).fetchone()
    return dict(row) if row else None


# Domain → standard slug(s) mapping.
# Each domain maps to a list of standard slugs (multi-engine projects get one
# scaffold per slug in a subdirectory).  Single-slug domains scaffold at root.
# Domains with no scaffold (research_report, generic) are omitted.
_DOMAIN_STANDARD_MAP: dict[str, list[str]] = {
    "software_app": ["python-backend", "react-frontend"],
    "api_service": ["python-backend"],
    "cli_script": ["python-backend"],
    "data_pipeline": ["python-backend"],
    "gui_app": ["python-gui"],
    "embedded_firmware": ["arduino"],
    "visual_design": ["react-frontend", "design-layout"],
}


def _domain_to_standard_slug(domain: str) -> list[str] | None:
    """Map a domain name to a list of standard slugs (may be empty)."""
    return _DOMAIN_STANDARD_MAP.get(domain)


def substitute_tokens(root: Path, params: dict[str, str]) -> None:
    """Replace token markers in file contents AND path segments.

    Operates in-place:
    1. Walk all files — replace ``__APP__``, ``__PKG__``, ``__PROJECT__`` in content.
    2. Walk all paths deepest-first — rename dirs/files that contain tokens.

    Only text-looking files are content-substituted (skips binary).
    """
    # Phase 1 — file contents
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        # Quick binary check: skip if null bytes or very high entropy
        try:
            content = p.read_bytes()
            if b"\x00" in content[:8192]:
                continue  # binary
            text = content.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        changed = False
        for token, value in params.items():
            if token in text:
                text = text.replace(token, value)
                changed = True
        if changed:
            p.write_text(text, encoding="utf-8")

    # Phase 2 — path segments (deepest first so parent renames don't orphan children)
    all_paths = sorted(
        (p for p in root.rglob("*") if p != root),
        key=lambda p: len(str(p)),
        reverse=True,
    )
    for p in all_paths:
        parent = p.parent
        old_name = p.name
        new_name = old_name
        for token, value in params.items():
            if token in new_name:
                new_name = new_name.replace(token, value)
        if new_name != old_name:
            p.rename(parent / new_name)


def _copy_scaffold_tree(src: Path, dst: Path) -> None:
    """Copy a scaffold directory tree to *dst*, overwriting any existing content.

    Skips ``.gitkeep`` files (preserve their presence in the source but allow
    the target to add its own).  Creates parent dirs as needed.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def generate_workspace(
    project_dir: str | Path,
    standard_slug: str,
    params: dict[str, str] | None = None,
    subdir: str = ".",
    defer_commit: bool = False,
) -> dict[str, Any]:
    """Generate a scaffold workspace in *project_dir* from a domain standard.

    This is the deterministic generator (zero LLM).  It:

    1. Resolves the active standard and its ``scaffold_ref``.
    2. Copies the scaffold tree into ``project_dir/subdir``.
    3. Substitutes ``__APP__`` / ``__PKG__`` / ``__PROJECT__`` in all contents
       and path segments.
    4. Writes the standard's ``conventions_md`` into ``AGENTS.md``.
    5. Writes ``.conductor/workspace.json`` manifest.
    6. Git-commits the result — unless ``defer_commit=True`` (caller commits).

    Args:
        project_dir: Root project directory (git repo).
        standard_slug: Domain standard slug (e.g. ``"python-backend"``).
        params: Optional token mapping.  Auto-derived from ``project_dir.name``
            when omitted.
        subdir: Subdirectory within the project to generate into.
        defer_commit: When True, skip the git add/commit so the caller can
            commit all scaffolds in a single atomic commit.

    Returns:
        Manifest dict with keys ``standard_slug``, ``standard_id``, ``version``,
        ``params``, ``subdir``, ``generated_at``.

    Raises:
        ValueError: When the standard is not found, has no scaffold_ref, or
            the scaffold directory does not exist on disk.
    """
    project_dir = Path(project_dir).resolve()
    std = _get_active_standard(standard_slug)
    if not std:
        raise ValueError(f"No active standard found for slug={standard_slug}")

    scaffold_ref = std.get("scaffold_ref")
    if not scaffold_ref:
        raise ValueError(f"Standard {standard_slug} has no scaffold_ref")

    src = Path(scaffold_ref)
    if not src.exists():
        raise ValueError(f"Scaffold directory does not exist: {src}")

    # Derive params when not provided
    if params is None:
        proj_name = project_dir.name
        params = {
            "__APP__": _display_name(proj_name),
            "__PKG__": pkg_slug(proj_name),
            "__PROJECT__": proj_name,
        }

    dst = project_dir / subdir

    # 1. Copy scaffold tree
    _copy_scaffold_tree(src, dst)

    # 2. Substitute tokens in contents and path segments
    substitute_tokens(dst, params)

    # 3. Overwrite AGENTS.md with the standard's authoritative conventions
    conventions = std.get("conventions_md") or ""
    if conventions:
        (dst / "AGENTS.md").write_text(conventions, encoding="utf-8")

    # 4. Write workspace manifest
    manifest: dict[str, Any] = {
        "standard_slug": standard_slug,
        "standard_id": str(std["id"]),
        "version": std["version"],
        "params": params,
        "subdir": subdir,
        "generated_at": datetime.datetime.now().isoformat(),
    }
    conductor_dir = dst / ".conductor"
    conductor_dir.mkdir(parents=True, exist_ok=True)
    (conductor_dir / "workspace.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )

    # 5. Commit to master (unless caller defers for batch commit)
    if not defer_commit:
        subprocess.run(
            ["git", "-C", str(project_dir), "add", "-A"],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(project_dir), "commit", "-m", "scaffold: generated workspace(s)"],
            check=True, capture_output=True, timeout=30,
        )

    logger.info("Generated workspace from %s into %s (defer_commit=%s)", standard_slug, dst, defer_commit)
    return manifest


# ── Workspace picture (WORKSPACE.md) ──────────────────────────────────────


def emit_workspace_picture(worktree: Path) -> None:
    """Write ``.plan/research/WORKSPACE.md`` — a bounded view of the workspace.

    Gives the planner a high-level picture of the scaffold / existing code
    structure without scanning files.  Emitted for BOTH greenfield and
    continuation projects.

    The brief references this file: *"Read .plan/research/WORKSPACE.md — the
    workspace already exists; your nodes' deliverable paths MUST fit its
    structure; do not plan scaffold-creation work."*
    """
    INFRA_EXCLUDES = {
        ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
        ".ruff_cache", ".opencode", ".conductor", ".plan", "l4_scratch",
        "dist", ".pio", ".cache",
    }

    # ── Tree ─────────────────────────────────────────────────────────────
    tree_lines: list[str] = []
    for p in sorted(worktree.rglob("*")):
        if p == worktree:
            continue
        if any(excl in p.parts for excl in INFRA_EXCLUDES):
            continue
        rel = p.relative_to(worktree)
        try:
            tree_lines.append(f"{'/' if p.is_dir() else ''}{rel}")
        except ValueError:
            continue
    tree_text = "\n".join(tree_lines[:200])  # cap at 200 entries

    # ── Key manifests ────────────────────────────────────────────────────
    manifest_globs = [
        "pyproject.toml", "package.json", "platformio.ini",
        "DESIGN.md", ".conductor/workspace.json",
    ]
    manifest_sections: list[str] = []
    for glob_pat in manifest_globs:
        for p in sorted(worktree.glob(f"**/{glob_pat}")):
            if any(excl in p.parts for excl in INFRA_EXCLUDES):
                continue
            try:
                text = p.read_text(encoding="utf-8")
                if len(text) > 4000:
                    text = text[:4000] + "\n--- truncated ---"
                manifest_sections.append(
                    f"### {p.relative_to(worktree)}\n```\n{text}\n```"
                )
            except Exception:
                pass

    # ── Standards ────────────────────────────────────────────────────────
    standards: list[str] = []
    for p in sorted(worktree.rglob(".conductor/workspace.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            standards.append(
                f"- {data.get('standard_slug', '?')} "
                f"v{data.get('version', '?')} "
                f"subdir={data.get('subdir', '.')}"
            )
        except Exception:
            pass

    research_dir = worktree / ".plan" / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    content = (
        "# Workspace Picture (generated)\n\n"
        "## Tree\n```\n" + tree_text + "\n```\n\n"
        "## Manifests\n" + "\n".join(manifest_sections) + "\n\n"
        "## Standards\n"
        + ("\n".join(standards) if standards else "(no standards)")
        + "\n"
    )
    (research_dir / "WORKSPACE.md").write_text(content, encoding="utf-8")
    logger.info("Workspace picture written to %s/.plan/research/WORKSPACE.md", worktree)


def _unignore_plan_dotdir(worktree: Path) -> None:
    """Remove ``.plan/`` and ``.conductor/`` from ``.gitignore`` so agent tools work."""
    gi = worktree / ".gitignore"
    if not gi.exists():
        return
    lines = gi.read_text().splitlines()
    cleaned = [l for l in lines if l.strip() not in (".plan/", ".conductor/")]
    if len(cleaned) != len(lines):
        gi.write_text("\n".join(cleaned) + "\n")
        logger.debug("Removed .plan/ and .conductor/ from .gitignore in %s", worktree)


def _build_aion_files_block(worktree: str | Path, only_relpaths: set[str] | None = None) -> str:
    """``[[AION_FILES]]`` block with absolute paths to ``.plan/`` and ``.conductor/`` files.

    When *only_relpaths* is provided (a set of relative paths like ``.plan/checks/node-002.json``),
    only those files (plus ``.conductor/NODE_BRIEF.md``) are included.
    Otherwise all files under ``.plan/`` and ``.conductor/`` are listed.
    """
    wt = Path(worktree)
    lines: list[str] = ["[[AION_FILES]]"]

    if only_relpaths is not None:
        # Scoped mode: only fix-flagged files + NODE_BRIEF.md
        for rp in sorted(only_relpaths):
            abspath = (wt / rp).absolute()
            lines.append(str(abspath))
        brief_path = wt / ".conductor" / "NODE_BRIEF.md"
        if brief_path.exists():
            lines.append(str(brief_path.absolute()))
    else:
        for suffix in (".plan", ".conductor"):
            target = wt / suffix
            if target.is_dir():
                for f in sorted(target.rglob("*")):
                    if f.is_file():
                        lines.append(str(f.absolute()))

    if len(lines) == 1:
        return ""
    return "\n" + "\n".join(lines) + "\n"


def _inline_file_refs(estimated_node_count: int) -> str:
    """Inline ``@`` file references for the plan brief, derived from ``estimated_node_count``."""
    refs = ["@.plan/index.json", "@.plan/TODO.md"]
    for i in range(1, estimated_node_count + 1):
        refs.append(f"@.plan/nodes/node-{i:03d}.json")
        refs.append(f"@.plan/checks/node-{i:03d}.json")
    return "Referenced files: " + ", ".join(refs) + "."


def _extract_fix_files(fix_block: str) -> set[str]:
    """Extract relative ``.plan/`` file paths mentioned under ``FIX THESE`` in the fix block."""
    import re as _re
    # Lines after "FIX THESE" that contain `.plan/...` paths
    in_fix = False
    paths: set[str] = set()
    for line in fix_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("FIX THESE"):
            in_fix = True
            continue
        if in_fix and stripped.startswith("#"):
            in_fix = False
            continue
        if in_fix:
            for m in _re.finditer(r"\.plan/[^\s,]+", stripped):
                paths.add(m.group())
    return paths


def _extract_fix_files_from_raw_errors(raw_error_block: str) -> set[str]:
    """Parse ``node-NNN:`` IDs from staffing errors → .plan/ file paths."""
    import re as _re
    paths: set[str] = set()
    for line in raw_error_block.splitlines():
        for m in _re.finditer(r"(node-\d{3}):", line):
            fname = f"{m.group(1)}.json"
            paths.add(f".plan/nodes/{fname}")
            paths.add(f".plan/checks/{fname}")
    return paths


# ── Worktree lifecycle ────────────────────────────────────────────────────


def create_planning_worktree(
    plan_id: str,
    project_id: str,
    workspace_root: str | Path,
    meta_goal: dict[str, Any] | None = None,
) -> str:
    """Create a planning worktree from the project's master branch.

    Continuation: if the project has a master branch, the worktree is created
    from it so ``.memory/`` (which lives on master) travels into the planning
    worktree.  Fresh projects get an initialised worktree.

    When ``meta_goal`` is provided, the domain is extracted so
    ``_build_static_brief`` can include domain-filtered capability vocabulary
    and quality dimensions in ``NODE_BRIEF.md``.

    Returns the absolute path to the worktree root.
    """
    root = Path(workspace_root).resolve()
    project_dir = root / project_id
    slug = re.sub(r"[^a-zA-Z0-9_.-]", "-", f"{plan_id}-planning")
    worktree_path = root / f"{project_id}.{slug}"

    if worktree_path.exists():
        logger.info("Planning worktree %s already exists — reusing", worktree_path)
        _unignore_plan_dotdir(worktree_path)
        return str(worktree_path)

    # Ensure project dir exists
    if not project_dir.exists():
        project_dir.mkdir(parents=True)
        subprocess.run(
            ["git", "-C", str(project_dir), "init"],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(project_dir), "config", "user.email",
             "conductor@aipc.local"],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(project_dir), "config", "user.name", "Conductor"],
            check=True, capture_output=True, timeout=30,
        )
        readme = project_dir / "README.md"
        readme.write_text(f"# {project_id}\n")
        subprocess.run(
            ["git", "-C", str(project_dir), "add", "."],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(project_dir), "commit", "-m", "init"],
            check=True, capture_output=True, timeout=30,
        )

    # ── Greenfield scaffold generation ──────────────────────────────────
    # For new projects with a known domain, generate the scaffold workspace
    # on master BEFORE creating the planning worktree.  This ensures the
    # scaffold is commit 0 — the planning agent and later execution nodes
    # all inherit the pre-structured workspace.
    #
    # Each domain maps to one or more standard slugs.  Single-engine
    # domains scaffold at the project root; multi-engine domains scaffold
    # into subdirectories named after each slug.
    domains = (meta_goal or {}).get("domains") if meta_goal else None
    domain = domains[0] if domains else ((meta_goal or {}).get("domain") if meta_goal else None)
    _scaffold_committed = False
    if domain and is_greenfield(project_dir):
        standard_slugs = _domain_to_standard_slug(domain)
        if standard_slugs:
            multi = len(standard_slugs) > 1
            for slug in standard_slugs:
                subdir = slug if multi else "."
                try:
                    generate_workspace(project_dir, slug, subdir=subdir, defer_commit=multi)
                    logger.info(
                        "Greenfield scaffold generated for domain=%s standard=%s subdir=%s",
                        domain, slug, subdir,
                    )
                    _scaffold_committed = True
                except ValueError as exc:
                    logger.warning(
                        "Scaffold generation skipped for %s/%s: %s", domain, slug, exc,
                    )
            if multi and _scaffold_committed:
                subprocess.run(
                    ["git", "-C", str(project_dir), "add", "-A"],
                    check=True, capture_output=True, timeout=30,
                )
                subprocess.run(
                    ["git", "-C", str(project_dir), "commit", "-m", "scaffold: generated workspace(s)"],
                    check=True, capture_output=True, timeout=30,
                )

    # Remove prior worktree if exists
    subprocess.run(
        ["git", "-C", str(project_dir), "worktree", "remove", "--force", str(worktree_path)],
        capture_output=True, timeout=60,
    )

    # Create worktree from master (or current branch)
    subprocess.run(
        ["git", "-C", str(project_dir), "branch", "-D", f"planning-{plan_id}"],
        capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(project_dir), "worktree", "add",
         "-b", f"planning-{plan_id}", str(worktree_path)],
        check=True, capture_output=True, timeout=60,
    )

    # Scaffold .plan/ dirs + deterministic stubs
    (worktree_path / ".plan" / "nodes").mkdir(parents=True, exist_ok=True)
    (worktree_path / ".plan" / "checks").mkdir(parents=True, exist_ok=True)

    # Write scoped opencode.json with domain-filtered static brief
    _write_planner_opencode_json(worktree_path, domain=domain)

    # Scaffold deterministic .plan/ stubs (index skeleton, node/check stubs, TODO.md)
    from backend.planning.plan_scaffolds import scaffold_plan_worktree
    scaffold_plan_worktree(worktree_path, meta_goal=meta_goal or None)

    # Ensure .plan/ is NOT gitignored — agent search/grep tools respect .gitignore
    _unignore_plan_dotdir(worktree_path)

    # Emit workspace picture — a bounded view of the scaffold / existing code the
    # planner can use to understand the workspace structure without scanning files.
    emit_workspace_picture(worktree_path)

    logger.info("Planning worktree created at %s", worktree_path)
    return str(worktree_path)


def on_planning_failed(
    worktree_path: str,
    project_id: str,
    workspace_root: str | Path,
) -> None:
    """Remove planning worktree after final failure. Idempotent.

    When the environment variable ``PLANNING_DEBUG=1`` is set, the worktree
    is **not** removed so it can be inspected for debugging.
    """
    root = Path(workspace_root).resolve()
    project_dir = root / project_id
    wt = Path(worktree_path)

    if not wt.exists():
        logger.info("Planning worktree %s already removed", worktree_path)
        return

    # When debugging, keep the worktree for post-mortem inspection
    if os.environ.get("PLANNING_DEBUG", "").strip() in ("1", "true", "yes"):
        logger.info(
            "PLANNING_DEBUG=1 — preserving planning worktree at %s for debugging",
            worktree_path,
        )
        return

    subprocess.run(
        ["git", "-C", str(project_dir), "worktree", "remove", "--force", str(wt)],
        capture_output=True, timeout=60,
    )
    # Also remove the branch
    subprocess.run(
        ["git", "-C", str(project_dir), "branch", "-D", f"planning-{wt.name}"],
        capture_output=True, timeout=30,
    )
    logger.info("Planning worktree removed %s", worktree_path)


# ── Scoped opencode.json ──────────────────────────────────────────────────


def _write_planner_opencode_json(worktree: Path, domain: str | None = None) -> None:
    """Write scoped permissions to the planning worktree.

    The meta-planner agent may edit ONLY ``.plan/**``.
    Bash is read-only (ls, cat, find). Research (webfetch, websearch) allowed.

    Reads the meta-planner agent config from the database to populate the
    model, allowed tools, and agent definition in opencode.json, so the
    worktree reflects the full agent profile (not just hardcoded permissions).

    When *domain* is provided, ``_build_static_brief`` includes domain-filtered
    capability vocabulary and quality dimensions in ``NODE_BRIEF.md``.
    """
    from backend.db.queries import get_agent_config

    cfg = get_agent_config("meta-planner") or {}
    model = cfg.get("model_preference") or "litellm/deepseek-planning"
    sys_prompt = (cfg.get("system_prompt") or "").strip()

    conductor_dir = worktree / ".conductor"
    conductor_dir.mkdir(parents=True, exist_ok=True)
    brief_path = conductor_dir / "NODE_BRIEF.md"

    static_brief = _build_static_brief(domain=domain)
    brief_content = static_brief
    if sys_prompt:
        brief_content = f"{sys_prompt}\n\n---\n\n{static_brief}"
    brief_path.write_text(brief_content, encoding="utf-8")

    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        "permission": {
            "edit": "allow",
            "bash": "allow",
        },
    }

    (worktree / "opencode.json").write_text(
        json.dumps(config, indent=2) + "\n",
    )
    logger.debug("Scoped opencode.json written to %s", worktree)


# ── Slate helpers (DB queries) ────────────────────────────────────────────


def _roster_slate() -> list[dict[str, Any]]:
    """Return all active agent_configs with their capabilities, for the brief.

    Each entry: ``{"agent_config_id": "...", "capabilities": [...], "backend": "..."}``
    """
    from backend.db.queries import conn as db_conn

    with db_conn() as c:
        rows = c.execute(
            """
            SELECT agent_config_id, COALESCE(new_capabilities, '[]'::jsonb) AS caps,
                   COALESCE(execution->>'backend', 'opencode') AS backend,
                   group_id
            FROM agent_configs
            WHERE active = true
            ORDER BY agent_config_id
            """
        ).fetchall()
    return [
        {"agent_config_id": r["agent_config_id"], "capabilities": r["caps"] or [],
         "backend": r["backend"] or "opencode",
         "group_id": r["group_id"] or ""}
        for r in rows
    ]


def _capability_slate(domain: str | None = None) -> list[dict[str, Any]]:
    from backend.db.queries import conn as db_conn

    if domain:
        from backend.planning.capability.selector import DOMAIN_TO_FAMILY
        families = DOMAIN_TO_FAMILY.get(domain, [])
        if families:
            with db_conn() as c:
                placeholders = ",".join("%s" for _ in families)
                rows = c.execute(
                    f"""
                    SELECT name, family
                    FROM capabilities
                    WHERE family ?| array[{placeholders}]
                    ORDER BY name
                    """,
                    families,
                ).fetchall()
            return [
                {"name": r["name"], "family": r["family"]}
                for r in rows
            ]

    with db_conn() as c:
        rows = c.execute(
            """
            SELECT name, family
            FROM capabilities
            ORDER BY name
            """
        ).fetchall()
    return [
        {"name": r["name"], "family": r["family"]}
        for r in rows
    ]


def capability_dims_slate(
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Return capabilities with their quality_dimensions for the brief.

    Same family-filter as ``_capability_slate`` but includes the
    ``quality_dimensions`` field so the meta-planner agent can seed
    per-node checks from them.
    """
    from backend.db.queries import conn as db_conn

    families: list[str] = []
    if domain:
        from backend.planning.capability.selector import DOMAIN_TO_FAMILY
        families = DOMAIN_TO_FAMILY.get(domain, [])

    if families:
        with db_conn() as c:
            placeholders = ",".join("%s" for _ in families)
            rows = c.execute(
                f"""
                SELECT name, family, quality_dimensions
                FROM capabilities
                WHERE family ?| array[{placeholders}]
                ORDER BY name
                """,
                families,
            ).fetchall()
        return [
            {
                "name": r["name"],
                "family": r["family"],
                "dimensions": r.get("quality_dimensions") or [],
            }
            for r in rows
        ]

    with db_conn() as c:
        rows = c.execute(
            """
            SELECT name, family, quality_dimensions
            FROM capabilities
            ORDER BY name
            """
        ).fetchall()
    return [
        {
            "name": r["name"],
            "family": r["family"],
            "dimensions": r.get("quality_dimensions") or [],
        }
        for r in rows
    ]


def _schema_text() -> str:
    """Return JSON schema docstrings for the brief."""
    from contracts.plan_assembler import (
        check_json_schema,
        index_json_schema,
        per_node_json_schema,
    )

    idx_schema = index_json_schema()
    node_schema = per_node_json_schema()
    ck_schema = check_json_schema()
    return (
        f"INDEX SCHEMA (for .plan/index.json):\n{json.dumps(idx_schema, indent=2)}\n\n"
        f"NODE SCHEMA (for each .plan/nodes/node-NNN.json):\n{json.dumps(node_schema, indent=2)}\n\n"
        f"CHECK SCHEMA (for each entry in .plan/checks/node-NNN.json):\n{json.dumps(ck_schema, indent=2)}"
    )


def _fmt_dims_for_brief(
    dimensions: list[dict[str, Any]],
) -> str:
    """Format capability dimensions for the brief, mapping ``kind`` values
    to check terminology (``objective``→``deterministic``, ``subjective``→``rubric``)
    so the agent doesn't confuse dimension ``kind`` with check ``kind``."""
    mapped: list[dict[str, Any]] = []
    for dim in dimensions:
        d = dict(dim)
        kind_val = d.pop("kind", "?")
        d["check_type"] = (
            "deterministic" if kind_val == "objective"
            else "rubric" if kind_val == "subjective"
            else kind_val
        )
        mapped.append(d)
    return str(mapped)


def _build_static_brief(domain: str | None = None) -> str:
    """Build the static reference section for NODE_BRIEF.md.

    This content does NOT depend on the specific goal — it is the meta-planner's
    reference manual (role, steps, rules, schemas, capability vocabulary,
    quality dimensions, roster). Written once at worktree creation and loaded
    as an ``{file:}`` instruction so the agent sees it alongside the dynamic
    ``planning_brief()`` message.

    When *domain* is provided, the CAPABILITY VOCABULARY and CAPABILITY
    DIMENSIONS sections are filtered to that domain's families.
    """
    parts: list[str] = []
    _NL = "\n"

    parts.append("# META-PLANNER REFERENCE")
    parts.append("")

    # ── YOUR ROLE ────────────────────────────────────────────────────────
    parts.append("## YOUR ROLE")
    parts.append("")
    parts.append(
        "You are a **Plan Architect** — an expert agent that decomposes goals "
        "into structured, executable plans. Your job is to take a goal and spec, "
        "examine the available capabilities (agents + tools), and produce a DAG "
        "of work nodes that, together, achieve the goal. Each node specifies what "
        "capability (agent type) will do the work, what files it may touch, and "
        "what quality gates (checks) must pass."
    )
    parts.append("")

    # ── EXECUTION STEPS ──────────────────────────────────────────────────
    parts.append("## EXECUTION STEPS")
    parts.append("")
    parts.append("Follow these steps **in order** to produce the plan DAG.")
    parts.append("")
    steps = [
        (
            "### STEP 1 — Scope",
            "Identify the concrete files, APIs, or components that need to be created "
            "or modified. Determine what is in scope and out of scope.",
        ),
        (
            "### STEP 2 — Assign Capabilities",
            "Examine the CAPABILITY VOCABULARY section below. For each work chunk, "
            "choose the most suitable capability (highest tool/verification match). "
            "Assign exactly one capability per node.",
        ),
        (
            "### STEP 3 — Generate Plan DAG",
            "Create JSON files at the worktree path following the schema below. "
            "Write .plan/index.json first, then per-node files, then per-node check files.",
        ),
    ]
    for title, desc in steps:
        parts.append(title)
        parts.append(desc)
        parts.append("")

    # ── OUTPUT FORMAT ────────────────────────────────────────────────────
    parts.append("## OUTPUT FORMAT (STRICT — FOLLOW THIS PROCEDURE)")
    parts.append("")
    parts.append(
        "Use .plan/TODO.md as a checklist to track your progress through these steps."
    )
    parts.append("")
    parts.append(
        "**STEP 1** — Write .plan/index.json ONLY first (skeleton): "
        "an index with goal, spec, quality_intent, and a nodes array listing "
        "each node's id, file, depends_on, and description."
    )
    parts.append("")
    parts.append(
        "**STEP 2** — For each node in index order, write .plan/nodes/node-NNN.json "
        "(filename MUST match id). Each node file carries full fields: "
        "deliverables, members, edit paths, context paths, and the capabilities list."
    )
    parts.append("")
    parts.append(
        "**STEP 3** — For each node, write .plan/checks/node-NNN.json seeded from "
        "that node's capability quality_dimensions (see CAPABILITY DIMENSIONS below). "
        "Objective dimensions → L1 deterministic checks. "
        "Subjective dimensions → L2 rubric checks. "
        "EVERY node MUST include the ``run_md_present`` L1 check "
        "(id='run_md_present', tier='L1', kind='deterministic', cmd='test -f RUN.md'). "
        "Do NOT include runtime checks (curl, localhost, pytest) on non-runnable nodes."
    )
    parts.append("")
    parts.append(
        "Check JSON field rules (the validator checks these strictly):"
    )
    parts.append("")
    parts.append("| Tier | kind | Required fields | Optional fields |")
    parts.append("|------|------|----------------|-----------------|")
    parts.append(
        "| L1 | deterministic | id, tier='L1', kind='deterministic', "
        "cmd (shell command string), expect (object with exit_code=0) | "
        "weight (default 1.0) |"
    )
    parts.append(
        "| L2 | rubric | id, tier='L2', kind='rubric', "
        "rubric_item (scoring question string, REQUIRED for rubric checks) | "
        "criterion, weight (default 1.0) |"
    )
    parts.append("")
    parts.append(
        "EXAMPLE L1 check: "
        '{"id": "file_exists", "tier": "L1", "kind": "deterministic", '
        '"cmd": "test -f main.py", "expect": {"exit_code": 0}}'
    )
    parts.append("")
    parts.append(
        "EXAMPLE L2 check: "
        '{"id": "code_style", "tier": "L2", "kind": "rubric", '
        '"rubric_item": "Does the code follow PEP 8 conventions?"}'
    )
    parts.append("")
    parts.append(
        "**STEP 4** — SELF-VERIFY before finishing: "
        "Re-read .plan/index.json and confirm every listed node has BOTH its "
        ".plan/nodes/ file AND its .plan/checks/ file on disk. "
        "Verify every node's internal ``id`` field matches its filename. "
        "Verify no orphan files exist. "
        "Fix ANY mismatch found. Only when everything is consistent, respond "
        "with a short confirmation."
    )
    parts.append("")

    # ── RULES ────────────────────────────────────────────────────────────
    parts.append("## RULES")
    parts.append("")
    rules = [
        "Nodes must be scoped and right-sized (not too coarse, not too fine).",
        "Dependencies must be acyclic and resolve within the DAG.",
        "Every node must have at least one deliverable.",
        "Members MUST be from the ROSTER only — never hallucinate an agent_config.",
        "Each node's ``capabilities`` list MUST be a subset of the assigned member's "
        "declared capabilities (shown in the ROSTER).",
        "Write ONLY .plan/ files. Do NOT write code or touch other files.",
        "Node IDs MUST use the ``node-NNN`` format with zero-padded numbers (e.g. ``node-001``, ``node-002``). "
        "Do NOT use descriptive or arbitrary IDs — the deterministic assembler relies on ``node-NNN`` naming.",
        "TOOL RULE — .plan/ scaffold files already exist (index.json, node stubs, check stubs, TODO.md). "
        "Use ``edit`` to modify them. Do NOT use ``write`` on existing files — the tool rejects it. "
        "Use ``write`` only for new files not already in .plan/. "
        "Do NOT output file contents in your message body.",
    ]
    for rule in rules:
        parts.append(f"- {rule}")
    parts.append("")

    # ── CAPABILITY VOCABULARY (domain-filtered when domain is known) ────
    caps = _capability_slate(domain)
    caps_formatted = "\n".join(
        f"  - {c['name']}  (family={c['family']})"
        for c in caps
    )
    parts.append("## CAPABILITY VOCABULARY")
    parts.append("")
    parts.append(
        "Use these capability names in the ``capabilities`` field of each node:"
    )
    parts.append("")
    parts.append(caps_formatted)
    parts.append("")

    # ── CAPABILITY DIMENSIONS (domain-filtered when domain is known) ────
    dims = capability_dims_slate(domain)
    dims_formatted = "\n".join(
        f"  - {d['name']}  dims={_fmt_dims_for_brief(d['dimensions'])}"
        for d in dims
    )
    parts.append("## CAPABILITY DIMENSIONS")
    parts.append("")
    parts.append(
        "Seed checks from these quality_dimensions — objective → L1, subjective → L2:"
    )
    parts.append("")
    parts.append(dims_formatted)
    parts.append("")

    # ── PLAN DAG SCHEMA ──────────────────────────────────────────────────
    schema = _schema_text()
    parts.append("## PLAN DAG SCHEMA")
    parts.append("")
    parts.append(schema)
    parts.append("")

    # ── ROSTER ───────────────────────────────────────────────────────────
    roster = _roster_slate()
    parts.append("## AVAILABLE CAPABILITIES (Roster)")
    parts.append("")
    parts.append(
        "The following agent configurations are available. "
        "Each has a unique ``agent_config_id`` and a list of ``capabilities``. "
        "Assign members from this list only."
    )
    parts.append("")
    parts.append(json.dumps(roster, indent=2))
    parts.append("")

    return _NL.join(parts)


# ── Brief builders ────────────────────────────────────────────────────────


def planning_brief(
    meta_goal: dict[str, Any],
    worktree: str,
) -> str:
    """Build the dynamic portion of the meta-planner brief.

    The static reference (role, steps, rules, schemas, capability vocabulary,
    quality dimensions, roster) lives in NODE_BRIEF.md loaded as a
    ``{file:}`` instruction. This message provides the dynamic parts:
    goal, spec, quality_intent, worktree path, and pointers to workspace
    context (WORKSPACE.md for scaffold structure, AGENTS.md for conventions).

    The full brief the agent sees = NODE_BRIEF.md (instruction) + this message.
    """
    brief = f"""GOAL: {meta_goal.get('goal', '')}

STRICT DIRECTIVE — You are a PLAN ARCHITECT.
Your ONLY output is .plan/ files (index.json, nodes/*, checks/*) that strictly serve this GOAL.
Do NOT write any implementation code, source files, tests, or configuration.
Follow the schema and rules in .conductor/NODE_BRIEF.md exactly — the deterministic evaluator checks your .plan/ structure, not the goal's domain.

SPEC: {meta_goal.get('spec', '')}

QUALITY INTENT (guide for plan structure, NOT implementation details):
{meta_goal.get('quality_intent', '')}
    The quality intent describes what makes a good PLAN: appropriate node scope,
    well-defined checks, clear deliverables, and realistic dependencies.
    It is NOT a specification for code implementation.

ESTIMATED NODE COUNT: {meta_goal.get('estimated_node_count', '')}
    Decompose the goal into exactly this many nodes unless the goal
    fundamentally cannot be expressed within that count.

WORKTREE: {worktree}

    Use .plan/TODO.md as a checklist. See .conductor/NODE_BRIEF.md for the full
    static reference (schemas, capability vocabulary, quality dimensions,
    detailed instructions, rules, and capability roster).

    Read .plan/research/WORKSPACE.md — the workspace already has structure
    (scaffold, manifests, standards). Your nodes' deliverable paths MUST fit
    within this existing structure. Do NOT plan scaffold-creation or
    project-init work (pyproject.toml, src/ layout, README, etc.).

    If AGENTS.md exists in the worktree root, it contains domain coding
    conventions — consult it when planning file-level deliverables.

    KEY TOOL RULE: All .plan/ scaffold files already exist. Use ``edit`` to modify
    them — do NOT use ``write`` on existing files (the tool rejects it).

    CRITICAL — ALL NODES REQUIRE DELIVERABLES: Every node listed in
    .plan/index.json must have a populated .plan/nodes/<node_id>.json file with
    non-empty ``task.deliverables``, ``task.text``, ``members``, ``capabilities``,
    and ``success.text``.  The validator checks EVERY node simultaneously — a
    single missing deliverable on ANY node will fail the entire plan.  Do NOT
    leave any node as a stub.

    {_inline_file_refs(meta_goal.get('estimated_node_count', 2))}

    Use the file references below (``[[AION_FILES]]``) to read/edit the specific
    ``.plan/`` files — the list includes all scaffolded nodes and checks."""

    brief += _build_aion_files_block(worktree)

    return brief


def retry_brief(
    prior_feedback: list[str],
    meta_goal: dict[str, Any],
    worktree: str,
) -> str:
    """Build a retry brief with only the steering delta (no full brief re-send).

    The static reference (role, steps, rules, schemas, capability vocabulary,
    quality dimensions, roster) is loaded from ``.conductor/NODE_BRIEF.md``
    via the ``{file:}`` instruction — the new room reuses the same worktree
    so it is still available.

    This message provides ONLY the validation feedback and fix instructions,
    avoiding duplication of dynamic content already embedded in
    ``.plan/index.json`` (goal, spec, quality_intent) and ``NODE_BRIEF.md``
    (capabilities, dimensions, schemas).

    Args:
        prior_feedback: List of error messages from the assembler, pydantic,
            or gate (file-targeted when possible).
        meta_goal: Same meta-goal dict as the original brief.
        worktree: Path to the planning worktree.

    Returns:
        Delta-only brief string — validation result + fix instructions.
    """
    from contracts.plan_assembler import render_deterministic_feedback

    fix_block = render_deterministic_feedback(worktree)
    feedback_block = "\n".join(f"  - {msg}" for msg in prior_feedback)

    # Scope @ refs + [[AION_FILES]] to only the files needing fixes
    fix_files = _extract_fix_files(fix_block)
    # Also extract file paths from RAW ERRORS (staffing/GATE failures on structurally-clean nodes)
    error_files = _extract_fix_files_from_raw_errors(feedback_block)
    fix_files.update(error_files)
    if fix_files:
        scoped_refs = ["@.plan/index.json", "@.plan/TODO.md"]
        scoped_refs += sorted(f"@{p}" for p in fix_files)
        refs_line = "Referenced files: " + ", ".join(scoped_refs) + "."
        aion_block = _build_aion_files_block(worktree, only_relpaths=fix_files)
    else:
        refs_line = _inline_file_refs(meta_goal.get("estimated_node_count", 2))
        aion_block = _build_aion_files_block(worktree)

    brief = f"""See .conductor/NODE_BRIEF.md for the full static reference (schemas,
capability vocabulary, quality dimensions, detailed instructions, rules, and
capability roster). Use .plan/TODO.md as a checklist.

# ── DETERMINISTIC VALIDATION RESULT ────────────────────────
{fix_block}

# ── RAW ERRORS FROM ASSEMBLER / GATE ──────────────────────
{feedback_block if prior_feedback else "  (no raw errors)"}

{refs_line}

# ── INSTRUCTIONS ───────────────────────────────────────────
Fix ONLY the files listed under "FIX THESE" above (includes files referenced in RAW ERRORS).
Do NOT touch files listed under "CORRECT" unless they are also referenced in RAW ERRORS.
Edit the specific .plan/ files referenced — do NOT rewrite the entire plan from scratch."""
    brief += aion_block
    return brief
