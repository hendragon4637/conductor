-- Steering — same-session retry before remediation
-- steering_count: number of steering attempts already performed on this session.
-- Evaluator uses it to decide NodeSteer (< 5) vs NodeRemediate (>= 5).
-- Executor/planner increment it on each steer; fresh spawns start at 0.

ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS steering_count INTEGER NOT NULL DEFAULT 0;
