"""Evaluator service test configuration — patch bus, DB, and heavy dependencies."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RABBIT_URL", "amqp://test:test@localhost:5672/test")
os.environ.setdefault("SERVICE", "evaluator")
os.environ.setdefault("ENV", "test")


@pytest.fixture(scope="session", autouse=True)
def _patch_lifespan():
    import services.evaluator.main as eval_module

    bus_patcher = patch.object(eval_module, "bus", MagicMock())
    bus_patcher.start()
    init_patcher = patch.object(eval_module, "init_db")
    init_patcher.start()

    yield

    init_patcher.stop()
    bus_patcher.stop()


@pytest.fixture
def mock_session():
    """Return a MagicMock that stands in for the `s` DB session."""
    return MagicMock()



