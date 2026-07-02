"""Planner service test configuration — fixtures for BYO-DAG tests."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RABBIT_URL", "amqp://test:test@localhost:5672/test")
os.environ.setdefault("SERVICE", "planner")
os.environ.setdefault("ENV", "test")


@pytest.fixture(scope="session", autouse=True)
def _patch_lifespan():
    import services.planner.main as planner_module

    bus_patcher = patch.object(planner_module, "bus", MagicMock())
    bus_patcher.start()
    init_patcher = patch.object(planner_module, "init_db")
    init_patcher.start()

    yield

    init_patcher.stop()
    bus_patcher.stop()


@pytest.fixture(scope="session")
def app(_patch_lifespan):
    from services.planner.main import app as planner_app

    return planner_app


@pytest.fixture(scope="session")
def client(app):
    return TestClient(app)

@pytest.fixture(scope="session")
def client_raw(app):
    """TestClient with raise_server_exceptions=False for error-path tests."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_gate_plan():
    from backend.evaluator.plan_evaluator import PlanGateDecision

    with patch("backend.evaluator.plan_evaluator.gate_plan") as mock:
        mock.return_value = PlanGateDecision(
            action="ratify",
            plan_goal_review=0.85,
            l2_judgments=[],
        )
        yield mock


@pytest.fixture
def mock_save_plan():
    with patch("backend.planning.store.save_plan") as mock:
        yield mock


@pytest.fixture
def mock_checkgen():
    with patch("backend.planning.capability.checkgen.generate_capability_checks") as mock:
        mock.return_value = []
        yield mock
