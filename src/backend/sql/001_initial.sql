-- PostgreSQL 16. Fresh database only; apply with psql -1 -v ON_ERROR_STOP=1.
-- File bytes live under ALO_STORAGE_ROOT, never in PostgreSQL.

CREATE TABLE files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    relative_path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (length(btrim(kind)) > 0),
    size_bytes BIGINT CHECK (size_bytes >= 0),
    sha256 TEXT CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (length(relative_path) > 0 AND relative_path NOT LIKE '/%'
        AND relative_path !~ '^[A-Za-z]:'
        AND position(chr(92) IN relative_path) = 0
        AND relative_path !~ '(^|/)\.{1,2}(/|$)'),
    CHECK (status <> 'ready' OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL))
);

CREATE TABLE sites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
    address TEXT,
    timezone TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id UUID NOT NULL REFERENCES sites(id),
    code TEXT NOT NULL CHECK (code ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (site_id, code),
    UNIQUE (site_id, id)
);

CREATE TABLE gt_datasets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL CHECK (name ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    version TEXT NOT NULL CHECK (version ~ '^v[0-9]{3,}$'),
    archive_file_id UUID NOT NULL REFERENCES files(id),
    manifest_file_id UUID NOT NULL REFERENCES files(id),
    label_schema_version TEXT NOT NULL,
    preprocessing_tool TEXT NOT NULL DEFAULT 'SAM3',
    review_status TEXT NOT NULL DEFAULT 'draft' CHECK (review_status IN ('draft', 'reviewed', 'approved')),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version),
    CHECK (archive_file_id <> manifest_file_id)
);

CREATE TABLE model_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_type TEXT NOT NULL CHECK (model_type IN ('unet', 'mask-rcnn', 'deeplab-v3')),
    version TEXT NOT NULL CHECK (version ~ '^v[0-9]{3,}$'),
    backbone TEXT NOT NULL DEFAULT 'ResNet-50',
    gt_dataset_id UUID REFERENCES gt_datasets(id),
    weights_file_id UUID NOT NULL REFERENCES files(id),
    config_file_id UUID NOT NULL REFERENCES files(id),
    classes_file_id UUID NOT NULL REFERENCES files(id),
    metrics_file_id UUID REFERENCES files(id),
    trained_at TIMESTAMPTZ,
    code_version TEXT,
    library_versions JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(library_versions) = 'object'),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_type, version)
);

CREATE TABLE schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id UUID NOT NULL REFERENCES sites(id),
    version TEXT NOT NULL CHECK (version ~ '^v[0-9]{3,}$'),
    document_date DATE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (site_id, version),
    UNIQUE (site_id, id)
);

CREATE TABLE schedule_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    file_id UUID NOT NULL REFERENCES files(id),
    display_order INTEGER NOT NULL CHECK (display_order >= 0),
    description TEXT,
    UNIQUE (schedule_id, file_id),
    UNIQUE (schedule_id, id)
);

