BEGIN;

-- File 06 — Multi-turn clarification state (meta-planner)
-- Adds plan-level state machine columns to pause/resume planning
-- when the goal formulator needs human input.

ALTER TABLE plans ADD COLUMN IF NOT EXISTS plan_status TEXT NOT NULL DEFAULT 'draft';
  -- draft | awaiting_clarification | formulated | decomposed | ratified
  -- (execution states live on runs, not plans)

ALTER TABLE plans ADD COLUMN IF NOT EXISTS clarify_context JSONB NOT NULL DEFAULT '[]'::jsonb;
  -- Condensed multi-turn Q&A history:
  -- [{"round": 1, "questions": [...], "answers": [...]}, ...]

ALTER TABLE plans ADD COLUMN IF NOT EXISTS clarify_rounds INTEGER NOT NULL DEFAULT 0;

ALTER TABLE plans ADD COLUMN IF NOT EXISTS partial_meta_goal JSONB;
  -- Formulator's best-so-far MetaGoal (preserved while awaiting clarification)

COMMIT;
