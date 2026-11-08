-- PostgreSQL 16. Consolidation of the verified 004 + workflow schema.
-- Apply once with psql -1 -v ON_ERROR_STOP=1, after a verified full backup.
-- Reject populated replacement tables. Never discard unexpected business rows.
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '120s';
LOCK TABLE public.agent_report_sources, public.agent_reports, public.camera_capture_candidates, public.change_request_decisions, public.change_request_message_files, public.change_request_messages, public.change_requests, public.daily_records, public.files, public.gt_datasets, public.locations, public.model_versions, public.notifications, public.organizations, public.question_archives, public.schedule_imports, public.schedule_sources, public.schedule_task_dependencies, public.schedule_task_locations, public.schedule_tasks, public.schedules, public.site_cameras, public.site_memberships, public.site_organizations, public.sites, public.submission_files, public.task_execution, public.task_reviews, public.task_submissions, public.task_vision_mappings, public.users, public.vision_evidence, public.vision_imports, public.vision_observations, public.weekly_reports, public.workflow_cycles, public.workflow_extension_context, public.workflow_manager_evidence, public.workflow_projects, public.workflow_report_snapshots, public.workflow_vision_batches, public.workflow_vision_deliveries IN ACCESS EXCLUSIVE MODE;
CREATE TEMP TABLE db20_preservation(name text PRIMARY KEY,digest text) ON COMMIT DROP;
INSERT INTO db20_preservation SELECT 'daily_records',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","site_id","location_id","observation_date","captured_at","photo_file_id","model_version_id","result_file_id","changes_file_id","report_file_id","previous_record_id","schedule_id","comparison_rule_version","status","description","error_message","created_at","updated_at" FROM public.daily_records) r;
INSERT INTO db20_preservation SELECT 'files',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","relative_path","kind","size_bytes","sha256","status","created_at","site_id" FROM public.files) r;
INSERT INTO db20_preservation SELECT 'gt_datasets',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","name","version","archive_file_id","manifest_file_id","label_schema_version","preprocessing_tool","review_status","description","created_at" FROM public.gt_datasets) r;
INSERT INTO db20_preservation SELECT 'locations',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","site_id","code","name","description","created_at" FROM public.locations) r;
INSERT INTO db20_preservation SELECT 'model_versions',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","model_type","version","backbone","gt_dataset_id","weights_file_id","config_file_id","classes_file_id","metrics_file_id","trained_at","code_version","library_versions","description","created_at" FROM public.model_versions) r;
INSERT INTO db20_preservation SELECT 'question_archives',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","site_id","title","asked_at","archive_file_id","description","created_at" FROM public.question_archives) r;
INSERT INTO db20_preservation SELECT 'schedule_sources',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","schedule_id","file_id","display_order","description" FROM public.schedule_sources) r;
INSERT INTO db20_preservation SELECT 'schedule_task_locations',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "site_id","schedule_id","task_id","location_id" FROM public.schedule_task_locations) r;
INSERT INTO db20_preservation SELECT 'schedule_tasks',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","schedule_id","schedule_source_id","source_task_id","display_order","week","task_name","start","finish","predecessor_id","successor_id","surface_type","category_tags","evidence_route" FROM public.schedule_tasks) r;
INSERT INTO db20_preservation SELECT 'schedules',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","site_id","version","document_date","description","created_at" FROM public.schedules) r;
INSERT INTO db20_preservation SELECT 'sites',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","code","name","address","timezone","description","created_at","latitude","longitude" FROM public.sites) r;
INSERT INTO db20_preservation SELECT 'weekly_reports',md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id","site_id","schedule_id","week","period_start","period_finish","report_file_id","metadata_file_id","description","created_at" FROM public.weekly_reports) r;
DO $$
DECLARE relation text; occupied boolean;
BEGIN
    IF (SELECT count(*) FROM pg_tables WHERE schemaname='public') <> 42 THEN
        RAISE EXCEPTION 'Expected the verified source schema';
    END IF;
    FOREACH relation IN ARRAY ARRAY['agent_report_sources','agent_reports','camera_capture_candidates','change_request_decisions','change_request_message_files','change_request_messages','change_requests','notifications','organizations','schedule_imports','schedule_task_dependencies','site_cameras','site_memberships','site_organizations','submission_files','task_execution','task_reviews','task_submissions','task_vision_mappings','users','vision_evidence','vision_imports','vision_observations' ] LOOP
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I)', relation) INTO occupied;
        IF occupied THEN RAISE EXCEPTION 'Migration requires explicit data mapping for populated table %', relation; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM workflow_projects p LEFT JOIN sites s ON s.code=p.site_code
               LEFT JOIN schedules sc ON sc.id::text=p.schedule_id AND sc.site_id=s.id
               WHERE s.id IS NULL OR sc.id IS NULL OR sc.version<>p.schedule_version) THEN
        RAISE EXCEPTION 'A workflow project has an invalid site or schedule';
    END IF;
