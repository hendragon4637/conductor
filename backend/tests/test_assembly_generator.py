"""Tests for assembly generator: service slugs, port resolution, Dockerfile
generation, compose YAML output, gates, and ISR (is_assembly_eligible)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from backend.assembly.generator import (
    _build_compose_yaml,
    _generate_dockerfile,
    _resolve_ports,
    _service_slug,
    check_compose_valid,
    is_assembly_eligible,
)


# ── _service_slug() ──────────────────────────────────────────────────────────

class TestServiceSlug:
    def test_lowercase(self):
        assert _service_slug("MyService") == "myservice"

    def test_replaces_underscores(self):
        assert _service_slug("my_service") == "my-service"

    def test_collapses_hyphens(self):
        assert _service_slug("a---b") == "a-b"

    def test_strips_leading_trailing(self):
        assert _service_slug("-web-") == "web"

    def test_empty_fallback(self):
        assert _service_slug("") == "svc"
        assert _service_slug("   ") == "svc"


# ── _resolve_ports() ─────────────────────────────────────────────────────────

class TestResolvePorts:
    def test_no_collision(self):
        services = [
            {"name": "backend", "template": {"port": 8000}},
            {"name": "frontend", "template": {"port": 5173}},
        ]
        resolved = _resolve_ports(services)
        ports = [s["assigned_host_port"] for s in resolved]
        assert ports == [8000, 5173]

    def test_collision_bump(self):
        services = [
            {"name": "backend", "template": {"port": 8000}},
            {"name": "backend-v2", "template": {"port": 8000}},
        ]
        resolved = _resolve_ports(services)
        ports = [s["assigned_host_port"] for s in resolved]
        assert ports == [8000, 8001]

    def test_missing_port_defaults(self):
        services = [
            {"name": "no-port", "template": {}},
        ]
        resolved = _resolve_ports(services)
        assert resolved[0]["assigned_host_port"] == 8000

    def test_multiple_collisions(self):
        services = [
            {"name": "a", "template": {"port": 3000}},
            {"name": "b", "template": {"port": 3000}},
            {"name": "c", "template": {"port": 3000}},
        ]
        resolved = _resolve_ports(services)
        ports = [s["assigned_host_port"] for s in resolved]
        assert ports == [3000, 3001, 3002]

    def test_deterministic_order(self):
        services = [
            {"name": "z", "template": {"port": 8000}},
            {"name": "a", "template": {"port": 8000}},
        ]
        r1 = _resolve_ports(services)
        r2 = _resolve_ports(services)
        assert r1 == r2


# ── _generate_dockerfile() ───────────────────────────────────────────────────

class TestGenerateDockerfile:
    DEFAULT_PYTHON_TMPL = {
        "image": "python:3.12-slim",
        "port": 8000,
        "build": {"context": ".", "dockerfile": "Dockerfile"},
    }

    def test_python_template(self):
        df = _generate_dockerfile("my-api", self.DEFAULT_PYTHON_TMPL)
        assert "FROM python:3.12-slim" in df
        assert "uvicorn" in df
        assert "8000" in df

    def test_node_template(self):
        tmpl = {
            "image": "node:20-alpine",
            "port": 5173,
            "build": {"context": "."},
        }
        df = _generate_dockerfile("my-frontend", tmpl)
        assert "FROM node:20-alpine" in df
        assert "npm ci" in df
        assert "5173" in df

    def test_fallback_to_python(self):
        tmpl = {"port": 9000}
        df = _generate_dockerfile("unknown", tmpl)
        assert "FROM python:" in df
        assert "9000" in df

    def test_deps_copy_injected(self):
        df = _generate_dockerfile("my-api", self.DEFAULT_PYTHON_TMPL, deps_dir="deps")
        assert "COPY deps deps" in df or "COPY deps" in df


# ── _build_compose_yaml() ────────────────────────────────────────────────────

class TestBuildComposeYaml:
    def test_basic_structure(self):
        services = [
            {
                "slug": "backend",
                "template": {"port": 8000, "image": "python:3.12-slim"},
                "assigned_host_port": 8000,
                "depends_on": [],
            }
        ]
        yaml_str = _build_compose_yaml(services)
        assert "services:" in yaml_str
        assert "backend:" in yaml_str
        assert "8000:8000" in yaml_str

    def test_env_vars(self):
        services = [
            {
                "slug": "api",
                "template": {
                    "port": 8000,
                    "env": {"REQUIRED": ["DATABASE_URL"], "OPTIONAL": ["DEBUG"]},
                },
                "assigned_host_port": 8000,
                "depends_on": [],
            }
        ]
        yaml_str = _build_compose_yaml(services)
        assert "DATABASE_URL" in yaml_str
        assert "DEBUG" in yaml_str

    def test_depends_on(self):
        services = [
            {
                "slug": "frontend",
                "template": {"port": 5173},
                "assigned_host_port": 5173,
                "depends_on": [{"service": "backend", "condition": "service_healthy"}],
            }
        ]
        yaml_str = _build_compose_yaml(services)
        assert "depends_on:" in yaml_str
        assert "backend:" in yaml_str
        assert "service_healthy" in yaml_str

    def test_healthcheck(self):
        services = [
            {
                "slug": "api",
                "template": {
                    "port": 8000,
                    "healthcheck": {
                        "test": ["CMD", "curl", "-f", "http://localhost:8000/health"],
                        "interval": "10s",
                        "retries": 5,
                    },
                },
                "assigned_host_port": 8000,
                "depends_on": [],
            }
        ]
        yaml_str = _build_compose_yaml(services)
        assert "healthcheck:" in yaml_str
        assert "curl" in yaml_str
        assert "retries: 5" in yaml_str

    def test_empty_services(self):
        yaml_str = _build_compose_yaml([])
        assert "services:" in yaml_str

    def test_image_tag_from_image_field(self):
        services = [
            {
                "slug": "api",
                "template": {"port": 8000, "image": "my-registry/api:v1"},
                "assigned_host_port": 8000,
                "depends_on": [],
            }
        ]
        yaml_str = _build_compose_yaml(services)
        assert "my-registry/api:v1" in yaml_str


# ── check_compose_valid() ────────────────────────────────────────────────────

class TestCheckComposeValid:
    def test_valid_yaml(self):
        yaml_str = """
