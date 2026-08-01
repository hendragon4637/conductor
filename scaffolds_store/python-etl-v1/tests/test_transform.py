"""Pure transform + quality tests with literal fixtures, no mocks."""

from __PKG__.quality.rules import validate_rows
from __PKG__.transform.clean import drop_invalid


def test_drop_invalid_filters_bad_rows() -> None:
    rows = [
        {"sku": "a", "quantity": "5", "price": "1.0"},
        {"sku": "b", "quantity": "0", "price": "2.0"},
        {"sku": "c", "quantity": "1", "price": "-3.0"},
    ]
    assert len(drop_invalid(rows, min_qty=1)) == 1


def test_validate_rows_splits_ok_and_rejects() -> None:
    ok, rejects = validate_rows(
        [{"sku": "a", "quantity": "1", "price": "2"}, {"sku": "", "quantity": "", "price": ""}]
    )
    assert len(ok) == 1
    assert len(rejects) == 1
