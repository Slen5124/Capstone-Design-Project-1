-- PostgreSQL 16. For an existing DB created by the earlier 001_initial.sql.
-- Review the deployed schema and API validation before applying with:
-- psql -1 -v ON_ERROR_STOP=1 -f src/backend/sql/002_model_metadata.sql
-- No data is relabelled, removed, or rewritten. Existing provenance stays intact.

ALTER TABLE model_versions DROP CONSTRAINT IF EXISTS model_versions_model_type_check;
ALTER TABLE model_versions ADD CONSTRAINT model_versions_model_type_check
    CHECK (model_type ~ '^[a-z0-9]+([-_][a-z0-9]+)*$');
ALTER TABLE model_versions ALTER COLUMN backbone DROP DEFAULT;
ALTER TABLE gt_datasets ALTER COLUMN preprocessing_tool DROP DEFAULT;

-- The caller now supplies backbone and preprocessing_tool explicitly.
-- Current model example: model_type='dinov2-upernet', backbone='dinov2-vit-b14-reg4'.
