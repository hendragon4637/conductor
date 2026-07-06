BEGIN;

-- Step 1: Add temporary JSONB column
ALTER TABLE capabilities ADD COLUMN IF NOT EXISTS family_arr JSONB;

-- Step 2: Migrate existing TEXT values to single-element JSONB arrays
UPDATE capabilities SET family_arr = to_jsonb(ARRAY[family]) WHERE family_arr IS NULL;

-- Step 3: Drop old TEXT column and rename
ALTER TABLE capabilities DROP COLUMN family;
ALTER TABLE capabilities RENAME COLUMN family_arr TO family;

-- Step 4: Add NOT NULL constraint
ALTER TABLE capabilities ALTER COLUMN family SET NOT NULL;

-- Step 5: Drop old btree index, create GIN index for JSONB ?| queries
DROP INDEX IF EXISTS idx_cap_family;
CREATE INDEX IF NOT EXISTS idx_cap_family_gin ON capabilities USING GIN (family);

COMMIT;
