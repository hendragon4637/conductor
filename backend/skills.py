from __future__ import annotations

import abc
import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

OPENCODE_CONFIG = Path.home() / ".config" / "opencode"
OPENCODE_AGENT_DIR = OPENCODE_CONFIG / "agent"
OPENCODE_SKILL_DIR = OPENCODE_CONFIG / "skills"

OMO_RESERVED: set[str] = {
    "sisyphus", "prometheus", "atlas", "hephaestus",
    "oracle", "librarian", "explore", "build", "plan", "general",
}

ROLE_TO_OPENCODE_MODE: dict[str, str] = {
    "executor": "build",
    "planner": "plan",
    "commander": "ask",
    "reviewer": "ask",
}

TOOL_TO_OPENCODE: dict[str, str] = {
    "read_file": "read",
    "write_file": "write",
    "shell": "bash",
    "browser": "browser",
    "http": "webfetch",
    "read_web": "webfetch",
}


class HarnessRenderer(abc.ABC):
    name: str

    @abc.abstractmethod
    def render_agent(self, row: dict[str, Any], scope: str = "global") -> tuple[str, str]:
        ...

    @abc.abstractmethod
    def render_skill(self, row: dict[str, Any], scope: str = "global") -> tuple[str, str]:
        ...

    @abc.abstractmethod
    def agents_dir(self, scope: str) -> Path:
        ...

    @abc.abstractmethod
    def skills_dir(self, scope: str) -> Path:
        ...

    def ensure_dirs(self, scope: str) -> None:
        self.agents_dir(scope).mkdir(parents=True, exist_ok=True)
        self.skills_dir(scope).mkdir(parents=True, exist_ok=True)


DEFAULT_HARNESS = "opencode"
RENDERERS: dict[str, HarnessRenderer] = {}


def register(renderer: HarnessRenderer) -> None:
    RENDERERS[renderer.name] = renderer


class OpenCodeRenderer(HarnessRenderer):
    name = "opencode"

    def agents_dir(self, scope: str) -> Path:
        if scope == "global":
            return OPENCODE_AGENT_DIR
        return Path(scope) / ".opencode" / "agent"

    def skills_dir(self, scope: str) -> Path:
        if scope == "global":
            return OPENCODE_SKILL_DIR
        return Path(scope) / ".opencode" / "skills"

    def render_agent(self, row: dict[str, Any], scope: str = "global") -> tuple[str, str]:
        agent_id = row["agent_config_id"]
        desc = (row.get("system_prompt") or "")[:120].replace("\n", " ").strip()
        role = row.get("role", "executor")
        mode = ROLE_TO_OPENCODE_MODE.get(role, "build")

        conductor_tools = row.get("tools") or []
        if isinstance(conductor_tools, str):
            conductor_tools = json.loads(conductor_tools)
        opencode_tools = sorted(set(
            TOOL_TO_OPENCODE.get(t, t) for t in conductor_tools
        ))

        frontmatter = {
            "description": desc or (row.get("new_capabilities") or ["imported"])[0],
            "mode": mode,
        }
        if opencode_tools:
            frontmatter["tools"] = opencode_tools

        fm_lines = ["---"]
        for k, v in frontmatter.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}:")
                for item in v:
                    fm_lines.append(f"  - {item}")
            else:
                fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")

        body = row.get("system_prompt") or ""
        content = "\n".join(fm_lines) + "\n\n" + body
        filename = f"{agent_id}.md"
        path = str(self.agents_dir(scope) / filename)
        return path, content

    def render_skill(self, row: dict[str, Any], scope: str = "global") -> tuple[str, str]:
        skill_id = row["skill_id"]
        name = row.get("name", skill_id)
        desc = (row.get("description") or "")[:200]
        body = row.get("body") or ""

        frontmatter = {"name": name, "description": desc}
        fm_lines = ["---"]
        for k, v in frontmatter.items():
            fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")

        content = "\n".join(fm_lines) + "\n\n" + body
        path = str(self.skills_dir(scope) / skill_id / "SKILL.md")
        return path, content


register(OpenCodeRenderer())


class StubRenderer(HarnessRenderer):
    name = "stub"

    def agents_dir(self, scope: str) -> Path:
        return Path("/tmp/stub-harness") / scope / "agent"

    def skills_dir(self, scope: str) -> Path:
        return Path("/tmp/stub-harness") / scope / "skills"

    def render_agent(self, row: dict[str, Any], scope: str = "global") -> tuple[str, str]:
        return ("", "")

    def render_skill(self, row: dict[str, Any], scope: str = "global") -> tuple[str, str]:
        return ("", "")