END $$;
CREATE TEMP TABLE db20_project_backup ON COMMIT DROP AS TABLE workflow_projects;
CREATE TEMP TABLE db20_backup_workflow_cycles ON COMMIT DROP AS TABLE workflow_cycles;
CREATE TEMP TABLE db20_backup_workflow_extension_context ON COMMIT DROP AS TABLE workflow_extension_context;
CREATE TEMP TABLE db20_backup_workflow_manager_evidence ON COMMIT DROP AS TABLE workflow_manager_evidence;
CREATE TEMP TABLE db20_backup_workflow_report_snapshots ON COMMIT DROP AS TABLE workflow_report_snapshots;
CREATE TEMP TABLE db20_backup_workflow_vision_batches ON COMMIT DROP AS TABLE workflow_vision_batches;
CREATE TEMP TABLE db20_backup_workflow_vision_deliveries ON COMMIT DROP AS TABLE workflow_vision_deliveries;
DROP TABLE public.agent_report_sources, public.agent_reports, public.camera_capture_candidates, public.change_request_decisions, public.change_request_message_files, public.change_request_messages, public.change_requests, public.notifications, public.organizations, public.schedule_imports, public.schedule_task_dependencies, public.site_cameras, public.site_memberships, public.site_organizations, public.submission_files, public.task_execution, public.task_reviews, public.task_submissions, public.task_vision_mappings, public.users, public.vision_evidence, public.vision_imports, public.vision_observations, public.workflow_cycles, public.workflow_extension_context, public.workflow_manager_evidence, public.workflow_projects, public.workflow_report_snapshots, public.workflow_vision_batches, public.workflow_vision_deliveries CASCADE;

ALTER TABLE schedule_tasks RENAME COLUMN category_tags TO category_codes;
ALTER TABLE schedule_tasks RENAME COLUMN evidence_route TO processing_route;
ALTER TABLE schedule_tasks ADD COLUMN effective_start date, ADD COLUMN effective_finish date,
    ADD CHECK (coalesce(effective_start,start)<=coalesce(effective_finish,finish));
ALTER TABLE locations ADD COLUMN camera_provider text, ADD COLUMN camera_external_id text,
    ADD COLUMN credential_ref text;
CREATE TABLE organizations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    organization_type text NOT NULL DEFAULT 'partner'
);
CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_provider text NOT NULL DEFAULT 'local' CHECK (btrim(auth_provider)<>''),
    auth_subject text NOT NULL CHECK (btrim(auth_subject)<>''),
    display_name text NOT NULL CHECK (btrim(display_name)<>''),
    is_active boolean NOT NULL DEFAULT true,
    UNIQUE (auth_provider,auth_subject)
);
CREATE TABLE site_memberships (
    site_id uuid NOT NULL REFERENCES sites(id),
    user_id uuid NOT NULL REFERENCES users(id),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    role text NOT NULL CHECK (role IN ('hq','field','viewer')),
    is_active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (site_id,user_id,organization_id)
);
ALTER TABLE files ADD COLUMN visibility text NOT NULL DEFAULT 'internal'
    CHECK (visibility IN ('internal','site','request')),
    ADD COLUMN uploaded_by uuid REFERENCES users(id);
