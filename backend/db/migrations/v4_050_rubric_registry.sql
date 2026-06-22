-- v4_050_rubric_registry.sql
-- Rubric registry + check_templates for retrievable/auditable/L3-tunable rubrics.
-- Rubrics are the PATTERNS (menu); instantiated checks live on plans.dag[].checks.
-- Migration: Conductor meta-planner File 04.

CREATE TABLE IF NOT EXISTS rubrics (
    name        TEXT PRIMARY KEY,
    applies_to  JSONB NOT NULL,           -- e.g. ["build","executor","api"]
    tier        TEXT NOT NULL DEFAULT 'L2', -- 'L1' | 'L2' | 'plan'
    items       JSONB NOT NULL,           -- [{id, rubric_item, weight}]
    version     INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS check_templates (
    name        TEXT PRIMARY KEY,          -- tests_pass | file_present | schema_valid
    tier        TEXT NOT NULL DEFAULT 'L1', -- 'L1'
    kind        TEXT NOT NULL,              -- shell | file_exists | json_schema
    template    JSONB NOT NULL              -- {cmd?, expect, kind} with placeholders
);

COMMENT ON TABLE rubrics IS 'Retrievable rubric patterns for check-gen and plan-evaluator. L3-tunable.';
COMMENT ON TABLE check_templates IS 'Deterministic L1 check templates with parameter placeholders.';