services:
  api:
    image: python:3.12-slim
    ports:
      - "8000:8000"
"""
        assert check_compose_valid(yaml_str) is True

    def test_invalid_yaml(self):
        assert check_compose_valid("not: yaml: broken") is False

    def test_empty_yaml(self):
        assert check_compose_valid("") is False
        assert check_compose_valid(None) is False

    def test_yaml_without_services(self):
        assert check_compose_valid("version: '3.9'") is False


# ── is_assembly_eligible() ───────────────────────────────────────────────────

class TestIsAssemblyEligible:
    def _clear_debounce(self):
        from backend.assembly.generator import _last_assembly_at
        _last_assembly_at.clear()

    def test_eligible_on_first_call(self):
        """Eligible if no prior assembly for this system."""
        self._clear_debounce()
        eligible, reason = is_assembly_eligible("sys-fresh")
        assert eligible is True
        assert reason == "eligible"

    def test_not_eligible_within_cooldown(self):
        """Not eligible if assembly ran within the cooldown period."""
        self._clear_debounce()
        # First call sets the timestamp
        is_assembly_eligible("sys-cooldown")
        # Second call immediately after is debounced
        eligible, reason = is_assembly_eligible("sys-cooldown")
        assert eligible is False
        assert "debounced" in reason.lower()

    def test_different_systems_independent(self):
        """Different systems have independent debounce states."""
        self._clear_debounce()
        is_assembly_eligible("sys-a")  # first call — sets timestamp
        eligible_b, _ = is_assembly_eligible("sys-b")  # different system
        assert eligible_b is True


# ── generate_assembly() (SQL-mocked) ─────────────────────────────────────────

class TestGenerateAssembly:
    @mock.patch("psycopg.connect")
    def test_returns_expected_keys(self, mock_connect):
        """generate_assembly returns compose_yaml, dockerfiles, services, errors, env_*."""
        # Mock a system with one component project that has a standard template
        mock_cur = mock.MagicMock()

        # fetchall results for: projects, dep_edges
        mock_cur.fetchall.side_effect = [
            # projects
            [
                {"project_id": "sys1-api", "name": "API", "kind": "component",
                 "description": "The API"},
            ],
            # dep_edges
            [],
        ]
        # fetchone results for: service_template, dep_shas
        mock_cur.fetchone.side_effect = [
            # standard row with service_template
            {
                "slug": "python-backend",
                "service_template": json.dumps({
                    "image": "python:3.12-slim",
                    "port": 8000,
                    "env": {"REQUIRED": ["DATABASE_URL"], "OPTIONAL": []},
                    "env_required": ["DATABASE_URL"],
                }),
            },
            # dep_shas
            {"dep_shas": json.dumps({"dep1": "abc123def456"})},
        ]

        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from backend.assembly.generator import generate_assembly
        result = generate_assembly("sys1")

        assert "compose_yaml" in result
        assert "dockerfiles" in result
        assert "services" in result
        assert "errors" in result
        assert "env_required" in result
        assert "env_example" in result

        assert result["env_required"] == ["DATABASE_URL"]
        assert "DATABASE_URL" in result["env_example"]

    @mock.patch("psycopg.connect")
    def test_empty_system(self, mock_connect):
        """Empty system returns error, not a crash."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = []  # no projects

        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from backend.assembly.generator import generate_assembly
        result = generate_assembly("nonexistent")
        assert result["errors"]