ALTER TABLE daily_records ADD UNIQUE (site_id,id);
CREATE TABLE change_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    task_id uuid NOT NULL,
    requester_id uuid NOT NULL,
    from_organization_id uuid NOT NULL,
    to_organization_id uuid NOT NULL REFERENCES organizations(id),
    request_type text NOT NULL CHECK (request_type IN ('schedule','extension','work')),
    proposed_start date,
    proposed_finish date,
    proposed_work text,
    reason text NOT NULL CHECK (btrim(reason)<>''),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected','applied')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_id,schedule_id,task_id,id),
    FOREIGN KEY (site_id,schedule_id) REFERENCES schedules(site_id,id),
    FOREIGN KEY (schedule_id,task_id) REFERENCES schedule_tasks(schedule_id,id),
    FOREIGN KEY (site_id,requester_id,from_organization_id)
        REFERENCES site_memberships(site_id,user_id,organization_id),
    CHECK (from_organization_id<>to_organization_id),
    CHECK (proposed_start IS NULL OR proposed_finish IS NULL OR proposed_start<=proposed_finish),
    CHECK ((request_type='work' AND proposed_work IS NOT NULL AND btrim(proposed_work)<>'') OR
        (request_type IN ('schedule','extension') AND (proposed_start IS NOT NULL OR proposed_finish IS NOT NULL))),
    CHECK (request_type<>'extension' OR proposed_finish IS NOT NULL)
);
CREATE TABLE task_observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    task_id uuid NOT NULL,
    daily_record_id uuid NOT NULL,
    target_key text NOT NULL CHECK (btrim(target_key)<>''),
    view_key text NOT NULL CHECK (btrim(view_key)<>''),
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    installed_ratio numeric CHECK (installed_ratio BETWEEN 0 AND 1),
    status text NOT NULL CHECK (status IN ('observed','unobserved','held','failed')),
    hold_reason text,
    overlay_file_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_id,schedule_id,task_id,id),
    UNIQUE (task_id,daily_record_id,target_key,view_key,revision),
    FOREIGN KEY (site_id,schedule_id) REFERENCES schedules(site_id,id),
    FOREIGN KEY (schedule_id,task_id) REFERENCES schedule_tasks(schedule_id,id),
    FOREIGN KEY (site_id,daily_record_id) REFERENCES daily_records(site_id,id),
    FOREIGN KEY (site_id,overlay_file_id) REFERENCES files(site_id,id),
    CHECK ((status='observed')=(installed_ratio IS NOT NULL)),
    CHECK (status='observed' OR (hold_reason IS NOT NULL AND btrim(hold_reason)<>''))
);
CREATE TABLE task_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id uuid NOT NULL REFERENCES sites(id),
    schedule_id uuid,
    task_id uuid,
    actor_id uuid REFERENCES users(id),
    event_type text NOT NULL CHECK (event_type IN ('submission','resubmission','review_accept','review_reject',
        'completion_confirm','message','request_accept','request_reject','schedule_apply','work_apply',
        'classification_change','workflow_state')),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    request_id uuid,
    target_event_id uuid,
    observation_id uuid,
    body text,
    details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details)='object'),
    UNIQUE (site_id,id),
    FOREIGN KEY (site_id,schedule_id) REFERENCES schedules(site_id,id),
    FOREIGN KEY (schedule_id,task_id) REFERENCES schedule_tasks(schedule_id,id),
    FOREIGN KEY (site_id,schedule_id,task_id,request_id)
        REFERENCES change_requests(site_id,schedule_id,task_id,id),
    FOREIGN KEY (site_id,target_event_id) REFERENCES task_events(site_id,id),
    FOREIGN KEY (site_id,schedule_id,task_id,observation_id)
        REFERENCES task_observations(site_id,schedule_id,task_id,id),
    CHECK (event_type='workflow_state' OR (schedule_id IS NOT NULL AND task_id IS NOT NULL AND actor_id IS NOT NULL)),
    CHECK (request_id IS NULL OR (schedule_id IS NOT NULL AND task_id IS NOT NULL)),
    CHECK (observation_id IS NULL OR (schedule_id IS NOT NULL AND task_id IS NOT NULL)),
    CHECK (event_type NOT IN ('message','request_accept','request_reject','schedule_apply','work_apply') OR request_id IS NOT NULL),
    CHECK (event_type NOT IN ('review_accept','review_reject','completion_confirm') OR
        ((target_event_id IS NOT NULL)::integer+(observation_id IS NOT NULL)::integer)=1),
    CHECK (target_event_id IS NULL OR target_event_id<>id),
    CHECK (event_type NOT IN ('review_reject','request_reject') OR btrim(coalesce(body,''))<>'')
);
CREATE TABLE task_event_files (
    site_id uuid NOT NULL,
    event_id uuid NOT NULL,
    file_id uuid NOT NULL,
    sort_order integer NOT NULL DEFAULT 0 CHECK (sort_order>=0),
    PRIMARY KEY (event_id,file_id),
    FOREIGN KEY (site_id,event_id) REFERENCES task_events(site_id,id),
    FOREIGN KEY (site_id,file_id) REFERENCES files(site_id,id)
);
CREATE TABLE agent_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    period_start date NOT NULL,
    period_finish date NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','failed','validated','approved')),
    report_file_id uuid,
    generated_at timestamptz NOT NULL DEFAULT now(),
    approved_by uuid REFERENCES users(id),
    approved_at timestamptz,
    provenance jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(provenance)='object'),
    FOREIGN KEY (site_id,schedule_id) REFERENCES schedules(site_id,id),
    FOREIGN KEY (site_id,report_file_id) REFERENCES files(site_id,id),
    UNIQUE (site_id,schedule_id,period_start,period_finish,revision),
    CHECK (period_start<=period_finish),
    CHECK ((status='approved')=(approved_by IS NOT NULL AND approved_at IS NOT NULL)),
    CHECK ((approved_by IS NULL)=(approved_at IS NULL))
);

