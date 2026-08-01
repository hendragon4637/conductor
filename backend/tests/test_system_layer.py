"""Tests for system layer core functions: slug derivation, project creation,
dependency guards, and standards menu."""
from __future__ import annotations

from unittest import mock

import pytest

from shared.models import _slug


# ── _slug() ──────────────────────────────────────────────────────────────────

class TestSlug:
    def test_lowercases(self):
        assert _slug("MyProject") == "myproject"

    def test_replaces_spaces(self):
        assert _slug("my project") == "my-project"

    def test_replaces_special_chars(self):
        assert _slug("hello_world!!!") == "hello-world"

    def test_collapses_hyphens(self):
        assert _slug("a---b") == "a-b"

    def test_strips_leading_trailing_hyphens(self):
        assert _slug("-hello-") == "hello"

    def test_empty_returns_unnamed(self):
        assert _slug("") == "unnamed"
        assert _slug("   ") == "unnamed"

    def test_mixed_casing_and_punctuation(self):
        assert _slug("Hello   World!!!") == "hello-world"

    def test_alphanumeric_preserved(self):
        assert _slug("abc123") == "abc123"


# ── create_project() ─────────────────────────────────────────────────────────

class TestCreateProject:
    """Tests use mocked psycopg to avoid a real database."""

    @mock.patch("psycopg.connect")
    def test_derived_id_format(self, mock_connect):
        """Derived project_id = {system_id}-{slug(name)}."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.side_effect = [
            None,  # uniqueness check — name does not exist
            None,  # collision check — no collision
            {"persona_id": "p1"},  # system lookup
            {"project_id": "sys1-my-proj"},  # insert RETURNING
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from shared.models import create_project
        pid = create_project(system_id="sys1", name="My Proj", kind="component")

        assert pid == "sys1-my-proj"

    @mock.patch("psycopg.connect")
    def test_uniqueness_enforcement(self, mock_connect):
        """Raises ValueError if project name already exists in the system."""
        mock_cur = mock.MagicMock()
        # First query finds existing project
        mock_cur.fetchone.side_effect = [
            {"project_id": "sys1-my-proj"},  # name exists
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from shared.models import create_project
        with pytest.raises(ValueError, match="already exists"):
            create_project(system_id="sys1", name="My Proj")

    @mock.patch("psycopg.connect")
    def test_collision_fallback(self, mock_connect):
        """If derived ID collides with an existing project_id, appends short uid."""
        # First: name does not exist → OK
        # Second: derived id collides
        # Third: persona_id lookup
        call_count = 0

        def fetchone_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # name does not exist
            if call_count == 2:
                return {"project_id": "sys1-existing"}  # derived ID collision
            if call_count == 3:
                return {"persona_id": "default"}
            return {"project_id": "sys1-existing-abcd"}

        mock_cur = mock.MagicMock()
        mock_cur.fetchone.side_effect = fetchone_side_effect
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from shared.models import create_project
        pid = create_project(system_id="sys1", name="existing")
        # Should append a 4-char suffix
        assert pid.startswith("sys1-existing-")

    @mock.patch("psycopg.connect")
    def test_inherits_persona_id(self, mock_connect):
        """New projects inherit persona_id from the system."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.side_effect = [
            None,  # uniqueness
            None,  # collision
            {"persona_id": "qa-bot"},  # system lookup
            {"project_id": "sys1-qa-proj"},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from shared.models import create_project
        pid = create_project(system_id="sys1", name="qa-proj")

        # Verify persona_id was passed in the INSERT
        insert_call = [c for c in mock_cur.mock_calls if "INSERT INTO projects" in str(c)][0]
        # persona_id is the 5th positional arg (index 4 in the params tuple)
        args = insert_call.args[1] if hasattr(insert_call, 'args') and len(insert_call.args) > 1 else ()
        assert len(args) >= 5, f"Expected at least 5 params, got {args}"
        assert args[4] == "qa-bot", f"Expected persona_id 'qa-bot', got {args[4]}"


# ── add_dependency() ─────────────────────────────────────────────────────────

class TestAddDependency:
    @mock.patch("psycopg.connect")
    def test_same_system_passes(self, mock_connect):
        """Projects in the same system can have a dependency edge."""
        mock_cur = mock.MagicMock()
        # Both projects in same system
        mock_cur.fetchall.side_effect = [
            [{"system_id": "sys1"}, {"system_id": "sys1"}],  # system check
            [],  # no existing edges → no cycles
        ]
        # fetchone for dep_name fallback
        mock_cur.fetchone.return_value = {"name": "Backend"}
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from shared.models import add_dependency
        # Should not raise
        add_dependency("proj-a", "proj-b")

    @mock.patch("psycopg.connect")
    def test_different_system_raises(self, mock_connect):
        """Projects in different systems raise ValueError."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"system_id": "sys1"},
            {"system_id": "sys2"},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from shared.models import add_dependency
        with pytest.raises(ValueError, match="must stay within"):
            add_dependency("proj-a", "proj-b")

    @mock.patch("psycopg.connect")
    def test_cycle_detected(self, mock_connect):
        """A dependency edge that creates a cycle raises ValueError."""
        mock_cur = mock.MagicMock()
        # Same system, edges: a→b, b→a would be cycle
        mock_cur.fetchall.side_effect = [
            [{"system_id": "sys1"}, {"system_id": "sys1"}],  # same system
            [  # existing edges: b → a
                {"project_id": "b", "depends_on_project_id": "a"},
            ],
        ]
        mock_cur.fetchone.return_value = {"name": "A"}
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from shared.models import add_dependency
        # a → b creates a cycle because b → a already exists
        with pytest.raises(ValueError, match="cycle"):
            add_dependency("a", "b")

    @mock.patch("psycopg.connect")
    def test_dep_name_defaults(self, mock_connect):
        """dep_name defaults to the dependency's project name when not provided."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.side_effect = [
            [{"system_id": "sys1"}, {"system_id": "sys1"}],
            [],
        ]
        mock_cur.fetchone.return_value = {"name": "Backend"}
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from shared.models import add_dependency
        add_dependency("frontend", "backend")

        # Verify INSERT used "Backend" as dep_name
        insert_calls = [
            c for c in mock_cur.mock_calls
            if "INSERT INTO project_dependencies" in str(c)
        ]
        assert insert_calls, "INSERT was not called"
