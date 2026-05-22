-- 003_pattern_enum.sql — Constrain agent_configs.pattern to canonical values

-- First, normalize any non-canonical existing values
UPDATE agent_configs SET pattern = 'standalone' WHERE pattern IN ('PEV', 'pev');
UPDATE agent_configs SET pattern = 'pipeline'   WHERE pattern IN ('linear', 'chain', 'sequence');
UPDATE agent_configs SET pattern = 'critic-verifier' WHERE pattern IN ('designer-critic', 'producer-critic');
UPDATE agent_configs SET pattern = 'custom'     WHERE pattern NOT IN (
  'standalone', 'pipeline', 'supervisor-worker', 'fan-out-fan-in',
  'critic-verifier', 'reflection', 'custom'
);

-- Add CHECK constraint
ALTER TABLE agent_configs DROP CONSTRAINT IF EXISTS pattern_valid;
ALTER TABLE agent_configs ADD CONSTRAINT pattern_valid CHECK (
  pattern IN (
    'standalone', 'pipeline', 'supervisor-worker', 'fan-out-fan-in',
    'critic-verifier', 'reflection', 'custom'
  )
);