-- Enforce site/task and actor membership at the DB trust boundary.
CREATE FUNCTION alo_task_event_scope() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public AS $$
DECLARE target public.task_events; request public.change_requests;
BEGIN
    IF NEW.actor_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM site_memberships m JOIN users u ON u.id=m.user_id
        WHERE m.site_id=NEW.site_id AND m.user_id=NEW.actor_id AND m.is_active AND u.is_active) THEN
        RAISE EXCEPTION 'Actor is not an active site member' USING ERRCODE='23503';
    END IF;
    IF NEW.target_event_id IS NOT NULL THEN
        SELECT * INTO STRICT target FROM task_events WHERE id=NEW.target_event_id;
        IF (target.site_id,target.schedule_id,target.task_id) IS DISTINCT FROM (NEW.site_id,NEW.schedule_id,NEW.task_id) THEN
            RAISE EXCEPTION 'Review target belongs to another task' USING ERRCODE='23503';
        END IF;
    END IF;
    IF NEW.event_type IN ('review_accept','review_reject','completion_confirm') AND NOT EXISTS (
        SELECT 1 FROM public.site_memberships WHERE site_id=NEW.site_id AND user_id=NEW.actor_id
            AND role='hq' AND is_active) THEN
        RAISE EXCEPTION 'A site HQ reviewer is required' USING ERRCODE='42501';
    END IF;
    IF NEW.event_type IN ('request_accept','request_reject','schedule_apply','work_apply') THEN
        SELECT * INTO STRICT request FROM public.change_requests WHERE id=NEW.request_id FOR UPDATE;
        IF NOT EXISTS (SELECT 1 FROM public.site_memberships WHERE site_id=NEW.site_id
            AND user_id=NEW.actor_id AND organization_id=request.to_organization_id
            AND role IN ('hq','field') AND is_active) THEN
            RAISE EXCEPTION 'The request recipient must decide or apply the change' USING ERRCODE='42501';
        END IF;
        IF NEW.event_type IN ('request_accept','request_reject') THEN
            IF request.status<>'pending' THEN RAISE EXCEPTION 'Request is already decided'; END IF;
            UPDATE public.change_requests SET status=CASE WHEN NEW.event_type='request_accept' THEN 'accepted' ELSE 'rejected' END
                WHERE id=NEW.request_id;
        ELSE
            IF request.status<>'accepted' THEN RAISE EXCEPTION 'Only accepted requests can be applied'; END IF;
            IF NEW.event_type='schedule_apply' THEN
                IF request.request_type='work' THEN RAISE EXCEPTION 'Work scope cannot be applied as dates'; END IF;
                PERFORM 1 FROM public.schedule_tasks WHERE id=NEW.task_id FOR UPDATE;
                IF request.request_type='extension' AND EXISTS (
                    SELECT 1 FROM public.schedule_tasks t WHERE t.id=NEW.task_id
                        AND request.proposed_finish<=coalesce(t.effective_finish,t.finish)) THEN
                    RAISE EXCEPTION 'An extension must exceed the currently applied finish';
                END IF;
                IF EXISTS (SELECT 1 FROM public.schedule_tasks t WHERE t.id=NEW.task_id AND
                    coalesce(request.proposed_start,t.effective_start,t.start)>
                    coalesce(request.proposed_finish,t.effective_finish,t.finish)) THEN
                    RAISE EXCEPTION 'Applied schedule dates conflict';
                END IF;
                UPDATE public.schedule_tasks SET
                    effective_start=coalesce(request.proposed_start,effective_start,start),
                    effective_finish=coalesce(request.proposed_finish,effective_finish,finish)
                    WHERE id=NEW.task_id;
            ELSIF request.request_type<>'work' THEN
                RAISE EXCEPTION 'A schedule request must be applied as dates';
            END IF;
            UPDATE public.change_requests SET status='applied' WHERE id=NEW.request_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER task_event_scope BEFORE INSERT OR UPDATE ON task_events
    FOR EACH ROW EXECUTE FUNCTION alo_task_event_scope();

CREATE INDEX site_memberships_user_site_idx ON site_memberships(user_id,site_id);
CREATE INDEX task_events_task_time_idx ON task_events(site_id,task_id,occurred_at DESC,id);
CREATE INDEX task_events_request_time_idx ON task_events(request_id,occurred_at,id) WHERE request_id IS NOT NULL;
CREATE INDEX task_events_review_target_idx ON task_events(target_event_id) WHERE target_event_id IS NOT NULL;
CREATE INDEX task_events_observation_idx ON task_events(observation_id) WHERE observation_id IS NOT NULL;
CREATE INDEX change_requests_pending_idx ON change_requests(site_id,status,created_at DESC);
CREATE UNIQUE INDEX task_events_one_request_decision ON task_events(request_id)
    WHERE event_type IN ('request_accept','request_reject');
CREATE UNIQUE INDEX task_events_one_request_application ON task_events(request_id)
    WHERE event_type IN ('schedule_apply','work_apply');
CREATE UNIQUE INDEX task_events_workflow_key ON task_events((details->>'relation'),(details->>'key'))
    WHERE event_type='workflow_state';

-- Legacy workflow names are views. Their rows live in the shared event table.
-- Keep the existing API's string identifiers and JSON payload contract.
CREATE VIEW workflow_cycles AS SELECT
    (details->'row'->>'key')::varchar AS "key",
    (details->'row'->>'site_code')::varchar AS "site_code",
    (details->'row'->>'schedule_id')::varchar AS "schedule_id",
    (details->'row'->>'week')::integer AS "week",
    (details->'row'->>'batch_id')::varchar AS "batch_id",
    (details->'row'->>'status')::varchar AS "status",
    (details->'row'->>'updated_at')::varchar AS "updated_at"
FROM task_events WHERE event_type='workflow_state' AND details->>'relation'='workflow_cycles';
CREATE VIEW workflow_extension_context AS SELECT
    (details->'row'->>'request_id')::varchar AS "request_id",
    (details->'row'->>'site_code')::varchar AS "site_code",
    (details->'row'->>'schedule_id')::varchar AS "schedule_id",
    (details->'row'->>'task_id')::varchar AS "task_id",
    (details->'row'->>'requested_week')::integer AS "requested_week",
    (details->'row'->>'applies_from_week')::integer AS "applies_from_week",
    (details->'row'->>'baseline_start')::date AS "baseline_start",
    (details->'row'->>'baseline_end')::date AS "baseline_end",
    (details->'row'->>'baseline_digest')::varchar AS "baseline_digest",
    (details->'row'->>'created_at')::varchar AS "created_at"
FROM task_events WHERE event_type='workflow_state' AND details->>'relation'='workflow_extension_context';
CREATE VIEW workflow_manager_evidence AS SELECT
    (details->'row'->>'id')::varchar AS "id",
    (details->'row'->>'cycle_key')::varchar AS "cycle_key",
    (details->'row'->>'task_id')::varchar AS "task_id",
    (details->'row'->'payload')::json AS "payload",
    (details->'row'->>'created_at')::varchar AS "created_at"