register(StubRenderer())


def _make_engine(db_url: str = ""):
    url = db_url or os.environ.get("DATABASE_URL", "")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url)


def capability_skills(engine, capability: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT s.skill_id, s.name, s.description, s.body, s.tools
                FROM skills s
                JOIN capability_skills cs ON cs.skill_id = s.skill_id
                WHERE cs.capability = :cap
                ORDER BY s.skill_id
            """),
            {"cap": capability},
        ).mappings()
        return [dict(r) for r in rows]


def skills_for_node(engine, capabilities: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for cap in capabilities:
        for sk in capability_skills(engine, cap):
            if sk["skill_id"] not in seen:
                seen.add(sk["skill_id"])
                result.append(sk)
    return result


def backend_supports(harness: str, skill_row: dict[str, Any]) -> bool:
    if harness == "opencode":
        return True
    return True


# ── Realizability check ─────────────────────────────────────────────

# Tools recognised by OpenCode harness (reverse of OpenCodeRenderer's mapping)
_HARNESS_TOOL_MAP: dict[str, set[str]] = {
    "opencode": {
        "read_file", "write_file", "shell", "browser",
        "http", "webfetch", "read_web", "read_data",
        "sendmessage", "taskget", "tasklist", "taskupdate", "taskcreate",
    },
}


def check_capability_realizability(
    engine,
    capabilities: list[str],
    harness: str = "opencode",
) -> dict[str, list[str]]:
    """For each capability, flag `required_tools` that the harness cannot serve.

    Returns ``{capability_name: [unsupported_tool, ...]}`` for any capability
    that has at least one unsupported tool.  Capabilities with full support
    are omitted from the result.
    """
    supported = _HARNESS_TOOL_MAP.get(harness, set())
    gaps: dict[str, list[str]] = {}

    with engine.connect() as conn:
        for cap in capabilities:
            row = conn.execute(
                text("SELECT required_tools FROM capabilities WHERE name = :cap"),
                {"cap": cap},
            ).mappings().first()
            if not row:
                logger.warning("Unknown capability '%s' — skipping realizability check", cap)
                continue
            required: list[str] = row["required_tools"] or []
            unsupported = [t for t in required if t not in supported]
            if unsupported:
                gaps[cap] = unsupported

    return gaps


def install_global_skills(engine, renderer: HarnessRenderer) -> int:
    renderer.ensure_dirs("global")
    count = 0
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT skill_id, name, description, body, tools FROM skills ORDER BY skill_id")
        ).mappings()
        for row in rows:
            skill_dict = dict(row)
            if not backend_supports(renderer.name, skill_dict):
                continue
            path, content = renderer.render_skill(skill_dict, scope="global")
            if not path:
                continue
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(content)
            count += 1
    logger.info("Installed %d skills to %s global skills dir", count, renderer.name)
    return count


def install_global_agents(engine, renderer: HarnessRenderer) -> int:
    renderer.ensure_dirs("global")
    count = 0
    skipped = 0
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT agent_config_id, role, domain, system_prompt,
                       tools, new_capabilities, source
                FROM agent_configs
                WHERE source = 'imported'
                ORDER BY agent_config_id
            """)
        ).mappings()
        for row in rows:
            agent_dict = dict(row)
            agent_id = agent_dict["agent_config_id"]
            base = agent_id.removeprefix("imp-")
            if base.lower() in OMO_RESERVED:
                skipped += 1
                continue
            path, content = renderer.render_agent(agent_dict, scope="global")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(content)
            count += 1
    logger.info(
        "Installed %d agents to %s global agent dir (%d OMO-reserved skipped)",
        count, renderer.name, skipped,
    )
    return count


def install_worktree_skills(engine, worktree_path: str, node: dict[str, Any]) -> int:
    backend = node.get("backend", "opencode")
    renderer = RENDERERS.get(backend)
    if not renderer:
        logger.warning("No renderer for backend '%s' — skipping worktree skills", backend)
        return 0

    node_caps = node.get("capabilities") or node.get("new_capabilities") or ["generic"]
    if isinstance(node_caps, str):
        node_caps = ["generic"]
    skills = skills_for_node(engine, node_caps)

    count = 0
    for sk in skills:
        path, content = renderer.render_skill(sk, scope=worktree_path)
        if not path:
            continue
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content)
        count += 1

    if count:
        logger.info("Installed %d worktree skills to %s", count, worktree_path)
    return count
