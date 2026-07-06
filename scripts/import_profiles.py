#!/usr/bin/env python3
"""Import agent profiles from three external repos into Conductor DB.

Files 02+03 from the Conductor agent profiles import plan:

  - agency-agents/       261 agent .md files organized by division
  - wshobson-agents/     194 agent .md + 106 command files under plugins/
  - awesome-agent-skills/ 1500+ skill links in README catalog

This script is idempotent: upsert, never duplicate.
Run: cd /opt/aipc/conductor && .venv/bin/python scripts/import_profiles.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

import yaml
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import_profiles")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = Path("/opt/aipc/conductor")
IMPORTS = BASE / "imports"
AGENCY_DIR = IMPORTS / "agency-agents"
WSHOBSON_DIR = IMPORTS / "wshobson-agents"
AWESOME_DIR = IMPORTS / "awesome-agent-skills"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OMO_RESERVED: set[str] = {
    "sisyphus", "prometheus", "atlas", "hephaestus",
    "oracle", "librarian", "explore", "build", "plan", "general",
}

# Canonical capability vocabulary — matches DB capabilities table.
CAPABILITIES_VOCAB: list[str] = [
    "backend_api",
    "cli_tool",
    "frontend",
    "generic",
    "research_report",
    "game_build",
    "music_generation",
    "video_content",
    "analytics_assistant",
    "data_pipeline",
    "agentic_business_flow",
]

# Map observed tool names from source repos → Conductor logical tools.
# Source tools on the left are matched case-insensitively substrings.
TOOL_ALIAS_MAP: dict[str, str] = {
    "read": "read_file",
    "glob": "read_file",
    "grep": "read_file",
    "write": "write_file",
    "edit": "write_file",
    "bash": "shell",
    "execute": "shell",
    "run": "shell",
    "terminal": "shell",
    "browser": "browser",
    "web": "read_web",
    "websearch": "read_web",
    "search": "read_web",
    "http": "http",
    "fetch": "http",
    "url": "http",
    "webfetch": "http",
    "agent": "shell",  # spawn sub-agents — map to shell as fallback
}

CANONICAL_TOOLS: set[str] = {"write_file", "read_file", "shell", "browser", "http", "read_web"}

# Override model for ALL imported agents.
OVERRIDE_MODEL = "nvidia/openai/gpt-oss-120b"

# agency-agents division → Conductor domain family
DIVISION_FAMILY_MAP: dict[str, str] = {
    "academic": "research",
    "design": "design",
    "engineering": "software",
    "finance": "finance",
    "game-development": "game-dev",
    "gis": "gis",
    "healthcare": "healthcare",
    "marketing": "marketing",
    "paid-media": "marketing",
    "product": "product",
    "project-management": "product",
    "sales": "sales",
    "security": "security",
    "spatial-computing": "spatial-computing",
    "specialized": "specialized",
    "support": "support",
    "testing": "testing",
}

# Divisions to skip (no frontmatter agent files)
NON_AGENT_DIVISIONS: set[str] = {"integrations", "examples", "scripts", "strategy"}


# ===================================================================
# 1. PARSING
# ===================================================================
def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split '---'-delimited YAML frontmatter from markdown body.

    Returns (frontmatter_dict, body_string).  Returns ({}, text) when
    no valid frontmatter is found.
    """
    # Strip leading whitespace, then check for opening ---
    cleaned = text.lstrip()
    if not cleaned.startswith("---"):
        return {}, text

    # Find closing ---
    rest = cleaned[3:].lstrip("\n")
    end_match = re.search(r"\n---\s*\n", rest)
    if not end_match:
        return {}, text

    yaml_block = rest[: end_match.start()]
    body = rest[end_match.end() :]

    try:
        fm = yaml.safe_load(yaml_block)
        if not isinstance(fm, dict):
            return {}, body
        return fm, body.strip()
    except yaml.YAMLError as exc:
        logger.warning("YAML parse error in frontmatter: %s", exc)
        return {}, text