FROM task_events WHERE event_type='workflow_state' AND details->>'relation'='workflow_manager_evidence';
CREATE VIEW workflow_projects AS SELECT
    (details->'row'->>'site_code')::varchar AS "site_code",
    (details->'row'->>'schedule_id')::varchar AS "schedule_id",
    (details->'row'->>'schedule_version')::varchar AS "schedule_version",
    (details->'row'->>'project_start')::varchar AS "project_start",
    (details->'row'->>'timezone')::varchar AS "timezone"
FROM task_events WHERE event_type='workflow_state' AND details->>'relation'='workflow_projects';
CREATE VIEW workflow_report_snapshots AS SELECT
    (details->'row'->>'id')::varchar AS "id",
    (details->'row'->>'cycle_key')::varchar AS "cycle_key",
    (details->'row'->>'material_digest')::varchar AS "material_digest",
    (details->'row'->>'status')::varchar AS "status",
    (details->'row'->'payload')::json AS "payload",
    (details->'row'->>'report_file_id')::varchar AS "report_file_id",
    (details->'row'->>'created_at')::varchar AS "created_at"
FROM task_events WHERE event_type='workflow_state' AND details->>'relation'='workflow_report_snapshots';
CREATE VIEW workflow_vision_batches AS SELECT
    (details->'row'->>'id')::varchar AS "id",
    (details->'row'->>'cycle_key')::varchar AS "cycle_key",
    (details->'row'->>'status')::varchar AS "status",
    (details->'row'->'results')::json AS "results",
    (details->'row'->>'digest')::varchar AS "digest",
    (details->'row'->>'created_at')::varchar AS "created_at",
    (details->'row'->>'updated_at')::varchar AS "updated_at"
FROM task_events WHERE event_type='workflow_state' AND details->>'relation'='workflow_vision_batches';
CREATE VIEW workflow_vision_deliveries AS SELECT
    (details->'row'->>'batch_id')::varchar AS "batch_id",
    (details->'row'->>'cycle_key')::varchar AS "cycle_key",
    (details->'row'->>'revision')::integer AS "revision",
    (details->'row'->>'result_file_id')::varchar AS "result_file_id",
    (details->'row'->>'payload_digest')::varchar AS "payload_digest",
    (details->'row'->'skipped')::json AS "skipped",
    (details->'row'->>'created_at')::varchar AS "created_at"
FROM task_events WHERE event_type='workflow_state' AND details->>'relation'='workflow_vision_deliveries';

CREATE FUNCTION alo_workflow_view_write() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public AS $$
DECLARE payload jsonb; key_field text; row_key text; event_key uuid;
    site_code_value text; project jsonb; site uuid; schedule uuid; cycle jsonb; required_field text;
