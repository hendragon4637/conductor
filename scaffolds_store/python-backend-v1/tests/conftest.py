import pytest
from fastapi.testclient import TestClient
from __PKG__.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
