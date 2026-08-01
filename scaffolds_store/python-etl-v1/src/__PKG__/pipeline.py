"""Orchestrates extract -> validate -> transform -> load. The only place they meet."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config
from .extract.csv_source import read_rows
from .load.csv_sink import write_rows
from .quality.rules import validate_rows
from .transform.clean import drop_invalid

logger = logging.getLogger(__name__)


def run(config: Config) -> int:
    raw = read_rows(config.input_path)
    logger.info("extracted %d rows from %s", len(raw), config.input_path)
    ok_rows, rejects = validate_rows(raw)
    if rejects:
        logger.warning("rejected %d rows: %s", len(rejects), [r for r in rejects])
    cleaned = drop_invalid(ok_rows, min_qty=config.min_qty)
    write_rows(cleaned, config.output_path)
    logger.info("wrote %d rows to %s", len(cleaned), config.output_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="__PKG__")
    parser.add_argument("--input", required=True, help="input CSV path")
    parser.add_argument("--output", required=True, help="output CSV path")
    args = parser.parse_args(argv)
    return run(Config.from_env(args.input, args.output))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
