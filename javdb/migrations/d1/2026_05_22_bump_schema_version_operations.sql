-- 2026-05-22: Bump SchemaVersion to 14 on javdb-operations (ADR-005 PR-4).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_05_22_bump_schema_version_operations.sql
--
-- The companion history migration (2026_05_22_drop_audit_tables_history.sql)
-- dropped audit tables and bumped history to v14. Reports and operations
-- have no audit tables to drop but must stay version-aligned so that
-- verify_d1_schema_versions() passes.

UPDATE SchemaVersion SET Version = 14;