BEGIN
    key_field := CASE TG_TABLE_NAME
        WHEN 'workflow_projects' THEN 'site_code' WHEN 'workflow_cycles' THEN 'key'
        WHEN 'workflow_vision_batches' THEN 'id' WHEN 'workflow_manager_evidence' THEN 'id'
        WHEN 'workflow_report_snapshots' THEN 'id' WHEN 'workflow_vision_deliveries' THEN 'batch_id'
        WHEN 'workflow_extension_context' THEN 'request_id'
        ELSE NULL END;
    IF key_field IS NULL THEN RAISE EXCEPTION 'Unsupported workflow relation'; END IF;
    IF TG_OP='DELETE' THEN payload:=to_jsonb(OLD); ELSE payload:=to_jsonb(NEW); END IF;
    FOREACH required_field IN ARRAY CASE TG_TABLE_NAME
        WHEN 'workflow_cycles' THEN ARRAY['key','site_code','schedule_id','week','status','updated_at']
        WHEN 'workflow_extension_context' THEN ARRAY['request_id','site_code','schedule_id','task_id','requested_week','applies_from_week','baseline_start','baseline_end','baseline_digest','created_at']
        WHEN 'workflow_manager_evidence' THEN ARRAY['id','cycle_key','task_id','payload','created_at']
        WHEN 'workflow_projects' THEN ARRAY['site_code','schedule_id','schedule_version','project_start','timezone']
        WHEN 'workflow_report_snapshots' THEN ARRAY['id','cycle_key','material_digest','status','payload','created_at']
        WHEN 'workflow_vision_batches' THEN ARRAY['id','cycle_key','status','results','created_at','updated_at']
        WHEN 'workflow_vision_deliveries' THEN ARRAY['batch_id','cycle_key','revision','result_file_id','payload_digest','skipped','created_at'] ELSE ARRAY[]::text[] END LOOP
        IF payload->required_field IS NULL OR payload->required_field='null'::jsonb THEN
            RAISE EXCEPTION 'Workflow field % is required',required_field USING ERRCODE='23502';
        END IF;
    END LOOP;
    row_key:=payload->>key_field;
    IF row_key IS NULL OR btrim(row_key)='' THEN RAISE EXCEPTION 'Workflow key is required'; END IF;
    IF TG_OP='UPDATE' AND to_jsonb(OLD)->>key_field IS DISTINCT FROM row_key THEN
        RAISE EXCEPTION 'Workflow identity is immutable';
    END IF;
    event_key:=md5(TG_TABLE_NAME||':'||row_key)::uuid;
    IF TG_TABLE_NAME IN ('workflow_projects','workflow_cycles','workflow_extension_context') THEN site_code_value:=payload->>'site_code';
    ELSE
        SELECT to_jsonb(c) INTO cycle FROM public.workflow_cycles c WHERE c.key=payload->>'cycle_key';
        IF cycle IS NULL THEN
            -- Report snapshots may precede creation of their workflow cycle.
            SELECT jsonb_build_object('site_code',s.code,'schedule_id',sc.id::text) INTO STRICT cycle
            FROM public.sites s JOIN public.schedules sc ON sc.site_id=s.id
            WHERE s.code=split_part(payload->>'cycle_key',':',1)
                AND sc.id::text=split_part(payload->>'cycle_key',':',2)
                AND cardinality(string_to_array(payload->>'cycle_key',':'))=3
                AND split_part(payload->>'cycle_key',':',3) ~ '^[1-9][0-9]*$';
        END IF;
        site_code_value:=cycle->>'site_code';
    END IF;
    SELECT s.id INTO STRICT site FROM public.sites s WHERE s.code=site_code_value;
    IF TG_TABLE_NAME IN ('workflow_projects','workflow_cycles','workflow_extension_context') THEN project:=payload; ELSE project:=cycle; END IF;
    SELECT sc.id INTO STRICT schedule FROM public.schedules sc
        WHERE sc.id::text=project->>'schedule_id' AND sc.site_id=site;
    IF TG_TABLE_NAME='workflow_manager_evidence' AND NOT EXISTS (
        SELECT 1 FROM public.schedule_tasks t WHERE t.schedule_id=schedule AND t.id::text=payload->>'task_id') THEN
        RAISE EXCEPTION 'Evidence task is outside the cycle schedule';
    END IF;
    IF TG_TABLE_NAME='workflow_extension_context' AND NOT EXISTS (
        SELECT 1 FROM public.change_requests r WHERE r.id::text=payload->>'request_id'
            AND r.site_id=site AND r.schedule_id=schedule AND r.task_id::text=payload->>'task_id') THEN
        RAISE EXCEPTION 'Extension context is outside its canonical request';
    END IF;
    IF TG_OP='INSERT' THEN
        INSERT INTO public.task_events(id,site_id,schedule_id,event_type,details)
        VALUES(event_key,site,schedule,'workflow_state',jsonb_build_object('relation',TG_TABLE_NAME,'key',row_key,'row',payload));
    ELSIF TG_OP='UPDATE' THEN
        UPDATE public.task_events SET details=jsonb_build_object('relation',TG_TABLE_NAME,'key',row_key,'row',payload),occurred_at=now()
        WHERE id=event_key AND site_id=site AND schedule_id=schedule AND event_type='workflow_state';
        IF NOT FOUND THEN RAISE EXCEPTION 'Workflow row disappeared'; END IF;
    ELSE
        DELETE FROM public.task_events WHERE id=event_key AND event_type='workflow_state';
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR UPDATE OR DELETE ON workflow_cycles FOR EACH ROW EXECUTE FUNCTION alo_workflow_view_write();
CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR UPDATE OR DELETE ON workflow_extension_context FOR EACH ROW EXECUTE FUNCTION alo_workflow_view_write();
CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR UPDATE OR DELETE ON workflow_manager_evidence FOR EACH ROW EXECUTE FUNCTION alo_workflow_view_write();
CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR UPDATE OR DELETE ON workflow_projects FOR EACH ROW EXECUTE FUNCTION alo_workflow_view_write();
CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR UPDATE OR DELETE ON workflow_report_snapshots FOR EACH ROW EXECUTE FUNCTION alo_workflow_view_write();
CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR UPDATE OR DELETE ON workflow_vision_batches FOR EACH ROW EXECUTE FUNCTION alo_workflow_view_write();
CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR UPDATE OR DELETE ON workflow_vision_deliveries FOR EACH ROW EXECUTE FUNCTION alo_workflow_view_write();

CREATE UNIQUE INDEX task_events_workflow_delivery_revision ON task_events
    ((details->'row'->>'cycle_key'),((details->'row'->>'revision')::integer))
    WHERE event_type='workflow_state' AND details->>'relation'='workflow_vision_deliveries';
INSERT INTO workflow_projects SELECT * FROM db20_project_backup;
INSERT INTO workflow_cycles SELECT * FROM db20_backup_workflow_cycles;
INSERT INTO workflow_vision_batches SELECT * FROM db20_backup_workflow_vision_batches;
INSERT INTO workflow_manager_evidence SELECT * FROM db20_backup_workflow_manager_evidence;
INSERT INTO workflow_report_snapshots SELECT * FROM db20_backup_workflow_report_snapshots;
INSERT INTO workflow_vision_deliveries SELECT * FROM db20_backup_workflow_vision_deliveries;
INSERT INTO workflow_extension_context SELECT * FROM db20_backup_workflow_extension_context;
COMMENT ON COLUMN task_events.details IS 'Event-specific metadata. workflow_state rows retain legacy API state as relation/key/row JSON.';
COMMENT ON COLUMN schedule_tasks.processing_route IS 'ai / field / manual_check; independent of category_codes and completion.';

-- Read/write aliases for the deployed extension API; no extra storage tables.
CREATE VIEW legacy_change_requests AS SELECT id,site_id,schedule_id,task_id,
    requester_id AS sender_id,from_organization_id AS sender_org_id,
    to_organization_id AS recipient_org_id,request_type AS kind,
    proposed_work AS summary,reason,proposed_start,proposed_finish,created_at FROM change_requests;