def parse_agent_md(filepath: Path, repo_label: str) -> dict[str, Any] | None:
    """Parse a single agent .md file into a neutral dict.

    Returns None if the file has no valid frontmatter (skip with warning).
    """
    text = filepath.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(text)

    if not fm or "name" not in fm:
        logger.debug("Skipping %s — no frontmatter or missing 'name'", filepath)
        return None

    raw_name = fm.get("name", "")
    description = fm.get("description", "")

    # Compute relative path within the repo
    rel_path = filepath.resolve().relative_to(IMPORTS.resolve() / repo_label).as_posix()

    result: dict[str, Any] = {
        "raw_name": raw_name,
        "description": description,
        "system_prompt": body,
        "import_ref": f"{repo_label}:{rel_path}",
        "raw_definition": fm,
        "repo": repo_label,
        "division": None,
        "plugin": None,
        "tools_raw": [],
        "is_command": False,
    }

    # repo-specific fields
    if repo_label == "agency-agents":
        # division = top-level dir name
        result["division"] = rel_path.split("/")[0]
    elif repo_label == "wshobson-agents":
        # path is plugins/<plugin>/agents/<name>.md or plugins/<plugin>/commands/<name>.md
        parts = rel_path.split("/")
        if len(parts) >= 3 and parts[0] == "plugins":
            result["plugin"] = parts[1]
        result["is_command"] = "commands" in rel_path

    # Extract tools from frontmatter
    tools_raw = fm.get("tools", [])
    if isinstance(tools_raw, str):
        tools_raw = [t.strip() for t in tools_raw.split(",") if t.strip()]
    if isinstance(tools_raw, list):
        result["tools_raw"] = tools_raw

    return result


def parse_all_agents(repo_label: str) -> list[dict[str, Any]]:
    """Recursively walk a repo and parse all .md agent files."""
    if repo_label == "agency-agents":
        base = AGENCY_DIR
    elif repo_label == "wshobson-agents":
        base = WSHOBSON_DIR
    else:
        raise ValueError(f"Unknown repo: {repo_label}")

    agents: list[dict[str, Any]] = []
    skipped = 0
    no_fm = 0

    for md_file in sorted(base.rglob("*.md")):
        rel = md_file.relative_to(base).as_posix()

        # Skip files in .git and top-level docs
        if rel.startswith(".git") or rel.startswith("docs/") or rel in (
            "README.md", "CONTRIBUTING.md", "LICENSE", "ARCHITECTURE.md",
            "CLAUDE.md", "AGENTS.md", "GEMINI.md", "Makefile",
        ):
            continue

        # For agency-agents, skip non-agent directories
        if repo_label == "agency-agents":
            top_dir = rel.split("/")[0]
            if top_dir in NON_AGENT_DIVISIONS or top_dir.startswith("."):
                continue

        # For wshobson, only parse agents/ and commands/ dirs
        if repo_label == "wshobson-agents":
            if not ("/agents/" in rel or "/commands/" in rel):
                continue

        parsed = parse_agent_md(md_file, repo_label)
        if parsed is None:
            no_fm += 1
            continue
        agents.append(parsed)
        skipped += 1

    logger.info(
        "Parsed %d agents from %s (%d skipped, %d no frontmatter)",
        len(agents), repo_label, skipped, no_fm,
    )
    return agents


# ===================================================================
# 2. TOOLS NORMALISATION
# ===================================================================
def normalize_tools(tools_raw: list[str]) -> list[str]:
    """Map raw tool names from frontmatter to Conductor logical tools."""
    normalized: set[str] = set()

    for t in tools_raw:
        t_lower = t.strip().lower()
        matched = False
        for alias, canonical in TOOL_ALIAS_MAP.items():
            if alias in t_lower:
                normalized.add(canonical)
                matched = True
        if not matched and t_lower and t_lower not in ("inherit",):
            # Keep unknown tools but warn
            logger.debug("Unknown tool '%s' — keeping as-is", t)
            normalized.add(t_lower)

    # Filter to known canonical set; keep unrecognised as-is
    return sorted(normalized)


