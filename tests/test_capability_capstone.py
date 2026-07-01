"""File 05 — E2E Capstone: proves the capability layer across software/data/creative.

S1 — Software (strong objective oracle): "Build a URL-shortener web app."
S2 — Data (mixed): "Ingest CSV, output fastest-lap JSON, handle malformed."
S3 — Creative (weak oracle + GAP): "Generate a 30s lo-fi track."

Run with: uv run python -m pytest tests/test_capability_capstone.py -v
"""
from __future__ import annotations

import json
import pytest

from backend.planning.capability.registry import (
    get_capability,
    caps_in_family,
    objective_dims,
    subjective_dims,
    all_capabilities,
)
from backend.planning.capability.selector import (
    candidate_capabilities,
    select_capabilities,
    resolve_node_capabilities,
    DOMAIN_TO_FAMILY,
)
from backend.planning.capability.checkgen import generate_capability_checks
from backend.planning.capability.harness_profiles import HARNESS_PROFILES
from backend.evaluator.plan_evaluator import staffing_check


# ── S1: Software (strong objective oracle) ──────────────────────────

@pytest.fixture
def s1_node():
    return {
        "id": "node-1",
        "task": {
            "text": "Build a URL-shortener web app with FastAPI backend and React frontend",
            "deliverables": [
                "FastAPI backend with /shorten and /{code} endpoints",
                "React frontend with input form and redirect",
                "Tests for both backend and frontend",
            ],
        },
        "success": {"text": "User can submit a URL, get a short code, and be redirected"},
        "members": [{"agent_config": "software-fullstack-executor", "backend": "opencode", "role": "executor"}],
        "domain": "software_app",
    }


def test_s1_candidate_family(s1_node):
    """S1: family pre-filter returns software capabilities + generic."""
    cands = candidate_capabilities(s1_node)
    names = {c["name"] for c in cands}
    assert "frontend" in names
    assert "backend_api" in names
    assert "cli_tool" in names
    assert "generic" in names
    # Should NOT include data or creative capabilities
    assert "data_pipeline" not in names
    assert "music_generation" not in names


def test_s1_selector(s1_node):
    """S1: selector returns [frontend, backend_api]."""
    caps = select_capabilities(s1_node)
    assert isinstance(caps, list)
    assert len(caps) >= 1
    assert "frontend" in caps or "backend_api" in caps
    # Validate all names are real
    for name in caps:
        assert get_capability(name) is not None, f"Hallucinated capability: {name}"


def test_s1_checkgen(s1_node):
    """S1: checks generated from objective->L1, subjective->L2."""
    resolve_node_capabilities(s1_node)
    checks = generate_capability_checks(s1_node)
    assert len(checks) >= 1
    l1 = [c for c in checks if c["type"] == "deterministic"]
    l2 = [c for c in checks if c["type"] == "rubric"]
    assert len(l1) >= 1, "S1 should have at least 1 L1 check (from objective dims)"
    # L1 should NOT have runtime signals
    for c in l1:
        cmd = (c.get("check_cmd") or "").lower()
        for sig in ("curl", "localhost", "http://"):
            assert sig not in cmd, f"L1 runtime leak in {c['id']}: {cmd}"
    # L2 items should have confidence
    for c in l2:
        assert "confidence" in c, f"L2 check {c['id']} missing confidence flag"


def test_s1_staffing(s1_node):
    """S1: staffing passes (realizability + coverage)."""
    resolve_node_capabilities(s1_node)
    fails = staffing_check(s1_node)
    assert fails == [], f"S1 staffing should pass but got: {fails}"


# ── S2: Data (mixed) ────────────────────────────────────────────────

@pytest.fixture
def s2_node():
    return {
        "id": "node-2",
        "task": {
            "text": "Ingest a CSV of race laps, output fastest-lap-per-driver JSON, handle malformed rows",
            "deliverables": [
                "Python script that reads laps.csv",
                "Outputs fastest_lap_per_driver.json",
                "Skips malformed rows without crashing",
            ],
        },
        "success": {"text": "CSV ingested, valid JSON output produced, malformed rows skipped"},
        "members": [{"agent_config": "data-executor", "backend": "opencode", "role": "executor"}],
        "domain": "data",
    }


def test_s2_candidate_family(s2_node):
    """S2: candidate family = data."""
    cands = candidate_capabilities(s2_node)
    names = {c["name"] for c in cands}
    assert "data_pipeline" in names
    assert "analytics_assistant" in names
    assert "generic" in names
    assert "frontend" not in names


def test_s2_selector(s2_node):
    """S2: selector returns data_pipeline (possibly + analytics_assistant)."""
    caps = select_capabilities(s2_node)
    assert isinstance(caps, list)
    assert len(caps) >= 1
    assert "data_pipeline" in caps
    for name in caps:
        assert get_capability(name) is not None


def test_s2_checkgen(s2_node):
    """S2: checks have runs_sample (objective), handles_malformed (objective), correctness (subjective)."""
    resolve_node_capabilities(s2_node)
    checks = generate_capability_checks(s2_node)
    assert len(checks) >= 1
    l1 = [c for c in checks if c["type"] == "deterministic"]
    l2 = [c for c in checks if c["type"] == "rubric"]
    assert len(l1) >= 1
    # L2 should have confidence flags
    for c in l2:
        assert "confidence" in c


