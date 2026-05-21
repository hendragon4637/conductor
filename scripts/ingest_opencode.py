#!/usr/bin/env python
"""Wrapper for cron — just calls the adapter."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.services.opencode_adapter import main
sys.exit(main())
