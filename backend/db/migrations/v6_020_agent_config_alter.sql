BEGIN;

ALTER TABLE agent_configs RENAME COLUMN harness_capabilities TO legacy_capabilities;

ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS new_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS tools JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'example-generated';
ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS execution JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
