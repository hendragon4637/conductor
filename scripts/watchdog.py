#!/usr/bin/env python
"""Cron wrapper for the stuck-trace watchdog."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.services.watchdog import sweep
print(sweep())
