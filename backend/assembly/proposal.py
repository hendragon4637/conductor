from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def propose_project(
    system_id: str,
    project_name: str,
    kind: str = "service",
    intent_text: str | None = None,
    source_ref: str | None = None,
    evidence: list[dict] | None = None,
) -> dict[str, Any] | None:
    """Create a proposed_project intent in the intake_intents table.

    Args:
        system_id: The system this new project belongs to.
        project_name: Human-readable name for the proposed project.
        kind: Project kind (``"service"``, ``"library"``, ``"docs"``, etc.).
        intent_text: Optional goal text.  Auto-generated if omitted.
        source_ref: Optional lineage pointer (e.g. ``"l4:<run_id>"``).
        evidence: Optional list of evidence dicts.

    Returns:
        The inserted row as a dict, or ``None`` on failure.

    The proposal is stored with ``status='proposed'`` so a human (or
    upstream auto-ratify) can call ``POST /intake/intents/{id}/ratify``
    to convert it into a real project.
    """
    import psycopg

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL not set — cannot propose project")
        return None

    if not intent_text:
        intent_text = (
            f"Create a new {kind} project called \"{project_name}\" "
            f"as part of system {system_id}. "
            "The project should implement the functionality described in the "
            "L4 findings that prompted this proposal."
        )

    proposed_project: dict[str, Any] = {
        "project_name": project_name,
        "kind": kind,
        "system_id": system_id,
    }

    intent: dict[str, Any] = {
        "origin": "l4_findings",
        "source_ref": source_ref or "",
        "project_id": system_id,
        "intent_text": intent_text,
        "evidence": json.dumps(evidence or []),
        "proposed_project": json.dumps(proposed_project),
    }

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                # Dedupe by project_name + system_id in proposed_project (File 05.4)
                cur.execute(
                    """SELECT id, status FROM intake_intents
                       WHERE project_id = %s
                         AND proposed_project->>'project_name' = %s
                         AND proposed_project->>'system_id' = %s
                         AND status IN ('proposed', 'submitted', 'awaiting_ratify')
                       LIMIT 1""",
                    (system_id, project_name, system_id),
                )
                existing = cur.fetchone()
                if existing:
                    logger.info(
                        "Duplicate proposal for %s in system %s — "
                        "intent_id=%s status=%s",
                        project_name, system_id, existing[0], existing[1],
                    )
                    return {"id": existing[0], "status": existing[1], "duplicate": True}

                cur.execute(
                    """INSERT INTO intake_intents
                       (origin, source_ref, project_id, intent_text,
                        evidence, proposed_project, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'proposed')
                       RETURNING id, status, created_at""",
                    (
                        intent["origin"],
                        intent["source_ref"],
                        intent["project_id"],
                        intent["intent_text"],
                        intent["evidence"],
                        intent["proposed_project"],
                    ),
                )
                row = cur.fetchone()
            conn.commit()

        if row:
            result = {"id": row[0], "status": row[1], "created_at": str(row[2])}
            logger.info(
                "Proposed project %s (kind=%s) in system %s — intent_id=%s",
                project_name, kind, system_id, row[0],
            )
            return result

    except Exception as exc:
        logger.exception("Failed to propose project %s in system %s", project_name, system_id)

    return None


def propose_project_from_findings(
    system_id: str,
    findings: list[dict],
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Create project proposals from L4 findings.

    Inspects each finding's severity and content to decide whether a
    new project should be proposed.  Currently all findings with severity
    ``"high"`` or ``"medium"`` produce a proposal (one per finding).
    Deduplication is handled by ``propose_project()``.

    Returns a list of result dicts (one per proposal attempt).
    """
    from collections import Counter

    # Group findings to avoid redundant proposals
    seen_names: set[str] = set()
    results: list[dict[str, Any]] = []

    for f in findings:
        if f.get("severity") not in ("high", "medium"):
            continue

        what = f.get("what", "")
        short = what[:60].strip()
        name = _project_name_from_finding(short, seen_names)
        seen_names.add(name)

        result = propose_project(
            system_id=system_id,
            project_name=name,
            kind="service",
            intent_text=(
                f"Address L4 finding: {what}\n\n"
                f"Evidence: {f.get('why', '')}"
            ),
            source_ref=f"l4:{run_id}" if run_id else None,
            evidence=[{
                "finding_what": what,
                "finding_why": f.get("why", ""),
                "severity": f.get("severity"),
                "scenario_id": f.get("scenario_id"),
            }],
        )
        if result:
            results.append(result)

        # Cap at 3 proposals per batch
        if len(results) >= 3:
            break

    return results


def _project_name_from_finding(short: str, seen: set[str]) -> str:
    """Derive a unique project name from a finding's short description."""
    import re

    # Strip punctuation, take first few words
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", short).strip()
    words = [w for w in clean.split() if w.lower() not in ("the", "a", "an", "of", "in", "to")]
    name = " ".join(words[:5]).strip() or "unnamed-fix"

    # Make unique within this batch
    if name in seen:
        idx = 2
        while f"{name}-{idx}" in seen:
            idx += 1
        name = f"{name}-{idx}"

    return name[:80]
