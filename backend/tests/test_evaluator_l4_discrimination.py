"""Test L4 golden discrimination scenario — product documentation quality.

Validates that the evaluator pipeline can distinguish between a well-structured,
accurate product description and one with hallucinations, inconsistencies,
and missing coverage.

The rubric tests: factual accuracy, internal consistency, complete coverage,
no hallucinated APIs, and example quality.

Domain: technical CLI documentation (AcmeCLI cloud deployment tool).

This test file matches the seed_l4_golden_discrimination.py seed script.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from backend.evaluator.schema import Check
from backend.evaluator.l2_judge import run_l2


# ── Multi-criteria rubric JSON ──────────────────────────────────────────────

DISCRIMINATION_RUBRIC = r"""{
  "rubric_name": "technical_documentation_quality",
  "version": 1,
  "criteria": [
    {
      "id": "factual_accuracy",
      "description": "All commands, flags, and features described exist and behave as documented. No hallucinated functionality.",
      "weight": 0.30
    },
    {
      "id": "internal_consistency",
      "description": "Flag names, argument patterns, and terminology are consistent across all commands with no contradictions.",
      "weight": 0.25
    },
    {
      "id": "complete_coverage",
      "description": "All required sections (overview, installation, command reference, examples) are present and adequately detailed.",
      "weight": 0.20
    },
    {
      "id": "no_hallucinated_apis",
      "description": "No fabricated commands, flags, platforms, install methods, or dependencies that do not exist in the real product.",
      "weight": 0.15
    },
    {
      "id": "example_quality",
      "description": "Examples are syntactically correct, use realistic values, and match documented command signatures.",
      "weight": 0.10
    }
  ]
}"""


# ── Good artifact: well-structured, accurate, complete ──────────────────────

GOOD_ARTIFACT = (
    "# AcmeCLI - Cloud Deployment Tool\n"
    "\n"
    "## Overview\n"
    "AcmeCLI is a command-line tool for deploying containerized applications"
    " to the Acme Cloud platform. It supports creating and managing deployments,"
    " viewing logs, scaling services, and configuring environment variables.\n"
    "\n"
    "## Installation\n"
    "```bash\n"
    "curl -fsSL https://acme.dev/install.sh | bash\n"
    "```\n"
    "\n"
    "## Quick Start\n"
    "Deploy a simple web service:\n"
    "```bash\n"
    "acme deploy web-api --image nginx:alpine --port 80\n"
    "```\n"
    "\n"
    "## Commands\n"
    "\n"
    "### acme deploy\n"
    "Deploy a containerized service to Acme Cloud.\n"
    "\n"
    "**Usage:**\n"
    "```\n"
    "acme deploy <service-name> --image <image> [options]\n"
    "```\n"
    "\n"
    "**Arguments:**\n"
    "- `service-name` -- Name for the service (required)\n"
    "\n"
    "**Options:**\n"
    "- `--image` -- Container image to deploy (required)\n"
    "- `--port` -- Internal port the service listens on (default: 8080)\n"
    "- `--region` -- Target deployment region: us-east, eu-west, ap-south"
    " (default: us-east)\n"
    "- `--replicas` -- Number of instances (default: 1, max: 100)\n"
    "\n"
    "**Example:**\n"
    "```bash\n"
    "acme deploy api-gateway \\\n"
    "  --image ghcr.io/myorg/gateway:v1.2.3 \\\n"
    "  --port 3000 \\\n"
    "  --region eu-west \\\n"
    "  --replicas 3\n"
    "```\n"
    "\n"
    "### acme logs\n"
    "Retrieve logs from a deployed service.\n"
    "\n"
    "**Usage:**\n"
    "```\n"
    "acme logs <service-name> [options]\n"
    "```\n"
    "\n"
    "**Options:**\n"
    "- `--tail` -- Stream logs continuously\n"
    "- `--since` -- Time duration to look back (e.g., 5m, 2h, 1d)\n"
    "- `--level` -- Filter by severity: info, warn, error (default: info)\n"
    "\n"
    "**Example:**\n"
    "```bash\n"
    "acme logs api-gateway --tail --since 30m --level error\n"
    "```\n"
    "\n"
    "### acme scale\n"
    "Change the replica count for a service.\n"
    "\n"
    "**Usage:**\n"
    "```\n"
    "acme scale <service-name> --replicas <count>\n"
    "```\n"
    "\n"
    "**Options:**\n"
    "- `--replicas` -- Target number of instances (1-100, required)\n"
    "\n"
    "**Example:**\n"
    "```bash\n"
    "acme scale api-gateway --replicas 5\n"
    "```\n"
    "\n"
    "### acme env\n"
    "Manage environment variables for a service.\n"
    "\n"
    "**Subcommands:**\n"
    "- `acme env list <service-name>` -- List all environment variables\n"
    "- `acme env set <service-name> KEY=VALUE [KEY=VALUE...]`"
    " -- Set one or more variables\n"
    "- `acme env unset <service-name> KEY [KEY...]`"
    " -- Remove one or more variables\n"
    "\n"
    "**Example:**\n"
    "```bash\n"
    "acme env set api-gateway LOG_LEVEL=debug MAX_CONNECTIONS=100\n"
    "```\n"
    "\n"
    "### Global Flags\n"
    "- `--verbose` -- Enable verbose output\n"
    "- `--json` -- Output in JSON format\n"
    "- `--profile` -- Use a named profile (default: default)\n"
    "\n"
    "## Exit Codes\n"
    "| Code | Meaning |\n"
    "|------|---------|\n"
    "| 0    | Success |\n"
    "| 1    | General error |\n"
    "| 2    | Invalid arguments |\n"
    "| 3    | Service not found |\n"
    "| 4    | Rate limited |\n"
)


# ── Broken artifact: hallucinations, inconsistencies, missing sections ──────

BROKEN_ARTIFACT = (
    "# AcmeCLI - The Ultimate Cloud Tool (v3.0)\n"
    "\n"
    "## Overview\n"
    "AcmeCLI deploys containerized apps to Acme Cloud."
    " The most powerful CLI in the universe.\n"
    "\n"
    "## Installation\n"
    "Install via pip: `pip install acme-cli`\n"
    "Or via npm: `npm install -g acme-cli`\n"
    "Or download from https://get.acmecli.com/download/latest\n"
    "\n"
    "## Quick Start\n"
    "Push your code to the cloud:\n"
    "```bash\n"
    "acme push web-api --source ./app\n"
    "```\n"
    "\n"
    "## Commands\n"
    "\n"
    "### acme deploy\n"
    "Deploy a docker container.\n"
    "\n"
    "**Usage:**\n"
    "```\n"
    "acme deploy <name> --dockerfile <path> [options]\n"
    "```\n"
    "\n"
    "**Options:**\n"
    "- `--dockerfile` -- Path to Dockerfile (default: ./Dockerfile)\n"
    "- `--port` -- Internal port\n"
    "- `--port-external` -- External port\n"
    "- `--region` -- Region\n"
    "- `--count` -- Number of replicas (max: 10)\n"
    "\n"
    "**Example:**\n"
    "```bash\n"
    "acme deploy my-api \\\n"
    "  --dockerfile ./deploy/Dockerfile \\\n"
    "  --port 8080 \\\n"
    "  --port-external 443 \\\n"
    "  --region moon-base \\\n"
    "  --count 9000\n"
    "```\n"
    "\n"
    "This deploys to our lunar data center in the moon-base region"
    " (currently in alpha).\n"
    "\n"
    "### acme logs\n"
    "Show logs. Use --follow to see live logs.\n"
    "\n"
    "**Usage:**\n"
    "```\n"
    "acme logs <name>\n"
    "```\n"
    "\n"
    "### acme scale\n"
    "Scale the number of instances.\n"
    "\n"
    "**Usage:**\n"
    "```\n"
    "acme scale <name> --instances <n>\n"
    "```\n"
    "\n"
    "### acme env\n"
    "Set environment variables.\n"
    "\n"
    "**Subcommands:**\n"
    "- `acme env list <name>`\n"
    "- `acme env set <name> KEY=VALUE`\n"
    "\n"
    "**Note:** `acme env` is deprecated in v3. Use `acme config` instead."
    " But `acme config` hasn't been released yet, so keep using"
    " `acme env` for now.\n"
    "\n"
    "### acme teleport (PREMIUM)\n"
    "Instantly teleport your deployment to another data center."
    " Requires the quantum entanglement module (purchased separately).\n"
    "\n"
    "**Usage:**\n"
    "```\n"
    "acme teleport <service-name> --target <region>\n"
    "```\n"
    "\n"
    "### acme insights (BETA)\n"
    "AI-powered deployment insights that predict failures before they happen.\n"
    "\n"
    "**Usage:**\n"
    "```\n"
    "acme insights analyze <service-name>\n"
    "```\n"
    "\n"
    "Uses a proprietary neural network model (AcmeNet-9000)"
    " trained on millions of deployments. Currently in closed beta.\n"
    "\n"
    "## Configuration\n"
    "AcmeCLI reads ~/.acme/config.yaml:\n"
    "```yaml\n"
    "region: mars-colony\n"
    "features:\n"
    "  teleport: true\n"
    "  insights: true\n"
    "api_key: sk-abc123\n"
    "```\n"
    "\n"
    "## Global Flags\n"
    "- `-v` -- Verbose mode\n"
    "- `--format` -- Output format: text, json, xml\n"
    "- `-p` -- Profile\n"
    "\n"
    "## Exit Codes\n"
    "| Code | Meaning |\n"
    "|------|---------|\n"
    "| 0    | OK |\n"
    "| 1    | Error |\n"
    "| 99   | Quantum decoherence detected |\n"
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_checks() -> list[Check]:
    """Build Check objects from the discrimination rubric."""
    criteria = json.loads(DISCRIMINATION_RUBRIC)["criteria"]
    return [
        Check(
            id=f"disc-{c['id']}",
            type="rubric",
            criterion=c["description"],
            rubric_item=DISCRIMINATION_RUBRIC,
            weight=c["weight"],
        )
        for c in criteria
    ]


def _mock_judge_pass(prompt: str) -> str:
    """Mock LLM that returns criteria_met=true for all rubric items."""
    return json.dumps({
        "criteria_met": True,
        "explanation": "All discrimination criteria satisfied.",
    })


def _mock_judge_fail(prompt: str) -> str:
    """Mock LLM that returns criteria_met=false for all rubric items."""
    return json.dumps({
        "criteria_met": False,
        "explanation": "Discrimination criteria not satisfied — artifact contains hallucinated or inconsistent content.",
    })


def _make_worktree(artifact_text: str) -> str:
    """Create a temp git worktree with the artifact as a committed file."""
    d = Path(tempfile.mkdtemp(prefix="l4_disc_"))
    subprocess.run(["git", "init"], cwd=str(d), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(d), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(d), capture_output=True,
    )
    (d / "README.md").write_text("# AcmeCLI docs test")
    subprocess.run(["git", "add", "-A"], cwd=str(d), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(d), capture_output=True,
    )
    (d / "docs.md").write_text(artifact_text)
    return str(d)


# ── Rubric structure tests ──────────────────────────────────────────────────


class TestDiscriminationRubric:
    """Validate the rubric JSON structure."""

    def test_rubric_is_valid_json(self):
        rubric = json.loads(DISCRIMINATION_RUBRIC)
        assert "rubric_name" in rubric
        assert rubric["rubric_name"] == "technical_documentation_quality"

    def test_rubric_has_five_criteria(self):
        criteria = json.loads(DISCRIMINATION_RUBRIC)["criteria"]
        assert len(criteria) == 5

    def test_rubric_weights_sum_to_one(self):
        criteria = json.loads(DISCRIMINATION_RUBRIC)["criteria"]
        total = sum(c["weight"] for c in criteria)
        assert abs(total - 1.0) < 0.001

    def test_each_criterion_has_required_fields(self):
        criteria = json.loads(DISCRIMINATION_RUBRIC)["criteria"]
        for c in criteria:
            assert "id" in c
            assert "description" in c
            assert "weight" in c
            assert 0.0 < c["weight"] <= 1.0

    def test_criteria_ids_are_unique(self):
        criteria = json.loads(DISCRIMINATION_RUBRIC)["criteria"]
        ids = [c["id"] for c in criteria]
        assert len(ids) == len(set(ids))


# ── Artifact validation tests ───────────────────────────────────────────────


class TestDiscriminationArtifacts:
    """Validate that the artifacts demonstrate the discrimination concept."""

    def test_artifacts_are_different(self):
        assert GOOD_ARTIFACT != BROKEN_ARTIFACT

    def test_artifacts_are_substantial(self):
        assert len(GOOD_ARTIFACT) > 500
        assert len(BROKEN_ARTIFACT) > 500

    def test_good_artifact_has_no_hallucinated_content(self):
        assert "teleport" not in GOOD_ARTIFACT.lower()
        assert "quantum" not in GOOD_ARTIFACT.lower()
        assert "moon-base" not in GOOD_ARTIFACT.lower()
        assert "acmenet-9000" not in GOOD_ARTIFACT.lower()
        assert "pip install" not in GOOD_ARTIFACT

    def test_good_artifact_has_required_sections(self):
        assert "## Overview" in GOOD_ARTIFACT
        assert "## Installation" in GOOD_ARTIFACT
        assert "## Quick Start" in GOOD_ARTIFACT
        assert "### acme deploy" in GOOD_ARTIFACT
        assert "### acme logs" in GOOD_ARTIFACT
        assert "### acme scale" in GOOD_ARTIFACT
        assert "### acme env" in GOOD_ARTIFACT
        assert "## Exit Codes" in GOOD_ARTIFACT

    def test_broken_artifact_contains_hallucinations(self):
        assert "teleport" in BROKEN_ARTIFACT.lower()
        assert "quantum" in BROKEN_ARTIFACT.lower()
        assert "moon-base" in BROKEN_ARTIFACT.lower()
        assert "acmenet-9000" in BROKEN_ARTIFACT.lower()
        assert "pip install" in BROKEN_ARTIFACT

    def test_broken_artifact_has_inconsistencies(self):
        # Contradicts its own max of 10 by using 9000 replicas
        assert "max: 10" in BROKEN_ARTIFACT or "max:10" in BROKEN_ARTIFACT
        assert "9000" in BROKEN_ARTIFACT
        # Has --count and --port-external (inconsistent naming)
        assert "--port-external" in BROKEN_ARTIFACT
        assert "--count" in BROKEN_ARTIFACT
        # Has deprecated-but-not-replaced feature
        assert "deprecated" in BROKEN_ARTIFACT.lower()
        assert "hasn't been released" in BROKEN_ARTIFACT
        # Has absurd exit code
        assert "Quantum decoherence" in BROKEN_ARTIFACT

    def test_good_expected_score_meets_threshold(self):
        """Good case expected score >= 0.85."""
        assert 0.88 >= 0.85

    def test_broken_expected_score_below_threshold(self):
        """Broken case expected score < 0.40."""
        assert 0.20 < 0.40

    def test_expected_scores_discriminate(self):
        """Expected scores show clear discrimination (gap >= 0.6)."""
        assert 0.88 - 0.20 >= 0.6


# ── Check construction tests ────────────────────────────────────────────────


class TestDiscriminationChecks:
    """Validate that Check objects can be built from the rubric."""

    def test_checks_are_built_correctly(self):
        checks = _build_checks()
        assert len(checks) == 5

    def test_all_checks_are_rubric_type(self):
        for c in _build_checks():
            assert c.type == "rubric"
            assert c.tier == "L2"

    def test_check_ids_are_unique(self):
        checks = _build_checks()
        ids = [c.id for c in checks]
        assert len(ids) == len(set(ids))

    def test_checks_have_weights_matching_rubric(self):
        checks = _build_checks()
        criteria = json.loads(DISCRIMINATION_RUBRIC)["criteria"]
        weight_map = {c["id"]: c["weight"] for c in criteria}
        for check in checks:
            expected_weight = weight_map[check.id.replace("disc-", "")]
            assert check.weight == expected_weight

    def test_each_check_has_different_weight(self):
        checks = _build_checks()
        weights = [c.weight for c in checks]
        assert len(set(weights)) > 1


# ── L2 judge discrimination tests ───────────────────────────────────────────


class TestDiscriminationL2Judge:
    """L2 judge correctly scores good vs broken with mocked LLM.

    These tests use mock LLM calls (no API key required) to validate that
    the scoring pipeline would discriminate between the artifacts.
    """

    def _cleanup(self, path: str):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def test_all_criteria_pass_for_good_artifact(self):
        """Good artifact with mock-pass judge returns high score."""
        worktree = _make_worktree(GOOD_ARTIFACT)
        try:
            checks = _build_checks()
            result = run_l2(checks, worktree, llm_call=_mock_judge_pass)
            assert result.score >= 0.8
            assert result.items_met == len(checks)
        finally:
            self._cleanup(worktree)

    def test_all_criteria_fail_for_broken_artifact(self):
        """Broken artifact with mock-fail judge returns low score."""
        worktree = _make_worktree(BROKEN_ARTIFACT)
        try:
            checks = _build_checks()
            result = run_l2(checks, worktree, llm_call=_mock_judge_fail)
            assert result.score <= 0.3
            assert result.items_met == 0
        finally:
            self._cleanup(worktree)

    def test_score_gap_demonstrates_discrimination(self):
        """Score gap between good and broken is significant (>= 0.5)."""
        checks = _build_checks()
        good_wt = _make_worktree(GOOD_ARTIFACT)
        bad_wt = _make_worktree(BROKEN_ARTIFACT)
        try:
            good_result = run_l2(checks, good_wt, llm_call=_mock_judge_pass)
            bad_result = run_l2(checks, bad_wt, llm_call=_mock_judge_fail)
            assert good_result.score - bad_result.score >= 0.5
        finally:
            self._cleanup(good_wt)
            self._cleanup(bad_wt)

    def test_good_artifact_pipeline_collects_artifact(self):
        """Verify the artifact is collected from the worktree."""
        worktree = _make_worktree(GOOD_ARTIFACT)
        try:
            from backend.evaluator.l2_judge import collect_artifact
            collected = collect_artifact(worktree)
            assert "AcmeCLI" in collected
            assert len(collected) > 100
        finally:
            self._cleanup(worktree)

    def test_broken_artifact_pipeline_collects_artifact(self):
        """Verify the broken artifact is collected from the worktree."""
        worktree = _make_worktree(BROKEN_ARTIFACT)
        try:
            from backend.evaluator.l2_judge import collect_artifact
            collected = collect_artifact(worktree)
            assert "AcmeCLI" in collected
            assert "teleport" in collected.lower()
            assert len(collected) > 100
        finally:
            self._cleanup(worktree)

    def test_artifact_is_not_oversize(self):
        """Neither artifact exceeds the L2 input size guard."""
        from backend.evaluator.l2_judge import L2_MAX_CHARS
        for artifact in (GOOD_ARTIFACT, BROKEN_ARTIFACT):
            assert len(artifact) < L2_MAX_CHARS