# ===================================================================
# 3. AWESOME-AGENT-SKILLS PARSING (README catalog)
# ===================================================================
def parse_skills_catalog() -> dict[str, Any]:
    """Extract all markdown skill links from the awesome-agent-skills README.

    Returns a dict with 'links' (list of dicts) and 'count'.
    """
    readme_path = AWESOME_DIR / "README.md"
    if not readme_path.exists():
        logger.warning("awesome-agent-skills README not found at %s", readme_path)
        return {"links": [], "count": 0}

    text = readme_path.read_text(encoding="utf-8", errors="replace")
    # Match markdown links: [text](url) - optional leading - or *
    pattern = re.compile(r"[-*]\s*\[([^\]]+)\]\(([^)]+)\)")
    links: list[dict[str, str]] = []

    for match in pattern.finditer(text):
        name = match.group(1).strip()
        url = match.group(2).strip()
        # Skip non-skill links (table-of-contents markers, badges, images)
        if any(skip in name.lower() for skip in ("skill quality", "become a sponsor")):
            continue
        if url.startswith("#"):
            continue
        links.append({"name": name, "url": url})

    logger.info("Extracted %d skill links from awesome-agent-skills README", len(links))
    return {"links": links, "count": len(links)}


# ===================================================================
# 4. CAPABILITY CLASSIFIER (LLM via LiteLLM gateway)
# ===================================================================
def _call_llm(prompt: str, model: str = "deepseek-planning") -> str | None:
    """Call LiteLLM gateway on localhost and return response text."""
    url = os.environ.get("LITELLM_BASE", "http://localhost:4000/v1")
    api_key = os.environ.get("LITELLM_KEY_PLANNING", "")

    # Fall back to LITELLM_KEY for compatibility
    if not api_key:
        for keyname in ("LITELLM_API_KEY", "OPENAI_API_KEY"):
            val = os.environ.get(keyname)
            if val:
                api_key = val
                break

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 2048,
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{url.rstrip('/')}/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        choice = result.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        return content.strip() if content else None
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        logger.warning("LLM gateway unreachable: %s — falling back to 'generic'", exc)
        return None
    except (json.JSONDecodeError, KeyError, OSError, TimeoutError) as exc:
        logger.warning("LLM call failed: %s — falling back to 'generic'", exc)
        return None


def classify_capabilities(
    item: dict[str, Any], vocab: list[str], max_retries: int = 1
) -> list[str]:
    """Classify an agent/skill into capabilities via LLM.

    Uses the ``deepseek-planning`` model via the LiteLLM gateway.
    Falls back to ['generic'] on any failure.
    """
    name = item.get("raw_name", item.get("name", ""))
    description = item.get("description", "")

    vocab_list = "\n".join(f"  - {c}" for c in sorted(vocab))
    prompt = (
        f"Given this AI agent profile:\n"
        f"Name: {name}\n"
        f"Description: {description}\n\n"
        f"Select the most relevant capabilities from this controlled vocabulary:\n"
        f"{vocab_list}\n\n"
        f"Return ONLY a comma-separated list of matching capability names from the "
        f"vocabulary. Select at most 3. If nothing matches, return 'generic'.\n"
        f"Do NOT include any explanation or extra text."
    )

    for attempt in range(max_retries + 1):
        response = _call_llm(prompt)
        if response:
            # Parse comma-separated list
            candidates = [c.strip().lower() for c in response.split(",")]
            valid = [c for c in candidates if c in {v.lower() for v in vocab}]
            if valid:
                return sorted(set(valid))
            if attempt < max_retries:
                logger.debug("LLM returned invalid capabilities: %s — retrying", response)
                continue
        if attempt < max_retries:
            time.sleep(1)
            continue

    logger.debug("Falling back to 'generic' for %s", name)
    return ["generic"]