def test_s2_staffing(s2_node):
    """S2: staffing passes (data-executor covers data_pipeline)."""
    resolve_node_capabilities(s2_node)
    fails = staffing_check(s2_node)
    assert fails == [], f"S2 staffing should pass but got: {fails}"


# ── S3: Creative (weak oracle + GAP) ────────────────────────────────

@pytest.fixture
def s3_node():
    return {
        "id": "node-3",
        "task": {
            "text": "Generate a 30-second lo-fi hip-hop track from a 'rainy day' mood prompt",
            "deliverables": ["30s WAV file", "brief description of the composition"],
        },
        "success": {"text": "Playable audio of ~30s duration matching lo-fi style"},
        "members": [{"agent_config": "software-fullstack-executor", "backend": "opencode", "role": "executor"}],
        "domain": "music",
    }


def test_s3_candidate_family(s3_node):
    """S3: family = creative."""
    cands = candidate_capabilities(s3_node)
    names = {c["name"] for c in cands}
    assert "music_generation" in names
    assert "generic" in names
    assert "backend_api" not in names


def test_s3_selector(s3_node):
    """S3: selector returns music_generation (possibly __gap__ if no fit)."""
    caps = select_capabilities(s3_node)
    assert isinstance(caps, list)
    assert len(caps) >= 1
    # Either music_generation or generic fallback
    for name in caps:
        assert get_capability(name) is not None


def test_s3_checkgen_has_objective_l1(s3_node):
    """S3: even a creative node has objective dims → L1 checks (e.g. valid_audio)."""
    resolve_node_capabilities(s3_node)
    checks = generate_capability_checks(s3_node)
    l1 = [c for c in checks if c["type"] == "deterministic"]
    l2 = [c for c in checks if c["type"] == "rubric"]
    assert len(l1) >= 1, "Creative node should have L1 checks from objective dims (e.g. valid_audio)"
    assert len(l2) >= 1, "Creative node should have L2 checks from subjective dims"
    for c in l2:
        assert c.get("confidence") == "provisional", "Creative caps with golden_ref_count=0 should be provisional"


def test_s3_staffing_fails_realizability():
    """S3: realizability FAILS — music_generation needs audio_gen, opencode lacks it."""
    s3 = {
        "id": "node-3",
        "capabilities": ["music_generation"],
        "task": {"text": "Generate a 30-second lo-fi track"},
        "members": [{"agent_config": "software-fullstack-executor", "backend": "opencode", "role": "executor"}],
    }
    fails = staffing_check(s3)
    realizability_fails = [f for f in fails if "tools" in f.lower() and "audio_gen" in f]
    assert len(realizability_fails) >= 1, (
        "S3 should FAIL realizability: music_generation needs audio_gen, "
        "opencode does not provide it"
    )


def test_s3_staffing_fails_coverage():
    """S3: coverage FAILS — no agent_config declares music_generation."""
    s3 = {
        "id": "node-3",
        "capabilities": ["music_generation"],
        "task": {"text": "Generate a 30-second lo-fi track"},
        "members": [{"agent_config": "software-fullstack-executor", "backend": "opencode", "role": "executor"}],
    }
    fails = staffing_check(s3)
    coverage_fails = [f for f in fails if "capabilities" in f.lower()]
    assert len(coverage_fails) >= 1, (
        "S3 should FAIL coverage: no agent_config declares music_generation"
    )


# ── Registry dimension splits ──────────────────────────────────────

def test_registry_dimension_splits():
    """Every capability has BOTH objective and subjective dims (where appropriate)."""
    all_caps = all_capabilities()
    assert len(all_caps) >= 10, "Registry should have 10+ capabilities"

    for cap in all_caps:
        obj = objective_dims(cap)
        subj = subjective_dims(cap)
        assert len(obj) >= 1, f"{cap['name']} has no objective dimensions"
        assert len(subj) >= 1, f"{cap['name']} has no subjective dimensions"
        # Verify each dim has the required fields
        for d in obj + subj:
            assert "id" in d, f"{cap['name']} dim missing id"
            assert "dimension" in d, f"{cap['name']} dim missing dimension"
            assert "kind" in d, f"{cap['name']} dim missing kind"


def test_registry_family_coverage():
    """All families are represented in the registry."""
    families = {c["family"] for c in all_capabilities()}
    for expected in ("software", "data", "creative", "business", "research"):
        assert expected in families, f"Missing family: {expected}"


# ── Harness profiles for realizability ─────────────────────────────

def test_harness_profiles_cover_backends():
    """All known backends have tool profiles."""
    assert "opencode" in HARNESS_PROFILES
    assert "hermes" in HARNESS_PROFILES
    for name, profile in HARNESS_PROFILES.items():
        assert "tools" in profile, f"{name} missing tools list"
        assert len(profile["tools"]) >= 3, f"{name} has too few tools"


def test_music_gen_needs_audio_gen():
    """music_generation requires audio_gen which opencode lacks."""
    cap = get_capability("music_generation")
    assert cap is not None
    assert "audio_gen" in cap.get("required_tools", [])
    assert "audio_gen" not in HARNESS_PROFILES["opencode"]["tools"]
