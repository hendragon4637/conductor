-- L2 partial judgments for chunked evaluation with re-queue resilience.
-- Stores completed rubric judgments so re-delivered evaluations skip them.
-- Also stores the best_chunk_idx so the next delivery starts with the
-- most informative chunk first.

ALTER TABLE node_sessions
  ADD COLUMN IF NOT EXISTS l2_partial_judgments JSONB DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS l2_best_chunk_idx INTEGER DEFAULT NULL;