def _batch_classify_call(
    batch: list[dict[str, Any]], vocab: list[str], max_retries: int = 2,
) -> list[list[str]]:
    """Send a batch of up to 30 agents in ONE LLM call for classification.

    Retries up to ``max_retries`` times on None/parse failure, with 2s delay.
    Falls back to per-item calls only if all retries are exhausted.
    """
    vocab_list = "\n".join(f"  - {c}" for c in sorted(vocab))
    lines = []
    for i, item in enumerate(batch, 1):
        name = item.get("raw_name", item.get("name", "?"))
        desc = (item.get("description", "") or "")[:200]
        lines.append(f"{i}. Name: {name}\n   Description: {desc}")
    agent_block = "\n".join(lines)

    prompt = (
        f"You are classifying AI agent profiles into a controlled capability "
        f"vocabulary.  For EACH agent below, select the most relevant "
        f"capabilities (at most 3) from this vocabulary:\n"
        f"{vocab_list}\n\n"
        f"Agents to classify (return capabilities ONLY, not the agent name):\n"
        f"{agent_block}\n\n"
        f"Respond with ONLY a JSON array of arrays, one per agent in order. "
        f"Each inner array contains capability strings ONLY (NOT the agent name). "
        f"If nothing matches for an agent, use [\"generic\"]. "
        f"Example: [[\"backend_api\"], [\"frontend\"], [\"generic\"]]\n"
        f"Do NOT include agent names in the inner arrays — only capability names "
        f"from the vocabulary. No explanation, no markdown, just the JSON array."
    )

    for attempt in range(max_retries + 1):
        response = _call_llm(prompt)
        if not response:
            if attempt < max_retries:
                logger.warning("LLM returned None for batch (attempt %d/%d) — retrying", attempt + 1, max_retries + 1)
                time.sleep(2)
                continue
            logger.warning("LLM returned None for batch after %d attempts — falling back", max_retries + 1)
            return []

        try:
            # Strip any markdown fences
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("\n", 1)[0]
            cleaned = cleaned.strip()
            parsed = json.loads(cleaned)
            if not isinstance(parsed, list) or len(parsed) != len(batch):
                if attempt < max_retries:
                    logger.warning(
                        "Batch LLM shape mismatch (attempt %d/%d): len=%d expected=%d — retrying",
                        attempt + 1, max_retries + 1,
                        len(parsed) if isinstance(parsed, list) else 0,
                        len(batch),
                    )
                    time.sleep(2)
                    continue
                logger.warning(
                    "Batch LLM returned unexpected shape after %d attempts: type=%s len=%d (expected %d). "
                    "Raw response (first 300): %s",
                    max_retries + 1,
                    type(parsed).__name__, len(parsed) if isinstance(parsed, list) else 0,
                    len(batch), response[:300],
                )
                return []
            results = []
            for caps in parsed:
                if not isinstance(caps, list):
                    results.append(["generic"])
                else:
                    valid = [c for c in caps if c.lower() in {v.lower() for v in vocab}]
                    results.append(sorted(set(valid)) if valid else ["generic"])
            return results
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            if attempt < max_retries:
                logger.warning("Batch LLM parse failed (attempt %d/%d): %s — retrying", attempt + 1, max_retries + 1, exc)
                time.sleep(2)
                continue
            logger.warning(
                "Batch LLM parse failed after %d attempts: %s. Raw (first 300): %s",
                max_retries + 1, exc, response[:300],
            )
            return []

    return []


