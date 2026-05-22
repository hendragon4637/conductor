#!/usr/bin/env python
"""
Scheduler -- fires every cron trigger whose next_fire_at has passed.

Run from cron every minute:
  * * * * * /opt/aipc/conductor/.venv/bin/python /opt/aipc/conductor/scripts/fire_due_triggers.py >> /var/log/aipc/conductor-triggers.log 2>&1
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.services.trigger_service import fire_due


def main():
    results = fire_due()
    if not results:
        return 0
    for r in results:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