CREATE VIEW legacy_site_memberships AS SELECT site_id,user_id,organization_id,role,is_active AS active FROM site_memberships;
CREATE VIEW site_organizations AS SELECT DISTINCT site_id,organization_id FROM site_memberships;
CREATE VIEW change_request_decisions AS
SELECT e.request_id,e.site_id,e.actor_id AS decided_by,r.to_organization_id AS recipient_org_id,
    CASE e.event_type WHEN 'request_accept' THEN 'accepted' ELSE 'rejected' END AS outcome,
    e.body AS reason,e.occurred_at AS decided_at,a.occurred_at AS applied_at,a.actor_id AS applied_by
FROM task_events e JOIN change_requests r ON r.id=e.request_id
LEFT JOIN task_events a ON a.request_id=e.request_id AND a.event_type IN ('schedule_apply','work_apply')
WHERE e.event_type IN ('request_accept','request_reject');
CREATE FUNCTION alo_legacy_decision_write() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public AS $$
DECLARE request public.change_requests;
BEGIN
    SELECT * INTO STRICT request FROM public.change_requests WHERE id=NEW.request_id FOR UPDATE;
    IF NEW.site_id<>request.site_id OR NEW.recipient_org_id<>request.to_organization_id THEN
        RAISE EXCEPTION 'Decision scope differs from request';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.outcome NOT IN ('accepted','rejected') OR NEW.applied_at IS NOT NULL OR NEW.applied_by IS NOT NULL THEN
            RAISE EXCEPTION 'Invalid initial decision';
        END IF;
        INSERT INTO public.task_events(site_id,schedule_id,task_id,actor_id,event_type,request_id,body,occurred_at)
        VALUES(request.site_id,request.schedule_id,request.task_id,NEW.decided_by,
            CASE NEW.outcome WHEN 'accepted' THEN 'request_accept' ELSE 'request_reject' END,
            NEW.request_id,NEW.reason,coalesce(NEW.decided_at,now()));
    ELSE
        IF (to_jsonb(NEW)-'applied_at'-'applied_by') IS DISTINCT FROM (to_jsonb(OLD)-'applied_at'-'applied_by')
            OR OLD.applied_at IS NOT NULL OR OLD.outcome<>'accepted' OR NEW.applied_at IS NULL OR NEW.applied_by IS NULL THEN
            RAISE EXCEPTION 'A decision is immutable; only its first accepted application is allowed';
        END IF;
        INSERT INTO public.task_events(site_id,schedule_id,task_id,actor_id,event_type,request_id,occurred_at)
        VALUES(request.site_id,request.schedule_id,request.task_id,NEW.applied_by,
            CASE request.request_type WHEN 'work' THEN 'work_apply' ELSE 'schedule_apply' END,NEW.request_id,NEW.applied_at);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER legacy_decision_write INSTEAD OF INSERT OR UPDATE ON change_request_decisions
    FOR EACH ROW EXECUTE FUNCTION alo_legacy_decision_write();

CREATE VIEW task_review_queue AS
SELECT e.site_id,e.schedule_id,e.task_id,e.id AS item_id,e.occurred_at AS created_at,
    'field'::text AS evidence_source,
    CASE WHEN e.details->>'completion_requested'='true' THEN 'completion_requested' ELSE 'field_submitted' END AS status
FROM task_events e WHERE e.event_type IN ('submission','resubmission')
    AND NOT EXISTS (SELECT 1 FROM task_events r WHERE r.target_event_id=e.id
        AND r.event_type IN ('review_accept','review_reject','completion_confirm'))
UNION ALL
SELECT o.site_id,o.schedule_id,o.task_id,o.id,o.created_at,'ai','ai_review'
FROM task_observations o WHERE NOT EXISTS (SELECT 1 FROM task_events r WHERE r.observation_id=o.id
    AND r.event_type IN ('review_accept','review_reject','completion_confirm'))
UNION ALL
SELECT r.site_id,r.schedule_id,r.task_id,r.id,r.created_at,NULL::text,'extension_requested'
FROM change_requests r WHERE r.request_type='extension' AND r.status='pending';

-- Existing loopback service account remains trusted. End-user RLS needs the
-- authenticated context and API integration described in the access guide.
REVOKE ALL ON organizations,users,site_memberships,change_requests,task_observations,task_events,task_event_files,agent_reports FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO alo_app;
REVOKE ALL ON FUNCTION alo_task_event_scope(),alo_workflow_view_write() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION alo_task_event_scope(),alo_workflow_view_write() TO alo_app;
REVOKE ALL ON FUNCTION alo_legacy_decision_write() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION alo_legacy_decision_write() TO alo_app;
DO $$ BEGIN
    IF (SELECT count(*) FROM pg_tables WHERE schemaname='public')<>20 THEN
        RAISE EXCEPTION 'Target schema must have exactly 20 base tables';
    END IF;
    IF (SELECT count(*) FROM workflow_projects)<>(SELECT count(*) FROM db20_project_backup) THEN
        RAISE EXCEPTION 'Workflow configuration transfer failed';
    END IF;