def batch_classify(
    items: list[dict[str, Any]],
    vocab: list[str],
    batch_size: int = 10,
    label: str = "agents",
    on_batch: Any = None,
) -> list[list[str]]:
    """Classify a list of items in true LLM batches, with progress logging.

    If ``on_batch`` is provided (callable(items, capabilities)), it is
    called after each batch is classified so the caller can flush results
    to DB immediately — avoiding total loss on crash mid-way.
    """
    results: list[list[str]] = []
    total = len(items)

    for i in range(0, total, batch_size):
        batch = items[i : i + batch_size]
        batch_results = _batch_classify_call(batch, vocab)

        if not batch_results or len(batch_results) != len(batch):
            # Fallback to per-item for this batch
            logger.warning("Batch LLM call failed — falling back to per-item for batch %d", i // batch_size)
            batch_results = []
            for item in batch:
                caps = classify_capabilities(item, vocab)
                batch_results.append(caps)

        # Assign capabilities to batch items
        for item, caps in zip(batch, batch_results):
            item["capabilities"] = caps

        # Flush callback (e.g. upsert to DB) — saves progress on each batch
        if on_batch is not None:
            on_batch(batch, batch_results)

        results.extend(batch_results)
        pct = len(results) / total * 100
        names = ", ".join(
            item.get("raw_name", item.get("name", "?"))[:20]
            for item in batch[:3]
        )
        logger.info(
            "Classified batch [%s %d/%d] %.0f%% — %s%s",
            label, len(results), total, pct, names,
            f" +{len(batch)-3} more" if len(batch) > 3 else "",
        )

        if i + batch_size < total:
            time.sleep(1)

    return results


# ===================================================================
# 5. DB UPSERT FUNCTIONS
# ===================================================================
def _get_engine() -> Any:
    """Create a SQLAlchemy engine from DATABASE_URL.

    Uses ``postgresql+psycopg://`` driver (psycopg v3 — the installed version).
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set in environment")
    # psycopg v3 is installed; force its dialect so SQLAlchemy doesn't
    # fail trying psycopg2 first.
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(db_url)


def _slugify(name: str) -> str:
    """Create a DB-safe slug from a name."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def upsert_skill(skill: dict[str, Any], engine: Any) -> str | None:
    """INSERT INTO skills … ON CONFLICT (skill_id) DO UPDATE.

    Returns the skill_id or None on failure.
    """
    skill_id = skill.get("skill_id", _slugify(skill.get("name", "")))
    if not skill_id:
        return None

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO skills (skill_id, name, description, body, tools, source, import_ref, updated_at)
                VALUES (:skill_id, :name, :description, :body, CAST(:tools AS jsonb), :source, :import_ref, now())
                ON CONFLICT (skill_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    body = EXCLUDED.body,
                    tools = CAST(EXCLUDED.tools AS jsonb),
                    source = EXCLUDED.source,
                    import_ref = EXCLUDED.import_ref,
                    updated_at = now()
            """),
            {
                "skill_id": skill_id,
                "name": skill.get("name", ""),
                "description": skill.get("description", ""),
                "body": skill.get("body", ""),
                "tools": json.dumps(skill.get("tools", [])),
                "source": skill.get("source", "imported"),
                "import_ref": skill.get("import_ref", ""),
            },
        )
    logger.debug("Upserted skill %s", skill_id)
    return skill_id


def upsert_capability_skill(capability: str, skill_id: str, engine: Any) -> None:
    """INSERT INTO capability_skills … ON CONFLICT DO NOTHING."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO capability_skills (capability, skill_id)
                VALUES (:capability, :skill_id)
                ON CONFLICT (capability, skill_id) DO NOTHING
            """),
            {"capability": capability, "skill_id": skill_id},
        )


def upsert_agent_config(agent: dict[str, Any], engine: Any) -> str | None:
    """INSERT INTO agent_configs … ON CONFLICT (agent_config_id) DO UPDATE.

    Collision guard: prefix with 'imp-' if the ID conflicts with OMO reserved
    names or already exists in the DB.
    """
    raw_name = agent.get("raw_name", "")
    slug = _slugify(raw_name)

    # Determine base ID
    agent_config_id = slug

    # Collision guard
    if agent_config_id.lower() in OMO_RESERVED:
        agent_config_id = f"imp-{agent_config_id}"
        logger.info("Collision guard: renamed '%s' → '%s'", slug, agent_config_id)

    # Check for existing ID collision
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM agent_configs WHERE agent_config_id = :id"),
            {"id": agent_config_id},
        ).scalar()
        if existing:
            agent_config_id = f"imp-{agent_config_id}"
            logger.info(
                "Existing ID collision: renamed '%s' → '%s'",
                slug, agent_config_id,
            )

    # Determine domain / family
    repo = agent.get("repo", "")
    division = agent.get("division")
    plugin = agent.get("plugin")

    if repo == "agency-agents" and division:
        domain = DIVISION_FAMILY_MAP.get(division, "specialized")
    elif repo == "wshobson-agents" and plugin:
        domain = _slugify(plugin)
    else:
        domain = "imported"

    # Normalize tools
    tools_raw = agent.get("tools_raw", [])
    tools = normalize_tools(tools_raw)

    # System prompt
    system_prompt = agent.get("system_prompt", "")

    # Capabilities
    capabilities = agent.get("capabilities", ["generic"])

    # Role: command files → "commander", agents → "executor"
    role = "commander" if agent.get("is_command") else "executor"

    execution = {
        "backend": "opencode",
        "model_preference": OVERRIDE_MODEL,
        "sub_agents": [],
    }

    raw_definition = agent.get("raw_definition", {})
    import_ref = agent.get("import_ref", "")

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO agent_configs (
                    agent_config_id, role, domain, system_prompt,
                    tools, new_capabilities, execution,
                    source, import_ref, raw_definition, backend_targets,
                    permission_policy, version, updated_at
                ) VALUES (
                    :agent_config_id, :role, :domain, :system_prompt,
                    CAST(:tools AS jsonb), CAST(:capabilities AS jsonb),
                    CAST(:execution AS jsonb),
                    'imported', :import_ref, CAST(:raw_definition AS jsonb),
                    '["opencode"]'::jsonb, '{}'::jsonb, 1, now()
                )
                ON CONFLICT (agent_config_id) DO UPDATE SET
                    role = EXCLUDED.role,
                    domain = EXCLUDED.domain,
                    system_prompt = EXCLUDED.system_prompt,
                    tools = CAST(EXCLUDED.tools AS jsonb),
                    new_capabilities = CAST(EXCLUDED.new_capabilities AS jsonb),
                    execution = CAST(EXCLUDED.execution AS jsonb),
                    source = EXCLUDED.source,
                    import_ref = EXCLUDED.import_ref,
                    raw_definition = CAST(EXCLUDED.raw_definition AS jsonb),
                    backend_targets = CAST(EXCLUDED.backend_targets AS jsonb),
                    version = agent_configs.version + 1,
                    updated_at = now()
            """),
            {
                "agent_config_id": agent_config_id,
                "role": role,
                "domain": domain,
                "system_prompt": system_prompt,
                "tools": json.dumps(tools),
                "capabilities": json.dumps(capabilities),
                "execution": json.dumps(execution),
                "import_ref": import_ref,
                "raw_definition": json.dumps(raw_definition),
            },
        )

    logger.debug("Upserted agent_config %s (domain=%s, role=%s)", agent_config_id, domain, role)
    return agent_config_id


