"""End-to-end: sample.csv -> pipeline.run() -> assert output artifact."""

from __future__ import annotations

from pathlib import Path

from __PKG__.config import Config
from __PKG__.pipeline import run

ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_e2e(tmp_path) -> None:
    out = tmp_path / "out.csv"
    assert run(Config(input_path=str(ROOT / "data/input/sample.csv"), output_path=str(out))) == 0
    content = out.read_text(encoding="utf-8")
    assert content.startswith("sku,quantity,price")
    assert "A-100" in content
    assert "BROKEN" not in content
