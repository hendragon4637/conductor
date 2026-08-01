"""Pure data-cleaning transforms."""

from __future__ import annotations


def drop_invalid(rows: list[dict], min_qty: int = 1) -> list[dict]:
    return [r for r in rows if float(r["quantity"]) >= min_qty and float(r["price"]) > 0]
