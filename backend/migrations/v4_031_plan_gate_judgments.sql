ALTER TABLE plans ADD COLUMN IF NOT EXISTS plan_goal_review REAL;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS plan_l2_judgments JSONB DEFAULT '[]'::jsonb;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS plan_l2_hard_failures JSONB DEFAULT '[]'::jsonb;