END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='daily_records') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","site_id" AS "site_id","location_id" AS "location_id","observation_date" AS "observation_date","captured_at" AS "captured_at","photo_file_id" AS "photo_file_id","model_version_id" AS "model_version_id","result_file_id" AS "result_file_id","changes_file_id" AS "changes_file_id","report_file_id" AS "report_file_id","previous_record_id" AS "previous_record_id","schedule_id" AS "schedule_id","comparison_rule_version" AS "comparison_rule_version","status" AS "status","description" AS "description","error_message" AS "error_message","created_at" AS "created_at","updated_at" AS "updated_at" FROM public.daily_records) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in daily_records'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='files') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","relative_path" AS "relative_path","kind" AS "kind","size_bytes" AS "size_bytes","sha256" AS "sha256","status" AS "status","created_at" AS "created_at","site_id" AS "site_id" FROM public.files) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in files'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='gt_datasets') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","name" AS "name","version" AS "version","archive_file_id" AS "archive_file_id","manifest_file_id" AS "manifest_file_id","label_schema_version" AS "label_schema_version","preprocessing_tool" AS "preprocessing_tool","review_status" AS "review_status","description" AS "description","created_at" AS "created_at" FROM public.gt_datasets) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in gt_datasets'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='locations') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","site_id" AS "site_id","code" AS "code","name" AS "name","description" AS "description","created_at" AS "created_at" FROM public.locations) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in locations'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='model_versions') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","model_type" AS "model_type","version" AS "version","backbone" AS "backbone","gt_dataset_id" AS "gt_dataset_id","weights_file_id" AS "weights_file_id","config_file_id" AS "config_file_id","classes_file_id" AS "classes_file_id","metrics_file_id" AS "metrics_file_id","trained_at" AS "trained_at","code_version" AS "code_version","library_versions" AS "library_versions","description" AS "description","created_at" AS "created_at" FROM public.model_versions) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in model_versions'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='question_archives') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","site_id" AS "site_id","title" AS "title","asked_at" AS "asked_at","archive_file_id" AS "archive_file_id","description" AS "description","created_at" AS "created_at" FROM public.question_archives) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in question_archives'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='schedule_sources') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","schedule_id" AS "schedule_id","file_id" AS "file_id","display_order" AS "display_order","description" AS "description" FROM public.schedule_sources) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in schedule_sources'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='schedule_task_locations') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "site_id" AS "site_id","schedule_id" AS "schedule_id","task_id" AS "task_id","location_id" AS "location_id" FROM public.schedule_task_locations) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in schedule_task_locations'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='schedule_tasks') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","schedule_id" AS "schedule_id","schedule_source_id" AS "schedule_source_id","source_task_id" AS "source_task_id","display_order" AS "display_order","week" AS "week","task_name" AS "task_name","start" AS "start","finish" AS "finish","predecessor_id" AS "predecessor_id","successor_id" AS "successor_id","surface_type" AS "surface_type","category_codes" AS "category_tags","processing_route" AS "evidence_route" FROM public.schedule_tasks) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in schedule_tasks'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='schedules') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","site_id" AS "site_id","version" AS "version","document_date" AS "document_date","description" AS "description","created_at" AS "created_at" FROM public.schedules) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in schedules'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='sites') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","code" AS "code","name" AS "name","address" AS "address","timezone" AS "timezone","description" AS "description","created_at" AS "created_at","latitude" AS "latitude","longitude" AS "longitude" FROM public.sites) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in sites'; END IF; END $$;
DO $$ BEGIN IF (SELECT digest FROM db20_preservation WHERE name='weekly_reports') IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM (SELECT "id" AS "id","site_id" AS "site_id","schedule_id" AS "schedule_id","week" AS "week","period_start" AS "period_start","period_finish" AS "period_finish","report_file_id" AS "report_file_id","metadata_file_id" AS "metadata_file_id","description" AS "description","created_at" AS "created_at" FROM public.weekly_reports) r) THEN RAISE EXCEPTION 'Original data fingerprint changed in weekly_reports'; END IF; END $$;
DO $$ BEGIN IF (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM workflow_projects r) IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM db20_project_backup r) THEN RAISE EXCEPTION 'Workflow project contents changed'; END IF; END $$;
DO $$ BEGIN IF (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM workflow_cycles r) IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM db20_backup_workflow_cycles r) THEN RAISE EXCEPTION 'Workflow contents changed in workflow_cycles'; END IF; END $$;
DO $$ BEGIN IF (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM workflow_vision_batches r) IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM db20_backup_workflow_vision_batches r) THEN RAISE EXCEPTION 'Workflow contents changed in workflow_vision_batches'; END IF; END $$;
DO $$ BEGIN IF (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM workflow_manager_evidence r) IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM db20_backup_workflow_manager_evidence r) THEN RAISE EXCEPTION 'Workflow contents changed in workflow_manager_evidence'; END IF; END $$;
DO $$ BEGIN IF (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM workflow_report_snapshots r) IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM db20_backup_workflow_report_snapshots r) THEN RAISE EXCEPTION 'Workflow contents changed in workflow_report_snapshots'; END IF; END $$;
DO $$ BEGIN IF (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM workflow_vision_deliveries r) IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM db20_backup_workflow_vision_deliveries r) THEN RAISE EXCEPTION 'Workflow contents changed in workflow_vision_deliveries'; END IF; END $$;
DO $$ BEGIN IF (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM workflow_extension_context r) IS DISTINCT FROM (SELECT md5(coalesce(string_agg(to_jsonb(r)::text,E'\n' ORDER BY to_jsonb(r)::text),'')) FROM db20_backup_workflow_extension_context r) THEN RAISE EXCEPTION 'Workflow contents changed in workflow_extension_context'; END IF; END $$;
