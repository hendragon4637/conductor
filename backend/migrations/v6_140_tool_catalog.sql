-- v6_140: Create tool_catalog table for the Tool Catalog feature.
--
-- Central registry of external tools (skills, MCP servers, CLI binaries)
-- that conductor can discover, vet, and deploy to workspaces.
--
-- Each row represents a single known tool with metadata sourced from
-- GitHub, registries, or manual curation.  The partial unique index
-- ensures at most one active (non-retired) row per tool name.

BEGIN;

-- ── tool_catalog: tool metadata and lifecycle status ────────────────────
CREATE TABLE IF NOT EXISTS tool_catalog (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,               -- singular kebab-case, e.g. 'eslint'
    description       TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('skill', 'mcp', 'cli')),
    source_url        TEXT,                        -- URL to GitHub / registry
    license           TEXT,                        -- SPDX identifier
    stars             INTEGER DEFAULT 0,
    velocity          JSONB,                       -- {commits_per_quarter: int, releases_per_year: int}
    maturity_score    REAL DEFAULT 0.0,
    status            TEXT NOT NULL DEFAULT 'candidate'
                          CHECK (status IN ('candidate', 'vetted', 'retired')),
    status_by         TEXT NOT NULL DEFAULT 'agent'
                          CHECK (status_by IN ('agent', 'human')),
    status_changed_at TIMESTAMPTZ,
    metadata          JSONB DEFAULT '{}',          -- raw scrape results
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);

-- ── partial unique index: one active name at a time ─────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_catalog_active_name
    ON tool_catalog (name)
    WHERE status <> 'retired';

-- ── row-level security roles ─────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aipc_agent') THEN
        CREATE ROLE aipc_agent;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aipc_admin') THEN
        CREATE ROLE aipc_admin;
    END IF;
END
$$;

-- ── row-level security policies ─────────────────────────────────────────
ALTER TABLE tool_catalog ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tool_catalog'
          AND policyname = 'tool_catalog_agent_insert'
    ) THEN
        CREATE POLICY tool_catalog_agent_insert
            ON tool_catalog
            FOR INSERT
            TO aipc_agent
            WITH CHECK (status <> 'vetted' OR status IS NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tool_catalog'
          AND policyname = 'tool_catalog_agent_update'
    ) THEN
        CREATE POLICY tool_catalog_agent_update
            ON tool_catalog
            FOR UPDATE
            TO aipc_agent
            USING (status <> 'vetted' OR status IS NULL)
            WITH CHECK (status <> 'vetted' OR status IS NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tool_catalog'
          AND policyname = 'tool_catalog_admin_all'
    ) THEN
        CREATE POLICY tool_catalog_admin_all
            ON tool_catalog
            FOR UPDATE
            TO aipc_admin
            USING (true)
            WITH CHECK (true);
    END IF;
END
$$;

COMMIT;