# ===================================================================
# 6. MAIN FLOW
# ===================================================================
def main() -> int:
    logger.info("=" * 60)
    logger.info("Conductor Agent Profiles Import — Files 02+03")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Step 0: Connect to DB
    # ------------------------------------------------------------------
    try:
        engine = _get_engine()
        # Quick connectivity check
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection OK")
    except Exception as exc:
        logger.error("Database connection failed: %s", exc)
        return 1

    # ------------------------------------------------------------------
    # Step 1: Load capabilities vocabulary from DB
    # ------------------------------------------------------------------
    db_vocab: list[str] = []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM capabilities ORDER BY name"))
            db_vocab = [row[0] for row in rows]
        logger.info("Loaded %d capabilities from DB", len(db_vocab))
    except Exception as exc:
        logger.warning("Could not load capabilities from DB: %s — using static vocab", exc)
        db_vocab = CAPABILITIES_VOCAB

    # ------------------------------------------------------------------
    # Step 2: Parse all agency-agents .md files
    # ------------------------------------------------------------------
    logger.info("\n--- Parsing agency-agents ---")
    agency_agents = parse_all_agents("agency-agents")
    logger.info("Found %d agency-agents profiles", len(agency_agents))

    # ------------------------------------------------------------------
    # Step 3: Parse all wshobson agent .md files (agents + commands)
    # ------------------------------------------------------------------
    logger.info("\n--- Parsing wshobson-agents ---")
    wshobson_agents = parse_all_agents("wshobson-agents")
    logger.info("Found %d wshobson profiles", len(wshobson_agents))

    # Separate agents from commands
    wshobson_agent_files = [a for a in wshobson_agents if not a["is_command"]]
    wshobson_command_files = [a for a in wshobson_agents if a["is_command"]]
    logger.info(
        "  → %d agents, %d commands",
        len(wshobson_agent_files), len(wshobson_command_files),
    )

    all_agents = agency_agents + wshobson_agent_files + wshobson_command_files

    # ------------------------------------------------------------------
    # Step 4+5: Classify capabilities + upsert to DB (interleaved per batch)
    # ------------------------------------------------------------------
    logger.info("\n--- Classifying capabilities & upserting (per batch) ---")
    collision_count = 0
    upserted_count = 0
    skipped_configs = 0

    def _flush_batch(batch: list[dict[str, Any]], _caps: list[list[str]]) -> None:
        nonlocal collision_count, upserted_count, skipped_configs
        for agent in batch:
            raw_name = agent.get("raw_name", "")
            slug = _slugify(raw_name)
            if slug.lower() in OMO_RESERVED:
                collision_count += 1
            result = upsert_agent_config(agent, engine)
            if result:
                upserted_count += 1
                if result != slug:
                    collision_count += 1
            else:
                skipped_configs += 1

    if all_agents:
        batch_classify(all_agents, db_vocab, batch_size=10, label="agents", on_batch=_flush_batch)
    else:
        logger.warning("No agents to classify!")

    logger.info(
        "Upserted %d agent configs (%d collisions, %d skipped)",
        upserted_count, collision_count, skipped_configs,
    )

    # ------------------------------------------------------------------
    # Step 6: Parse awesome-agent-skills catalog
    # ------------------------------------------------------------------
    logger.info("\n--- Parsing awesome-agent-skills catalog ---")
    catalog = parse_skills_catalog()

    # Upsert skills from catalog as a registry entry
    if catalog["count"] > 0:
        # Create a single registry record for the catalog
        registry_skill = {
            "skill_id": "awesome-agent-skills-catalog",
            "name": "Awesome Agent Skills Catalog",
            "description": (
                f"A curated catalog of {catalog['count']} agent skills from "
                f"the VoltAgent awesome-agent-skills repository. "
                f"Sources include Anthropic, Google, Vercel, Stripe, and the community."
            ),
            "body": json.dumps(catalog["links"], indent=2),
            "tools": ["read_web"],
            "source": "catalog",
            "import_ref": "awesome-agent-skills:README.md",
        }
        upsert_skill(registry_skill, engine)

        # Link to 'generic' capability
        upsert_capability_skill("generic", "awesome-agent-skills-catalog", engine)

    # ------------------------------------------------------------------
    # Step 7: Summary
    # ------------------------------------------------------------------
    logger.info("\n" + "=" * 60)
    logger.info("IMPORT SUMMARY")
    logger.info("=" * 60)
    logger.info("  agency-agents profiles:     %d", len(agency_agents))
    logger.info("  wshobson agent files:       %d", len(wshobson_agent_files))
    logger.info("  wshobson command files:     %d", len(wshobson_command_files))
    logger.info("  awesome-agent-skills links: %d", catalog["count"])
    logger.info("  ─────────────────────────────────────")
    logger.info("  Agent configs upserted:     %d", upserted_count)
    logger.info("  Collisions (reserved/dup):  %d", collision_count)
    logger.info("  Skipped (errors):           %d", skipped_configs)
    logger.info("  ─────────────────────────────────────")
    logger.info("  DB capabilities loaded:     %d", len(db_vocab))
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Import agent profiles into Conductor DB")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only first N agents per repo (for testing)")
    args = parser.parse_args()
    if args.limit:
        # Monkey-patch parse_all_agents to slice results
        _orig_parse = parse_all_agents
        def _limited_parse(repo_label: str) -> list[dict[str, Any]]:
            agents = _orig_parse(repo_label)
            return agents[:args.limit]
        import import_profiles  # noqa
        import_profiles.parse_all_agents = _limited_parse  # type: ignore
        globals()["parse_all_agents"] = _limited_parse
        logger.info("LIMITED MODE: processing only %d agents per repo", args.limit)
    sys.exit(main())
