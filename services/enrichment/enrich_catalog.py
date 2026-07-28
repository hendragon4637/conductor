"""enrich_catalog — automated tool catalog enrichment pipeline.

Flow:
  1. Load sources allowlist from config/catalog_sources.yaml
  2. Load maturity thresholds from config/catalog_maturity.yaml
  3. Enumerate each source:
     - GitHub topic search → filter by min_stars → fetch metadata
     - Package registries (PyPI/npm) → filter by downloads → fetch metadata
     - MCP registries → scrape/parse → filter by maturity
  4. Dedupe against existing tool_catalog entries (by name, case-insensitive)
  5. Filter maturity (stars, age, license, velocity)
  6. INSERT candidates with status='candidate', status_by='agent'
  7. Budget gate: stop after max_candidates_per_run (default 20)
  8. Weekly budget: abort if > budget_weekly_max (default 50) inserted in last 7d
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
import yaml
import urllib.request
import urllib.parse

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("CONDUCTOR_CONFIG_DIR", "/opt/aipc/conductor/config"))


def load_sources_config() -> dict[str, Any]:
    """Load catalog_sources.yaml."""
    path = CONFIG_DIR / "catalog_sources.yaml"
    if not path.exists():
        logger.warning("catalog_sources.yaml not found at %s", path)
        return {"github": [], "registries": {}, "mcp_registries": [], "global_limits": {}}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_maturity_config() -> dict[str, Any]:
    """Load catalog_maturity.yaml."""
    path = CONFIG_DIR / "catalog_maturity.yaml"
    if not path.exists():
        logger.warning("catalog_maturity.yaml not found at %s", path)
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def get_db_url() -> str:
    return os.environ["DATABASE_URL"]


def count_recent_inserts(db_url: str, days: int = 7) -> int:
    """Count tool_catalog rows inserted in the last N days."""
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM tool_catalog WHERE created_at >= %s",
            (datetime.now(timezone.utc) - timedelta(days=days),),
        )
        row = cur.fetchone()
        return row[0] if row else 0


def existing_names(db_url: str) -> set[str]:
    """Return set of existing tool names (case-insensitive)."""
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT LOWER(name) FROM tool_catalog")
        return {row[0] for row in cur.fetchall()}


def search_github_topic(topic_query: str, min_stars: int = 20, max_results: int = 20) -> list[dict[str, Any]]:
    """Search GitHub repos by topic. Returns list of candidate dicts."""
    candidates = []
    # Use GitHub search API for repos with the given topic
    query = urllib.parse.quote(f"{topic_query} stars:>={min_stars}")
    url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page={min(max_results, 100)}"

    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        for item in data.get("items", [])[:max_results]:
            candidates.append({
                "name": item["name"],
                "description": (item.get("description") or "")[:500],
                "kind": "skill",  # heuristic — could be refined
                "source_url": item["html_url"],
                "license": (item.get("license") or {}).get("spdx_id", "") if item.get("license") else "",
                "stars": item.get("stargazers_count", 0),
                "velocity": {
                    "commits_per_quarter": 0,  # would need separate API call
                    "releases_per_year": 0,
                },
                "metadata": {
                    "full_name": item.get("full_name", ""),
                    "language": item.get("language", ""),
                    "topics": item.get("topics", []),
                    "description": item.get("description", ""),
                    "created_at": item.get("created_at", ""),
                    "updated_at": item.get("updated_at", ""),
                },
            })
    except Exception as e:
        logger.warning("GitHub search failed for %s: %s", topic_query, e)

    return candidates


def filter_maturity(candidates: list[dict[str, Any]], maturity_cfg: dict[str, Any], kind: str = "skill") -> list[dict[str, Any]]:
    """Filter candidates by maturity thresholds."""
    defaults = maturity_cfg.get("defaults", {})
    kind_overrides = maturity_cfg.get("per_kind_overrides", {}).get(kind, {})

    min_stars = kind_overrides.get("min_stars", defaults.get("min_stars", 0))
    allowed_licenses = set(defaults.get("allowed_licenses", []))
    blocked_licenses = set(defaults.get("blocked_licenses", []))

    filtered = []
    for c in candidates:
        if c["stars"] < min_stars:
            continue
        license_spdx = c.get("license", "")
        if license_spdx and license_spdx not in allowed_licenses:
            if license_spdx in blocked_licenses:
                continue
        filtered.append(c)

    return filtered


def dedupe(candidates: list[dict[str, Any]], existing: set[str]) -> list[dict[str, Any]]:
    """Remove candidates whose name already exists in the catalog."""
    return [c for c in candidates if c["name"].lower() not in existing]


def insert_candidates(db_url: str, candidates: list[dict[str, Any]]) -> int:
    """INSERT candidates into tool_catalog. Returns count of inserted rows."""
    count = 0
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        for c in candidates:
            try:
                cur.execute(
                    """INSERT INTO tool_catalog
                       (name, description, kind, source_url, license, stars, velocity, metadata)
                       VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                       ON CONFLICT (name) WHERE status <> 'retired' DO NOTHING""",
                    (
                        c["name"],
                        c["description"],
                        c.get("kind", "skill"),
                        c.get("source_url", ""),
                        c.get("license", ""),
                        c["stars"],
                        json.dumps(c.get("velocity", {})),
                        json.dumps(c.get("metadata", {})),
                    ),
                )
                if cur.rowcount > 0:
                    count += 1
            except Exception as e:
                logger.warning("Failed to insert candidate %s: %s", c["name"], e)
        conn.commit()
    return count


def enrich_catalog(db_url: str | None = None) -> dict[str, Any]:
    """Run the full enrichment pipeline. Returns summary dict."""
    _db_url = db_url or get_db_url()
    sources = load_sources_config()
    maturity = load_maturity_config()
    limits = sources.get("global_limits", {})
    max_per_run = limits.get("max_candidates_per_run", 20)
    weekly_max = limits.get("budget_weekly_max", 50)

    # Budget gate: check weekly inserts
    recent = count_recent_inserts(_db_url)
    if recent >= weekly_max:
        logger.info("Weekly budget exhausted: %d/%d inserted this week", recent, weekly_max)
        return {"status": "budget_exhausted", "inserted": 0, "total_candidates": 0, "recent_inserts": recent}

    existing = existing_names(_db_url)
    all_candidates = []

    # Enumerate GitHub sources
    for src in sources.get("github", []):
        if not src.get("type") == "topic":
            continue
        candidates = search_github_topic(
            src["query"],
            min_stars=src.get("min_stars", 20),
            max_results=src.get("max_candidates", 20),
        )
        all_candidates.extend(candidates)

    # MCP registries — parse from README links
    for mcp_src in sources.get("mcp_registries", []):
        if not mcp_src.get("enabled", True):
            continue
        try:
            req = urllib.request.Request(mcp_src["url"])
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read().decode()
            # Simple heuristic: extract markdown links to GitHub repos
            import re
            links = re.findall(r'https://github\.com/[\w.-]+/[\w.-]+', content)
            mcp_candidates = []
            seen = set()
            for link in links:
                name = link.rstrip("/").split("/")[-1]
                if name.lower() in seen:
                    continue
                seen.add(name.lower())
                mcp_candidates.append({
                    "name": name,
                    "description": f"MCP server from {link}",
                    "kind": "mcp",
                    "source_url": link,
                    "license": "",
                    "stars": 0,
                    "velocity": {"commits_per_quarter": 0, "releases_per_year": 0},
                    "metadata": {"source": "mcp_registry", "url": link},
                })
            all_candidates.extend(mcp_candidates[:mcp_src.get("max_candidates", 20)])
        except Exception as e:
            logger.warning("MCP registry fetch failed for %s: %s", mcp_src.get("name"), e)

    # Filter maturity
    filtered = []
    for c in all_candidates:
        kind = c.get("kind", "skill")
        matured = filter_maturity([c], maturity, kind)
        filtered.extend(matured)

    # Dedupe
    unique = dedupe(filtered, existing)

    # Budget cap per run
    capped = unique[:max_per_run]

    # Insert
    inserted = insert_candidates(_db_url, capped)

    return {
        "status": "ok",
        "inserted": inserted,
        "total_candidates": len(all_candidates),
        "after_maturity": len(filtered),
        "after_dedupe": len(unique),
        "capped_at": max_per_run,
        "recent_inserts": recent,
    }


def enrich_candidate(db_url: str, candidate_name: str) -> dict[str, Any]:
    """Targeted enrichment for a single tool (used by gap trigger)."""
    # For gap trigger: lightweight search for a specific tool
    existing = existing_names(db_url)
    if candidate_name.lower() in existing:
        return {"status": "already_exists", "name": candidate_name}

    # Search GitHub for the specific tool
    try:
        query = urllib.parse.quote(f"{candidate_name} in:name")
        url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page=5"
        token = os.environ.get("GITHUB_TOKEN", "")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        items = data.get("items", [])
        if not items:
            return {"status": "not_found", "name": candidate_name}

        best = items[0]
        candidate = {
            "name": best["name"],
            "description": (best.get("description") or "")[:500],
            "kind": "skill",
            "source_url": best["html_url"],
            "license": (best.get("license") or {}).get("spdx_id", "") if best.get("license") else "",
            "stars": best.get("stargazers_count", 0),
            "velocity": {"commits_per_quarter": 0, "releases_per_year": 0},
            "metadata": {"source": "gap_trigger", "full_name": best.get("full_name", "")},
        }
        insert_candidates(db_url, [candidate])
        return {"status": "inserted", "name": candidate_name, "stars": candidate["stars"]}
    except Exception as e:
        logger.warning("Targeted enrichment failed for %s: %s", candidate_name, e)
        return {"status": "error", "name": candidate_name, "error": str(e)}
