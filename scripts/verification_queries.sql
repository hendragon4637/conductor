-- ============================================================================
-- Verification Measurements
-- ============================================================================
-- Run via: docker exec -i postgres psql -U aipc -d aipc_conductor \
--            < scripts/verification_queries.sql
--
-- These queries measure standard-adherence quality across runs and
-- node_sessions. They are designed to be run ad-hoc against the Conductor
-- application database (aipc_conductor).
-- ============================================================================

-- 1. Failure rate by standard
-- For each standard (by runs.standard_ids), count how many node_sessions
-- had gate_outcome='fail' vs total. Grouped by standard slug.
SELECT ds.slug, ds.name,
       COUNT(ns.id) AS total_sessions,
       COUNT(*) FILTER (WHERE ns.gate_outcome = 'fail') AS failures,
       ROUND(COUNT(*) FILTER (WHERE ns.gate_outcome = 'fail')::numeric / GREATEST(COUNT(*), 1), 4) AS failure_rate
FROM domain_standards ds
JOIN runs r ON r.standard_ids @> ARRAY[ds.id]
JOIN node_sessions ns ON ns.run_id = r.id
GROUP BY ds.id, ds.slug, ds.name
ORDER BY failure_rate DESC;

-- 2. L2 score variance by standard
-- Mean, variance, min, max of l2_score per standard.
SELECT ds.slug, ds.name,
       ROUND(AVG(ns.l2_score)::numeric, 4) AS mean_l2,
       ROUND(VAR_POP(ns.l2_score)::numeric, 4) AS variance_l2,
       ROUND(MIN(ns.l2_score)::numeric, 4) AS min_l2,
       ROUND(MAX(ns.l2_score)::numeric, 4) AS max_l2,
       COUNT(ns.id) AS observations
FROM domain_standards ds
JOIN runs r ON r.standard_ids @> ARRAY[ds.id]
JOIN node_sessions ns ON ns.run_id = r.id AND ns.l2_score IS NOT NULL
GROUP BY ds.id, ds.slug, ds.name
ORDER BY variance_l2 DESC;

-- 3. Standard adherence
-- How many runs have standard_ids set vs total runs.
SELECT
       COUNT(*) AS total_runs,
       COUNT(*) FILTER (WHERE standard_ids IS NOT NULL AND array_length(standard_ids, 1) > 0) AS stamped_runs,
       ROUND(COUNT(*) FILTER (WHERE standard_ids IS NOT NULL AND array_length(standard_ids, 1) > 0)::numeric / GREATEST(COUNT(*), 1), 4) AS adherence_rate
FROM runs;
