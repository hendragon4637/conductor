from __future__ import annotations
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from backend.services.schema_validator import validate


SKILLS_ROOT = Path(os.environ.get("SKILLS_ROOT", "/opt/aipc/conductor/skills"))


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_skills(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or SKILLS_ROOT
    if not root.is_dir():
        return []

    skills: list[dict[str, Any]] = []
    for manifest_path in sorted(root.rglob("skills_manifest.yaml")):
        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            skills.append({
                "manifest_path": str(manifest_path),
                "error": f"YAML parse: {e}",
                "valid": False,
            })
            continue

        errs = validate("skills_manifest", data)
        if errs:
            skills.append({
                "manifest_path": str(manifest_path),
                "skill_id": data.get("skill_id"),
                "error": "; ".join(errs),
                "valid": False,
            })
            continue

        content_path = (manifest_path.parent / data["content_path"]).resolve()
        live_hash = _hash_file(content_path) if content_path.is_file() else None
        stored_hash = data.get("content_hash")

        # Convert any datetime objects to ISO strings for JSON serialization
        clean = {}
        for k, v in data.items():
            if isinstance(v, datetime):
                clean[k] = v.isoformat()
            else:
                clean[k] = v

        skills.append({
            **clean,
            "manifest_path": str(manifest_path),
            "content_path_resolved": str(content_path),
            "live_content_hash": live_hash,
            "hash_matches": (live_hash == stored_hash) if stored_hash else None,
            "valid": True,
        })

    return skills


def update_content_hash(manifest_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    content_path = (manifest_path.parent / data["content_path"]).resolve()
    if not content_path.is_file():
        raise FileNotFoundError(content_path)
    new_hash = _hash_file(content_path)
    data["content_hash"] = new_hash

    out_text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    manifest_path.write_text(out_text, encoding="utf-8")
    return {"manifest_path": str(manifest_path), "content_hash": new_hash}


def find_by_skill_id(skill_id: str) -> dict[str, Any] | None:
    for s in discover_skills():
        if s.get("skill_id") == skill_id:
            return s
    return None


def find_for_agent_config(harness: str, domain: str, role: str) -> dict[str, Any] | None:
    for s in discover_skills():
        if not s.get("valid"):
            continue
        if harness not in (s.get("compatible_harnesses") or []):
            continue
        if domain not in (s.get("compatible_domains") or []):
            continue
        if role not in (s.get("compatible_roles") or []):
            continue
        return s
    return None
