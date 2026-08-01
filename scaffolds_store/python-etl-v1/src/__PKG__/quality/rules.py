"""Row validation rules."""

from __future__ import annotations

REQUIRED_FIELDS = ("sku", "quantity", "price")


def validate_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    ok: list[dict] = []
    rejects: list[dict] = []
    for row in rows:
        if all(f in row and row[f] != "" for f in REQUIRED_FIELDS):
            ok.append(row)
        else:
            rejects.append(row)
    return ok, rejects