CREATE TABLE schedule_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    schedule_source_id UUID NOT NULL,
    source_task_id TEXT,
    display_order INTEGER NOT NULL CHECK (display_order >= 0),
    week TEXT,
    task_name TEXT NOT NULL CHECK (length(btrim(task_name)) > 0),
    start DATE,
    finish DATE,
    predecessor_id UUID,
    successor_id UUID,
    UNIQUE (schedule_id, id),
    UNIQUE (schedule_source_id, source_task_id),
    UNIQUE (schedule_id, predecessor_id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (schedule_id, successor_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (schedule_id, schedule_source_id) REFERENCES schedule_sources(schedule_id, id),
    FOREIGN KEY (schedule_id, predecessor_id) REFERENCES schedule_tasks(schedule_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (schedule_id, successor_id) REFERENCES schedule_tasks(schedule_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (start IS NULL OR finish IS NULL OR finish >= start),
    CHECK (predecessor_id IS NULL OR predecessor_id <> id),
    CHECK (successor_id IS NULL OR successor_id <> id),
    CHECK (predecessor_id IS NULL OR successor_id IS NULL OR predecessor_id <> successor_id)
);

-- Serialize edits per schedule so concurrent changes cannot create inconsistent chains.
CREATE FUNCTION lock_schedule_tasks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.schedule_id <> OLD.schedule_id THEN
        RAISE EXCEPTION 'A task cannot move to another schedule';
    END IF;
    IF TG_OP = 'DELETE' THEN
        PERFORM id FROM schedules WHERE id = OLD.schedule_id FOR UPDATE;
        RETURN OLD;
    END IF;
    PERFORM id FROM schedules WHERE id = NEW.schedule_id FOR UPDATE;
    RETURN NEW;
END;
$$;

CREATE TRIGGER schedule_tasks_lock
    BEFORE INSERT OR UPDATE OR DELETE ON schedule_tasks
    FOR EACH ROW EXECUTE FUNCTION lock_schedule_tasks();

-- Check reciprocal links after both ends have been edited in the same transaction.
CREATE FUNCTION validate_schedule_task_links() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    task schedule_tasks%ROWTYPE;
    has_cycle BOOLEAN;
BEGIN
    SELECT * INTO task FROM schedule_tasks WHERE id = NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF task.predecessor_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM schedule_tasks WHERE id = task.predecessor_id AND successor_id = task.id
    ) THEN RAISE EXCEPTION 'Predecessor/successor links must be reciprocal'; END IF;
    IF task.successor_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM schedule_tasks WHERE id = task.successor_id AND predecessor_id = task.id
    ) THEN RAISE EXCEPTION 'Predecessor/successor links must be reciprocal'; END IF;
    WITH RECURSIVE chain AS (
        SELECT id, successor_id, ARRAY[id] AS visited, false AS cycle
        FROM schedule_tasks WHERE id = task.id
        UNION ALL
        SELECT next_task.id, next_task.successor_id,
            chain.visited || next_task.id, next_task.id = ANY(chain.visited)
        FROM chain JOIN schedule_tasks AS next_task ON next_task.id = chain.successor_id
        WHERE NOT chain.cycle
    )
    SELECT EXISTS (SELECT 1 FROM chain WHERE cycle) INTO has_cycle;
    IF has_cycle THEN RAISE EXCEPTION 'Schedule task links cannot form a cycle'; END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER schedule_tasks_validate_links
    AFTER INSERT OR UPDATE ON schedule_tasks
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_schedule_task_links();

CREATE TABLE schedule_task_locations (
    site_id UUID NOT NULL,
    schedule_id UUID NOT NULL,
    task_id UUID NOT NULL,
    location_id UUID NOT NULL,
    PRIMARY KEY (task_id, location_id),
    FOREIGN KEY (site_id, schedule_id) REFERENCES schedules(site_id, id),
    FOREIGN KEY (schedule_id, task_id) REFERENCES schedule_tasks(schedule_id, id),
    FOREIGN KEY (site_id, location_id) REFERENCES locations(site_id, id)
);

CREATE TABLE daily_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id UUID NOT NULL,
    location_id UUID NOT NULL,
    observation_date DATE NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    photo_file_id UUID NOT NULL REFERENCES files(id),
    model_version_id UUID REFERENCES model_versions(id),
    result_file_id UUID REFERENCES files(id),
    changes_file_id UUID REFERENCES files(id),
    report_file_id UUID REFERENCES files(id),
    previous_record_id UUID,
    schedule_id UUID,
    comparison_rule_version TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    description TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (location_id, observation_date),
    UNIQUE (location_id, id),
    FOREIGN KEY (site_id, location_id) REFERENCES locations(site_id, id),
    FOREIGN KEY (site_id, schedule_id) REFERENCES schedules(site_id, id),
    FOREIGN KEY (location_id, previous_record_id) REFERENCES daily_records(location_id, id),
    CHECK (previous_record_id IS NULL OR previous_record_id <> id),
    CHECK (status <> 'completed' OR (model_version_id IS NOT NULL AND result_file_id IS NOT NULL AND report_file_id IS NOT NULL)),
    CHECK (changes_file_id IS NULL OR (previous_record_id IS NOT NULL AND comparison_rule_version IS NOT NULL))
);

CREATE INDEX daily_records_site_date_idx ON daily_records(site_id, observation_date DESC);
CREATE INDEX daily_records_status_idx ON daily_records(status) WHERE status <> 'completed';
CREATE INDEX daily_records_model_idx ON daily_records(model_version_id);
CREATE INDEX daily_records_previous_idx ON daily_records(previous_record_id);
CREATE INDEX model_versions_dataset_idx ON model_versions(gt_dataset_id);
CREATE INDEX schedule_sources_file_idx ON schedule_sources(file_id);
CREATE INDEX schedule_tasks_order_idx ON schedule_tasks(schedule_id, display_order, id);
CREATE INDEX schedule_tasks_dates_idx ON schedule_tasks(schedule_id, start, finish);
CREATE INDEX schedule_task_locations_location_idx ON schedule_task_locations(location_id);

-- No sample data, model weights, passwords or guessed schedule tasks are inserted.
