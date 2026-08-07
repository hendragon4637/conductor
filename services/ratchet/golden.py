"""Golden file loader — validates, hashes, and registers golden sets.

Golden sets are FILES, not tables (guide 00: schemas differ per component
and files give git diffs — the audit that makes the ruler trustworthy).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from services.ratchet import registry
from services.ratchet import store


class GoldenItem(BaseModel):
    item_id: str
    split: Literal["calibration", "heldout"]
    input: dict
    expected: dict
    historical: dict = {}
    rationale: str = ""
    exclude_from_scoring: bool = False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_golden(path: Path, component_name: str) -> tuple[list[GoldenItem], str]:
    """Load + validate a golden file against the component's schemas.

    Returns (items, sha256). Raises ValueError on any validation failure —
    a malformed label fails at load, never silently as a wrong score.
    """
    sha = sha256_file(path)
    sha_file = path.with_suffix(".sha256")
    if sha_file.exists():
        recorded = sha_file.read_text().strip().split()[0]
        if recorded != sha:
            raise ValueError(
                f"sha256 mismatch for {path}: file={sha} recorded={recorded}"
            )

    comp = registry.get_component(component_name)
    Input, Expected = comp.schemas

    items: list[GoldenItem] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            item = GoldenItem(**raw)
            Input(**item.input)
            Expected(**item.expected)
            items.append(item)

    return items, sha


def register(path: Path, component_name: str) -> dict[str, Any]:
    """Validate, hash, and register a golden file. Returns the golden record."""
    items, sha = load_golden(path, component_name)
    scorable = [i for i in items if not i.exclude_from_scoring]
    gid = path.stem

    store.register_golden(
        gid=gid,
        component=component_name,
        path=str(path),
        sha256=sha,
        item_count=len(items),
        scorable_count=len(scorable),
        split_rule="md5(item_id) % 10 < 3 -> heldout",
    )
    return {
        "id": gid,
        "component": component_name,
        "sha256": sha,
        "item_count": len(items),
        "scorable_count": len(scorable),
        "calibration": sum(1 for i in items if i.split == "calibration" and not i.exclude_from_scoring),
        "heldout": sum(1 for i in items if i.split == "heldout"),
        "excluded": [i.item_id for i in items if i.exclude_from_scoring],
    }
