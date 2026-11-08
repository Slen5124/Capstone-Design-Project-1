-- PostgreSQL 16. Install the current 20-table schema into a NEW EMPTY database.
-- Run with psql -1 -v ON_ERROR_STOP=1. Login roles and application grants are separate.
DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public') THEN
    RAISE EXCEPTION 'Clean schema installation requires an empty public schema';
END IF; END $$;
--
-- PostgreSQL database dump
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: alo_legacy_decision_write(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.alo_legacy_decision_write() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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


--
-- Name: alo_report_sections_valid(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.alo_report_sections_valid(doc jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE
    names text[] := ARRAY['weekly_summary','baseline_comparison','weekend_comparison',
        'limitations','change_summary','next_week_plan','sources_and_checks'];
    section text;
    sentence jsonb;
BEGIN
    IF jsonb_typeof(doc) <> 'object' THEN RETURN false; END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(doc)) <> cardinality(names) THEN RETURN false; END IF;
    FOREACH section IN ARRAY names LOOP
        IF jsonb_typeof(doc->section) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(doc->section) = 0 THEN RETURN false; END IF;
        FOR sentence IN SELECT value FROM jsonb_array_elements(doc->section) LOOP
            IF jsonb_typeof(sentence) <> 'string' OR char_length(btrim(sentence #>> '{}')) NOT BETWEEN 10 AND 240
            THEN RETURN false; END IF;
        END LOOP;
    END LOOP;
    RETURN true;
END;
$$;


--
-- Name: alo_task_event_scope(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.alo_task_event_scope() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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


--
-- Name: alo_workflow_view_write(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.alo_workflow_view_write() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
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
END $_$;


--
-- Name: lock_schedule_tasks(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.lock_schedule_tasks() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: validate_schedule_task_links(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_schedule_task_links() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    period_start date NOT NULL,
    period_finish date NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    report_file_id uuid,
    generated_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_by uuid,
    approved_at timestamp with time zone,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT agent_reports_check CHECK ((period_start <= period_finish)),
    CONSTRAINT agent_reports_check1 CHECK (((status = 'approved'::text) = ((approved_by IS NOT NULL) AND (approved_at IS NOT NULL)))),
    CONSTRAINT agent_reports_check2 CHECK (((approved_by IS NULL) = (approved_at IS NULL))),
    CONSTRAINT agent_reports_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT agent_reports_revision_check CHECK ((revision > 0)),
    CONSTRAINT agent_reports_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'failed'::text, 'validated'::text, 'approved'::text])))
);


--
-- Name: change_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.change_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    task_id uuid NOT NULL,
    requester_id uuid NOT NULL,
    from_organization_id uuid NOT NULL,
    to_organization_id uuid NOT NULL,
    request_type text NOT NULL,
    proposed_start date,
    proposed_finish date,
    proposed_work text,
    reason text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT change_requests_check CHECK ((from_organization_id <> to_organization_id)),
    CONSTRAINT change_requests_check1 CHECK (((proposed_start IS NULL) OR (proposed_finish IS NULL) OR (proposed_start <= proposed_finish))),
    CONSTRAINT change_requests_check2 CHECK ((((request_type = 'work'::text) AND (proposed_work IS NOT NULL) AND (btrim(proposed_work) <> ''::text)) OR ((request_type = ANY (ARRAY['schedule'::text, 'extension'::text])) AND ((proposed_start IS NOT NULL) OR (proposed_finish IS NOT NULL))))),
    CONSTRAINT change_requests_check3 CHECK (((request_type <> 'extension'::text) OR (proposed_finish IS NOT NULL))),
    CONSTRAINT change_requests_reason_check CHECK ((btrim(reason) <> ''::text)),
    CONSTRAINT change_requests_request_type_check CHECK ((request_type = ANY (ARRAY['schedule'::text, 'extension'::text, 'work'::text]))),
    CONSTRAINT change_requests_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'accepted'::text, 'rejected'::text, 'applied'::text])))
);


--
-- Name: task_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    schedule_id uuid,
    task_id uuid,
    actor_id uuid,
    event_type text NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    request_id uuid,
    target_event_id uuid,
    observation_id uuid,
    body text,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT task_events_check CHECK (((event_type = 'workflow_state'::text) OR ((schedule_id IS NOT NULL) AND (task_id IS NOT NULL) AND (actor_id IS NOT NULL)))),
    CONSTRAINT task_events_check1 CHECK (((request_id IS NULL) OR ((schedule_id IS NOT NULL) AND (task_id IS NOT NULL)))),
    CONSTRAINT task_events_check2 CHECK (((observation_id IS NULL) OR ((schedule_id IS NOT NULL) AND (task_id IS NOT NULL)))),
    CONSTRAINT task_events_check3 CHECK (((event_type <> ALL (ARRAY['message'::text, 'request_accept'::text, 'request_reject'::text, 'schedule_apply'::text, 'work_apply'::text])) OR (request_id IS NOT NULL))),
    CONSTRAINT task_events_check4 CHECK (((event_type <> ALL (ARRAY['review_accept'::text, 'review_reject'::text, 'completion_confirm'::text])) OR ((((target_event_id IS NOT NULL))::integer + ((observation_id IS NOT NULL))::integer) = 1))),
    CONSTRAINT task_events_check5 CHECK (((target_event_id IS NULL) OR (target_event_id <> id))),
    CONSTRAINT task_events_check6 CHECK (((event_type <> ALL (ARRAY['review_reject'::text, 'request_reject'::text])) OR (btrim(COALESCE(body, ''::text)) <> ''::text))),
    CONSTRAINT task_events_details_check CHECK ((jsonb_typeof(details) = 'object'::text)),
    CONSTRAINT task_events_event_type_check CHECK ((event_type = ANY (ARRAY['submission'::text, 'resubmission'::text, 'review_accept'::text, 'review_reject'::text, 'completion_confirm'::text, 'message'::text, 'request_accept'::text, 'request_reject'::text, 'schedule_apply'::text, 'work_apply'::text, 'classification_change'::text, 'workflow_state'::text])))
);


--
-- Name: COLUMN task_events.details; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_events.details IS 'Event-specific metadata. workflow_state rows retain legacy API state as relation/key/row JSON.';


--
-- Name: change_request_decisions; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.change_request_decisions AS
 SELECT e.request_id,
    e.site_id,
    e.actor_id AS decided_by,
    r.to_organization_id AS recipient_org_id,
        CASE e.event_type
            WHEN 'request_accept'::text THEN 'accepted'::text
            ELSE 'rejected'::text
        END AS outcome,
    e.body AS reason,
    e.occurred_at AS decided_at,
    a.occurred_at AS applied_at,
    a.actor_id AS applied_by
   FROM ((public.task_events e
     JOIN public.change_requests r ON ((r.id = e.request_id)))
     LEFT JOIN public.task_events a ON (((a.request_id = e.request_id) AND (a.event_type = ANY (ARRAY['schedule_apply'::text, 'work_apply'::text])))))
  WHERE (e.event_type = ANY (ARRAY['request_accept'::text, 'request_reject'::text]));


--
-- Name: daily_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.daily_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    location_id uuid NOT NULL,
    observation_date date NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    photo_file_id uuid NOT NULL,
    model_version_id uuid,
    result_file_id uuid,
    changes_file_id uuid,
    report_file_id uuid,
    previous_record_id uuid,
    schedule_id uuid,
    comparison_rule_version text,
    status text DEFAULT 'pending'::text NOT NULL,
    description text,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT daily_records_check CHECK (((previous_record_id IS NULL) OR (previous_record_id <> id))),
    CONSTRAINT daily_records_check1 CHECK (((status <> 'completed'::text) OR ((model_version_id IS NOT NULL) AND (result_file_id IS NOT NULL) AND (report_file_id IS NOT NULL)))),
    CONSTRAINT daily_records_check2 CHECK (((changes_file_id IS NULL) OR ((previous_record_id IS NOT NULL) AND (comparison_rule_version IS NOT NULL)))),
    CONSTRAINT daily_records_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'processing'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.files (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    relative_path text NOT NULL,
    kind text NOT NULL,
    size_bytes bigint,
    sha256 text,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    site_id uuid,
    visibility text DEFAULT 'internal'::text NOT NULL,
    uploaded_by uuid,
    CONSTRAINT files_check CHECK (((status <> 'ready'::text) OR ((size_bytes IS NOT NULL) AND (sha256 IS NOT NULL)))),
    CONSTRAINT files_kind_check CHECK ((length(btrim(kind)) > 0)),
    CONSTRAINT files_relative_path_check CHECK (((length(relative_path) > 0) AND (relative_path !~~ '/%'::text) AND (relative_path !~ '^[A-Za-z]:'::text) AND (POSITION((chr(92)) IN (relative_path)) = 0) AND (relative_path !~ '(^|/)\.{1,2}(/|$)'::text))),
    CONSTRAINT files_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT files_size_bytes_check CHECK ((size_bytes >= 0)),
    CONSTRAINT files_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'ready'::text, 'failed'::text]))),
    CONSTRAINT files_visibility_check CHECK ((visibility = ANY (ARRAY['internal'::text, 'site'::text, 'request'::text])))
);


--
-- Name: gt_datasets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gt_datasets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    version text NOT NULL,
    archive_file_id uuid NOT NULL,
    manifest_file_id uuid NOT NULL,
    label_schema_version text NOT NULL,
    preprocessing_tool text DEFAULT 'SAM3'::text NOT NULL,
    review_status text DEFAULT 'draft'::text NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT gt_datasets_check CHECK ((archive_file_id <> manifest_file_id)),
    CONSTRAINT gt_datasets_name_check CHECK ((name ~ '^[a-z0-9]+(-[a-z0-9]+)*$'::text)),
    CONSTRAINT gt_datasets_review_status_check CHECK ((review_status = ANY (ARRAY['draft'::text, 'reviewed'::text, 'approved'::text]))),
    CONSTRAINT gt_datasets_version_check CHECK ((version ~ '^v[0-9]{3,}$'::text))
);


--
-- Name: legacy_change_requests; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.legacy_change_requests AS
 SELECT id,
    site_id,
    schedule_id,
    task_id,
    requester_id AS sender_id,
    from_organization_id AS sender_org_id,
    to_organization_id AS recipient_org_id,
    request_type AS kind,
    proposed_work AS summary,
    reason,
    proposed_start,
    proposed_finish,
    created_at
   FROM public.change_requests;


--
-- Name: site_memberships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.site_memberships (
    site_id uuid NOT NULL,
    user_id uuid NOT NULL,
    organization_id uuid NOT NULL,
    role text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT site_memberships_role_check CHECK ((role = ANY (ARRAY['hq'::text, 'field'::text, 'viewer'::text])))
);


--
-- Name: legacy_site_memberships; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.legacy_site_memberships AS
 SELECT site_id,
    user_id,
    organization_id,
    role,
    is_active AS active
   FROM public.site_memberships;


--
-- Name: locations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.locations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    camera_provider text,
    camera_external_id text,
    credential_ref text,
    CONSTRAINT locations_code_check CHECK ((code ~ '^[a-z0-9]+(-[a-z0-9]+)*$'::text)),
    CONSTRAINT locations_name_check CHECK ((length(btrim(name)) > 0))
);


--
-- Name: model_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    model_type text NOT NULL,
    version text NOT NULL,
    backbone text DEFAULT 'ResNet-50'::text NOT NULL,
    gt_dataset_id uuid,
    weights_file_id uuid NOT NULL,
    config_file_id uuid NOT NULL,
    classes_file_id uuid NOT NULL,
    metrics_file_id uuid,
    trained_at timestamp with time zone,
    code_version text,
    library_versions jsonb DEFAULT '{}'::jsonb NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT model_versions_library_versions_check CHECK ((jsonb_typeof(library_versions) = 'object'::text)),
    CONSTRAINT model_versions_model_type_check CHECK ((model_type = ANY (ARRAY['unet'::text, 'mask-rcnn'::text, 'deeplab-v3'::text]))),
    CONSTRAINT model_versions_version_check CHECK ((version ~ '^v[0-9]{3,}$'::text))
);


--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    organization_type text DEFAULT 'partner'::text NOT NULL,
    CONSTRAINT organizations_name_check CHECK (((length(btrim(name)) >= 1) AND (length(btrim(name)) <= 200)))
);


--
-- Name: question_archives; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.question_archives (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    title text NOT NULL,
    asked_at timestamp with time zone NOT NULL,
    archive_file_id uuid NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT question_archives_title_check CHECK (((length(btrim(title)) >= 1) AND (length(btrim(title)) <= 200)))
);


--
-- Name: TABLE question_archives; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.question_archives IS 'Site question/answer Markdown archives. Full content lives in files, not chat sessions.';


--
-- Name: schedule_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_sources (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    schedule_id uuid NOT NULL,
    file_id uuid NOT NULL,
    display_order integer NOT NULL,
    description text,
    CONSTRAINT schedule_sources_display_order_check CHECK ((display_order >= 0))
);


--
-- Name: schedule_task_locations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_task_locations (
    site_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    task_id uuid NOT NULL,
    location_id uuid NOT NULL
);


--
-- Name: schedule_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    schedule_id uuid NOT NULL,
    schedule_source_id uuid NOT NULL,
    source_task_id text,
    display_order integer NOT NULL,
    week text,
    task_name text NOT NULL,
    start date,
    finish date,
    predecessor_id uuid,
    successor_id uuid,
    surface_type text,
    category_codes text[] DEFAULT '{}'::text[] NOT NULL,
    processing_route text,
    effective_start date,
    effective_finish date,
    CONSTRAINT schedule_tasks_category_tags_check CHECK (((category_codes <@ ARRAY['wall'::text, 'floor'::text, 'ceiling'::text, 'fixture'::text, 'mep'::text, 'admin'::text, 'other'::text]) AND (array_position(category_codes, NULL::text) IS NULL))),
    CONSTRAINT schedule_tasks_check CHECK (((start IS NULL) OR (finish IS NULL) OR (finish >= start))),
    CONSTRAINT schedule_tasks_check1 CHECK (((predecessor_id IS NULL) OR (predecessor_id <> id))),
    CONSTRAINT schedule_tasks_check2 CHECK (((successor_id IS NULL) OR (successor_id <> id))),
    CONSTRAINT schedule_tasks_check3 CHECK (((predecessor_id IS NULL) OR (successor_id IS NULL) OR (predecessor_id <> successor_id))),
    CONSTRAINT schedule_tasks_check4 CHECK ((COALESCE(effective_start, start) <= COALESCE(effective_finish, finish))),
    CONSTRAINT schedule_tasks_display_order_check CHECK ((display_order >= 0)),
    CONSTRAINT schedule_tasks_evidence_route_check CHECK ((processing_route = ANY (ARRAY['ai'::text, 'field'::text, 'manual_check'::text]))),
    CONSTRAINT schedule_tasks_surface_type_check CHECK ((surface_type = ANY (ARRAY['floor'::text, 'ceiling'::text, 'wall'::text]))),
    CONSTRAINT schedule_tasks_task_name_check CHECK ((length(btrim(task_name)) > 0))
);


--
-- Name: COLUMN schedule_tasks.surface_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schedule_tasks.surface_type IS 'Related surface only: floor/ceiling/wall or SQL NULL. Not activity completion or progress.';


--
-- Name: COLUMN schedule_tasks.processing_route; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schedule_tasks.processing_route IS 'ai / field / manual_check; independent of category_codes and completion.';


--
-- Name: schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    version text NOT NULL,
    document_date date,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT schedules_version_check CHECK ((version ~ '^v[0-9]{3,}$'::text))
);


--
-- Name: site_organizations; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.site_organizations AS
 SELECT DISTINCT site_id,
    organization_id
   FROM public.site_memberships;


--
-- Name: sites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sites (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    address text,
    timezone text NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    latitude numeric(8,5),
    longitude numeric(8,5),
    CONSTRAINT sites_check CHECK ((((latitude IS NULL) AND (longitude IS NULL)) OR ((latitude IS NOT NULL) AND (longitude IS NOT NULL) AND ((latitude >= ('-90'::integer)::numeric) AND (latitude <= (90)::numeric)) AND ((longitude >= ('-180'::integer)::numeric) AND (longitude <= (180)::numeric))))),
    CONSTRAINT sites_code_check CHECK ((code ~ '^[a-z0-9]+(-[a-z0-9]+)*$'::text)),
    CONSTRAINT sites_name_check CHECK ((length(btrim(name)) > 0))
);


--
-- Name: task_event_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_event_files (
    site_id uuid NOT NULL,
    event_id uuid NOT NULL,
    file_id uuid NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    CONSTRAINT task_event_files_sort_order_check CHECK ((sort_order >= 0))
);


--
-- Name: task_observations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_observations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    task_id uuid NOT NULL,
    daily_record_id uuid NOT NULL,
    target_key text NOT NULL,
    view_key text NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    installed_ratio numeric,
    status text NOT NULL,
    hold_reason text,
    overlay_file_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT task_observations_check CHECK (((status = 'observed'::text) = (installed_ratio IS NOT NULL))),
    CONSTRAINT task_observations_check1 CHECK (((status = 'observed'::text) OR ((hold_reason IS NOT NULL) AND (btrim(hold_reason) <> ''::text)))),
    CONSTRAINT task_observations_installed_ratio_check CHECK (((installed_ratio >= (0)::numeric) AND (installed_ratio <= (1)::numeric))),
    CONSTRAINT task_observations_revision_check CHECK ((revision > 0)),
    CONSTRAINT task_observations_status_check CHECK ((status = ANY (ARRAY['observed'::text, 'unobserved'::text, 'held'::text, 'failed'::text]))),
    CONSTRAINT task_observations_target_key_check CHECK ((btrim(target_key) <> ''::text)),
    CONSTRAINT task_observations_view_key_check CHECK ((btrim(view_key) <> ''::text))
);


--
-- Name: task_review_queue; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.task_review_queue AS
 SELECT e.site_id,
    e.schedule_id,
    e.task_id,
    e.id AS item_id,
    e.occurred_at AS created_at,
    'field'::text AS evidence_source,
        CASE
            WHEN ((e.details ->> 'completion_requested'::text) = 'true'::text) THEN 'completion_requested'::text
            ELSE 'field_submitted'::text
        END AS status
   FROM public.task_events e
  WHERE ((e.event_type = ANY (ARRAY['submission'::text, 'resubmission'::text])) AND (NOT (EXISTS ( SELECT 1
           FROM public.task_events r
          WHERE ((r.target_event_id = e.id) AND (r.event_type = ANY (ARRAY['review_accept'::text, 'review_reject'::text, 'completion_confirm'::text])))))))
UNION ALL
 SELECT o.site_id,
    o.schedule_id,
    o.task_id,
    o.id AS item_id,
    o.created_at,
    'ai'::text AS evidence_source,
    'ai_review'::text AS status
   FROM public.task_observations o
  WHERE (NOT (EXISTS ( SELECT 1
           FROM public.task_events r
          WHERE ((r.observation_id = o.id) AND (r.event_type = ANY (ARRAY['review_accept'::text, 'review_reject'::text, 'completion_confirm'::text]))))))
UNION ALL
 SELECT r.site_id,
    r.schedule_id,
    r.task_id,
    r.id AS item_id,
    r.created_at,
    NULL::text AS evidence_source,
    'extension_requested'::text AS status
   FROM public.change_requests r
  WHERE ((r.request_type = 'extension'::text) AND (r.status = 'pending'::text));


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    auth_provider text DEFAULT 'local'::text NOT NULL,
    auth_subject text NOT NULL,
    display_name text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT users_auth_provider_check CHECK ((btrim(auth_provider) <> ''::text)),
    CONSTRAINT users_auth_subject_check CHECK ((btrim(auth_subject) <> ''::text)),
    CONSTRAINT users_display_name_check CHECK ((btrim(display_name) <> ''::text))
);


--
-- Name: weekly_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.weekly_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    schedule_id uuid,
    week text NOT NULL,
    period_start date,
    period_finish date,
    report_file_id uuid NOT NULL,
    metadata_file_id uuid NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT weekly_reports_check CHECK (((period_start IS NULL) OR (period_finish IS NULL) OR (period_finish >= period_start))),
    CONSTRAINT weekly_reports_check1 CHECK ((report_file_id <> metadata_file_id)),
    CONSTRAINT weekly_reports_week_check CHECK ((week ~ '^w[1-9][0-9]*$'::text))
);


--
-- Name: COLUMN weekly_reports.week; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.weekly_reports.week IS 'Original report week w1/w2/...; not an ISO calendar week.';


--
-- Name: workflow_cycles; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.workflow_cycles AS
 SELECT (((details -> 'row'::text) ->> 'key'::text))::character varying AS key,
    (((details -> 'row'::text) ->> 'site_code'::text))::character varying AS site_code,
    (((details -> 'row'::text) ->> 'schedule_id'::text))::character varying AS schedule_id,
    (((details -> 'row'::text) ->> 'week'::text))::integer AS week,
    (((details -> 'row'::text) ->> 'batch_id'::text))::character varying AS batch_id,
    (((details -> 'row'::text) ->> 'status'::text))::character varying AS status,
    (((details -> 'row'::text) ->> 'updated_at'::text))::character varying AS updated_at
   FROM public.task_events
  WHERE ((event_type = 'workflow_state'::text) AND ((details ->> 'relation'::text) = 'workflow_cycles'::text));


--
-- Name: workflow_extension_context; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.workflow_extension_context AS
 SELECT (((details -> 'row'::text) ->> 'request_id'::text))::character varying AS request_id,
    (((details -> 'row'::text) ->> 'site_code'::text))::character varying AS site_code,
    (((details -> 'row'::text) ->> 'schedule_id'::text))::character varying AS schedule_id,
    (((details -> 'row'::text) ->> 'task_id'::text))::character varying AS task_id,
    (((details -> 'row'::text) ->> 'requested_week'::text))::integer AS requested_week,
    (((details -> 'row'::text) ->> 'applies_from_week'::text))::integer AS applies_from_week,
    (((details -> 'row'::text) ->> 'baseline_start'::text))::date AS baseline_start,
    (((details -> 'row'::text) ->> 'baseline_end'::text))::date AS baseline_end,
    (((details -> 'row'::text) ->> 'baseline_digest'::text))::character varying AS baseline_digest,
    (((details -> 'row'::text) ->> 'created_at'::text))::character varying AS created_at
   FROM public.task_events
  WHERE ((event_type = 'workflow_state'::text) AND ((details ->> 'relation'::text) = 'workflow_extension_context'::text));


--
-- Name: workflow_manager_evidence; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.workflow_manager_evidence AS
 SELECT (((details -> 'row'::text) ->> 'id'::text))::character varying AS id,
    (((details -> 'row'::text) ->> 'cycle_key'::text))::character varying AS cycle_key,
    (((details -> 'row'::text) ->> 'task_id'::text))::character varying AS task_id,
    (((details -> 'row'::text) -> 'payload'::text))::json AS payload,
    (((details -> 'row'::text) ->> 'created_at'::text))::character varying AS created_at
   FROM public.task_events
  WHERE ((event_type = 'workflow_state'::text) AND ((details ->> 'relation'::text) = 'workflow_manager_evidence'::text));


--
-- Name: workflow_projects; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.workflow_projects AS
 SELECT (((details -> 'row'::text) ->> 'site_code'::text))::character varying AS site_code,
    (((details -> 'row'::text) ->> 'schedule_id'::text))::character varying AS schedule_id,
    (((details -> 'row'::text) ->> 'schedule_version'::text))::character varying AS schedule_version,
    (((details -> 'row'::text) ->> 'project_start'::text))::character varying AS project_start,
    (((details -> 'row'::text) ->> 'timezone'::text))::character varying AS timezone
   FROM public.task_events
  WHERE ((event_type = 'workflow_state'::text) AND ((details ->> 'relation'::text) = 'workflow_projects'::text));


--
-- Name: workflow_report_snapshots; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.workflow_report_snapshots AS
 SELECT (((details -> 'row'::text) ->> 'id'::text))::character varying AS id,
    (((details -> 'row'::text) ->> 'cycle_key'::text))::character varying AS cycle_key,
    (((details -> 'row'::text) ->> 'material_digest'::text))::character varying AS material_digest,
    (((details -> 'row'::text) ->> 'status'::text))::character varying AS status,
    (((details -> 'row'::text) -> 'payload'::text))::json AS payload,
    (((details -> 'row'::text) ->> 'report_file_id'::text))::character varying AS report_file_id,
    (((details -> 'row'::text) ->> 'created_at'::text))::character varying AS created_at
   FROM public.task_events
  WHERE ((event_type = 'workflow_state'::text) AND ((details ->> 'relation'::text) = 'workflow_report_snapshots'::text));


--
-- Name: workflow_vision_batches; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.workflow_vision_batches AS
 SELECT (((details -> 'row'::text) ->> 'id'::text))::character varying AS id,
    (((details -> 'row'::text) ->> 'cycle_key'::text))::character varying AS cycle_key,
    (((details -> 'row'::text) ->> 'status'::text))::character varying AS status,
    (((details -> 'row'::text) -> 'results'::text))::json AS results,
    (((details -> 'row'::text) ->> 'digest'::text))::character varying AS digest,
    (((details -> 'row'::text) ->> 'created_at'::text))::character varying AS created_at,
    (((details -> 'row'::text) ->> 'updated_at'::text))::character varying AS updated_at
   FROM public.task_events
  WHERE ((event_type = 'workflow_state'::text) AND ((details ->> 'relation'::text) = 'workflow_vision_batches'::text));


--
-- Name: workflow_vision_deliveries; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.workflow_vision_deliveries AS
 SELECT (((details -> 'row'::text) ->> 'batch_id'::text))::character varying AS batch_id,
    (((details -> 'row'::text) ->> 'cycle_key'::text))::character varying AS cycle_key,
    (((details -> 'row'::text) ->> 'revision'::text))::integer AS revision,
    (((details -> 'row'::text) ->> 'result_file_id'::text))::character varying AS result_file_id,
    (((details -> 'row'::text) ->> 'payload_digest'::text))::character varying AS payload_digest,
    (((details -> 'row'::text) -> 'skipped'::text))::json AS skipped,
    (((details -> 'row'::text) ->> 'created_at'::text))::character varying AS created_at
   FROM public.task_events
  WHERE ((event_type = 'workflow_state'::text) AND ((details ->> 'relation'::text) = 'workflow_vision_deliveries'::text));


--
-- Name: agent_reports agent_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT agent_reports_pkey PRIMARY KEY (id);


--
-- Name: agent_reports agent_reports_site_id_schedule_id_period_start_period_finis_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT agent_reports_site_id_schedule_id_period_start_period_finis_key UNIQUE (site_id, schedule_id, period_start, period_finish, revision);


--
-- Name: change_requests change_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_requests
    ADD CONSTRAINT change_requests_pkey PRIMARY KEY (id);


--
-- Name: change_requests change_requests_site_id_schedule_id_task_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_requests
    ADD CONSTRAINT change_requests_site_id_schedule_id_task_id_id_key UNIQUE (site_id, schedule_id, task_id, id);


--
-- Name: daily_records daily_records_location_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_location_id_id_key UNIQUE (location_id, id);


--
-- Name: daily_records daily_records_location_id_observation_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_location_id_observation_date_key UNIQUE (location_id, observation_date);


--
-- Name: daily_records daily_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_pkey PRIMARY KEY (id);


--
-- Name: daily_records daily_records_site_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_site_id_id_key UNIQUE (site_id, id);


--
-- Name: files files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_pkey PRIMARY KEY (id);


--
-- Name: files files_relative_path_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_relative_path_key UNIQUE (relative_path);


--
-- Name: files files_site_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_site_id_id_key UNIQUE (site_id, id);


--
-- Name: gt_datasets gt_datasets_name_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gt_datasets
    ADD CONSTRAINT gt_datasets_name_version_key UNIQUE (name, version);


--
-- Name: gt_datasets gt_datasets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gt_datasets
    ADD CONSTRAINT gt_datasets_pkey PRIMARY KEY (id);


--
-- Name: locations locations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_pkey PRIMARY KEY (id);


--
-- Name: locations locations_site_id_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_site_id_code_key UNIQUE (site_id, code);


--
-- Name: locations locations_site_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_site_id_id_key UNIQUE (site_id, id);


--
-- Name: model_versions model_versions_model_type_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_model_type_version_key UNIQUE (model_type, version);


--
-- Name: model_versions model_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_pkey PRIMARY KEY (id);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (id);


--
-- Name: question_archives question_archives_archive_file_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_archives
    ADD CONSTRAINT question_archives_archive_file_id_key UNIQUE (archive_file_id);


--
-- Name: question_archives question_archives_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_archives
    ADD CONSTRAINT question_archives_pkey PRIMARY KEY (id);


--
-- Name: schedule_sources schedule_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sources
    ADD CONSTRAINT schedule_sources_pkey PRIMARY KEY (id);


--
-- Name: schedule_sources schedule_sources_schedule_id_file_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sources
    ADD CONSTRAINT schedule_sources_schedule_id_file_id_key UNIQUE (schedule_id, file_id);


--
-- Name: schedule_sources schedule_sources_schedule_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sources
    ADD CONSTRAINT schedule_sources_schedule_id_id_key UNIQUE (schedule_id, id);


--
-- Name: schedule_task_locations schedule_task_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task_locations
    ADD CONSTRAINT schedule_task_locations_pkey PRIMARY KEY (task_id, location_id);


--
-- Name: schedule_tasks schedule_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_pkey PRIMARY KEY (id);


--
-- Name: schedule_tasks schedule_tasks_schedule_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_schedule_id_id_key UNIQUE (schedule_id, id);


--
-- Name: schedule_tasks schedule_tasks_schedule_id_predecessor_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_schedule_id_predecessor_id_key UNIQUE (schedule_id, predecessor_id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: schedule_tasks schedule_tasks_schedule_id_successor_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_schedule_id_successor_id_key UNIQUE (schedule_id, successor_id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: schedule_tasks schedule_tasks_schedule_source_id_source_task_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_schedule_source_id_source_task_id_key UNIQUE (schedule_source_id, source_task_id);


--
-- Name: schedules schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_pkey PRIMARY KEY (id);


--
-- Name: schedules schedules_site_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_site_id_id_key UNIQUE (site_id, id);


--
-- Name: schedules schedules_site_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_site_id_version_key UNIQUE (site_id, version);


--
-- Name: site_memberships site_memberships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.site_memberships
    ADD CONSTRAINT site_memberships_pkey PRIMARY KEY (site_id, user_id, organization_id);


--
-- Name: sites sites_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_code_key UNIQUE (code);


--
-- Name: sites sites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_pkey PRIMARY KEY (id);


--
-- Name: task_event_files task_event_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_event_files
    ADD CONSTRAINT task_event_files_pkey PRIMARY KEY (event_id, file_id);


--
-- Name: task_events task_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_pkey PRIMARY KEY (id);


--
-- Name: task_events task_events_site_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_site_id_id_key UNIQUE (site_id, id);


--
-- Name: task_observations task_observations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_observations
    ADD CONSTRAINT task_observations_pkey PRIMARY KEY (id);


--
-- Name: task_observations task_observations_site_id_schedule_id_task_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_observations
    ADD CONSTRAINT task_observations_site_id_schedule_id_task_id_id_key UNIQUE (site_id, schedule_id, task_id, id);


--
-- Name: task_observations task_observations_task_id_daily_record_id_target_key_view_k_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_observations
    ADD CONSTRAINT task_observations_task_id_daily_record_id_target_key_view_k_key UNIQUE (task_id, daily_record_id, target_key, view_key, revision);


--
-- Name: users users_auth_provider_auth_subject_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_auth_provider_auth_subject_key UNIQUE (auth_provider, auth_subject);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: weekly_reports weekly_reports_metadata_file_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weekly_reports
    ADD CONSTRAINT weekly_reports_metadata_file_id_key UNIQUE (metadata_file_id);


--
-- Name: weekly_reports weekly_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weekly_reports
    ADD CONSTRAINT weekly_reports_pkey PRIMARY KEY (id);


--
-- Name: weekly_reports weekly_reports_report_file_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weekly_reports
    ADD CONSTRAINT weekly_reports_report_file_id_key UNIQUE (report_file_id);


--
-- Name: weekly_reports weekly_reports_site_id_week_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weekly_reports
    ADD CONSTRAINT weekly_reports_site_id_week_key UNIQUE (site_id, week);


--
-- Name: change_requests_pending_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX change_requests_pending_idx ON public.change_requests USING btree (site_id, status, created_at DESC);


--
-- Name: daily_records_model_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX daily_records_model_idx ON public.daily_records USING btree (model_version_id);


--
-- Name: daily_records_previous_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX daily_records_previous_idx ON public.daily_records USING btree (previous_record_id);


--
-- Name: daily_records_site_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX daily_records_site_date_idx ON public.daily_records USING btree (site_id, observation_date DESC);


--
-- Name: daily_records_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX daily_records_status_idx ON public.daily_records USING btree (status) WHERE (status <> 'completed'::text);


--
-- Name: model_versions_dataset_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX model_versions_dataset_idx ON public.model_versions USING btree (gt_dataset_id);


--
-- Name: question_archives_site_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX question_archives_site_date_idx ON public.question_archives USING btree (site_id, asked_at DESC, id);


--
-- Name: schedule_sources_file_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schedule_sources_file_idx ON public.schedule_sources USING btree (file_id);


--
-- Name: schedule_task_locations_location_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schedule_task_locations_location_idx ON public.schedule_task_locations USING btree (location_id);


--
-- Name: schedule_tasks_dates_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schedule_tasks_dates_idx ON public.schedule_tasks USING btree (schedule_id, start, finish);


--
-- Name: schedule_tasks_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schedule_tasks_order_idx ON public.schedule_tasks USING btree (schedule_id, display_order, id);


--
-- Name: schedule_tasks_surface_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schedule_tasks_surface_idx ON public.schedule_tasks USING btree (schedule_id, surface_type, display_order);


--
-- Name: site_memberships_user_site_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX site_memberships_user_site_idx ON public.site_memberships USING btree (user_id, site_id);


--
-- Name: task_events_observation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX task_events_observation_idx ON public.task_events USING btree (observation_id) WHERE (observation_id IS NOT NULL);


--
-- Name: task_events_one_request_application; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX task_events_one_request_application ON public.task_events USING btree (request_id) WHERE (event_type = ANY (ARRAY['schedule_apply'::text, 'work_apply'::text]));


--
-- Name: task_events_one_request_decision; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX task_events_one_request_decision ON public.task_events USING btree (request_id) WHERE (event_type = ANY (ARRAY['request_accept'::text, 'request_reject'::text]));


--
-- Name: task_events_request_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX task_events_request_time_idx ON public.task_events USING btree (request_id, occurred_at, id) WHERE (request_id IS NOT NULL);


--
-- Name: task_events_review_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX task_events_review_target_idx ON public.task_events USING btree (target_event_id) WHERE (target_event_id IS NOT NULL);


--
-- Name: task_events_task_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX task_events_task_time_idx ON public.task_events USING btree (site_id, task_id, occurred_at DESC, id);


--
-- Name: task_events_workflow_delivery_revision; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX task_events_workflow_delivery_revision ON public.task_events USING btree ((((details -> 'row'::text) ->> 'cycle_key'::text)), ((((details -> 'row'::text) ->> 'revision'::text))::integer)) WHERE ((event_type = 'workflow_state'::text) AND ((details ->> 'relation'::text) = 'workflow_vision_deliveries'::text));


--
-- Name: task_events_workflow_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX task_events_workflow_key ON public.task_events USING btree (((details ->> 'relation'::text)), ((details ->> 'key'::text))) WHERE (event_type = 'workflow_state'::text);


--
-- Name: weekly_reports_site_period_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX weekly_reports_site_period_idx ON public.weekly_reports USING btree (site_id, period_start, period_finish);


--
-- Name: change_request_decisions legacy_decision_write; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER legacy_decision_write INSTEAD OF INSERT OR UPDATE ON public.change_request_decisions FOR EACH ROW EXECUTE FUNCTION public.alo_legacy_decision_write();


--
-- Name: schedule_tasks schedule_tasks_lock; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER schedule_tasks_lock BEFORE INSERT OR DELETE OR UPDATE ON public.schedule_tasks FOR EACH ROW EXECUTE FUNCTION public.lock_schedule_tasks();


--
-- Name: schedule_tasks schedule_tasks_validate_links; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER schedule_tasks_validate_links AFTER INSERT OR UPDATE ON public.schedule_tasks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.validate_schedule_task_links();


--
-- Name: task_events task_event_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER task_event_scope BEFORE INSERT OR UPDATE ON public.task_events FOR EACH ROW EXECUTE FUNCTION public.alo_task_event_scope();


--
-- Name: workflow_cycles workflow_view_write; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR DELETE OR UPDATE ON public.workflow_cycles FOR EACH ROW EXECUTE FUNCTION public.alo_workflow_view_write();


--
-- Name: workflow_extension_context workflow_view_write; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR DELETE OR UPDATE ON public.workflow_extension_context FOR EACH ROW EXECUTE FUNCTION public.alo_workflow_view_write();


--
-- Name: workflow_manager_evidence workflow_view_write; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR DELETE OR UPDATE ON public.workflow_manager_evidence FOR EACH ROW EXECUTE FUNCTION public.alo_workflow_view_write();


--
-- Name: workflow_projects workflow_view_write; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR DELETE OR UPDATE ON public.workflow_projects FOR EACH ROW EXECUTE FUNCTION public.alo_workflow_view_write();


--
-- Name: workflow_report_snapshots workflow_view_write; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR DELETE OR UPDATE ON public.workflow_report_snapshots FOR EACH ROW EXECUTE FUNCTION public.alo_workflow_view_write();


--
-- Name: workflow_vision_batches workflow_view_write; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR DELETE OR UPDATE ON public.workflow_vision_batches FOR EACH ROW EXECUTE FUNCTION public.alo_workflow_view_write();


--
-- Name: workflow_vision_deliveries workflow_view_write; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER workflow_view_write INSTEAD OF INSERT OR DELETE OR UPDATE ON public.workflow_vision_deliveries FOR EACH ROW EXECUTE FUNCTION public.alo_workflow_view_write();


--
-- Name: agent_reports agent_reports_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT agent_reports_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id);


--
-- Name: agent_reports agent_reports_site_id_report_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT agent_reports_site_id_report_file_id_fkey FOREIGN KEY (site_id, report_file_id) REFERENCES public.files(site_id, id);


--
-- Name: agent_reports agent_reports_site_id_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT agent_reports_site_id_schedule_id_fkey FOREIGN KEY (site_id, schedule_id) REFERENCES public.schedules(site_id, id);


--
-- Name: change_requests change_requests_schedule_id_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_requests
    ADD CONSTRAINT change_requests_schedule_id_task_id_fkey FOREIGN KEY (schedule_id, task_id) REFERENCES public.schedule_tasks(schedule_id, id);


--
-- Name: change_requests change_requests_site_id_requester_id_from_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_requests
    ADD CONSTRAINT change_requests_site_id_requester_id_from_organization_id_fkey FOREIGN KEY (site_id, requester_id, from_organization_id) REFERENCES public.site_memberships(site_id, user_id, organization_id);


--
-- Name: change_requests change_requests_site_id_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_requests
    ADD CONSTRAINT change_requests_site_id_schedule_id_fkey FOREIGN KEY (site_id, schedule_id) REFERENCES public.schedules(site_id, id);


--
-- Name: change_requests change_requests_to_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_requests
    ADD CONSTRAINT change_requests_to_organization_id_fkey FOREIGN KEY (to_organization_id) REFERENCES public.organizations(id);


--
-- Name: daily_records daily_records_changes_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_changes_file_id_fkey FOREIGN KEY (changes_file_id) REFERENCES public.files(id);


--
-- Name: daily_records daily_records_location_id_previous_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_location_id_previous_record_id_fkey FOREIGN KEY (location_id, previous_record_id) REFERENCES public.daily_records(location_id, id);


--
-- Name: daily_records daily_records_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id);


--
-- Name: daily_records daily_records_photo_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_photo_file_id_fkey FOREIGN KEY (photo_file_id) REFERENCES public.files(id);


--
-- Name: daily_records daily_records_report_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_report_file_id_fkey FOREIGN KEY (report_file_id) REFERENCES public.files(id);


--
-- Name: daily_records daily_records_result_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_result_file_id_fkey FOREIGN KEY (result_file_id) REFERENCES public.files(id);


--
-- Name: daily_records daily_records_site_id_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_site_id_location_id_fkey FOREIGN KEY (site_id, location_id) REFERENCES public.locations(site_id, id);


--
-- Name: daily_records daily_records_site_id_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_records
    ADD CONSTRAINT daily_records_site_id_schedule_id_fkey FOREIGN KEY (site_id, schedule_id) REFERENCES public.schedules(site_id, id);


--
-- Name: files files_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id);


--
-- Name: files files_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: gt_datasets gt_datasets_archive_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gt_datasets
    ADD CONSTRAINT gt_datasets_archive_file_id_fkey FOREIGN KEY (archive_file_id) REFERENCES public.files(id);


--
-- Name: gt_datasets gt_datasets_manifest_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gt_datasets
    ADD CONSTRAINT gt_datasets_manifest_file_id_fkey FOREIGN KEY (manifest_file_id) REFERENCES public.files(id);


--
-- Name: locations locations_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id);


--
-- Name: model_versions model_versions_classes_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_classes_file_id_fkey FOREIGN KEY (classes_file_id) REFERENCES public.files(id);


--
-- Name: model_versions model_versions_config_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_config_file_id_fkey FOREIGN KEY (config_file_id) REFERENCES public.files(id);


--
-- Name: model_versions model_versions_gt_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_gt_dataset_id_fkey FOREIGN KEY (gt_dataset_id) REFERENCES public.gt_datasets(id);


--
-- Name: model_versions model_versions_metrics_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_metrics_file_id_fkey FOREIGN KEY (metrics_file_id) REFERENCES public.files(id);


--
-- Name: model_versions model_versions_weights_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_weights_file_id_fkey FOREIGN KEY (weights_file_id) REFERENCES public.files(id);


--
-- Name: question_archives question_archives_archive_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_archives
    ADD CONSTRAINT question_archives_archive_file_id_fkey FOREIGN KEY (archive_file_id) REFERENCES public.files(id);


--
-- Name: question_archives question_archives_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_archives
    ADD CONSTRAINT question_archives_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id);


--
-- Name: schedule_sources schedule_sources_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sources
    ADD CONSTRAINT schedule_sources_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.files(id);


--
-- Name: schedule_sources schedule_sources_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sources
    ADD CONSTRAINT schedule_sources_schedule_id_fkey FOREIGN KEY (schedule_id) REFERENCES public.schedules(id);


--
-- Name: schedule_task_locations schedule_task_locations_schedule_id_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task_locations
    ADD CONSTRAINT schedule_task_locations_schedule_id_task_id_fkey FOREIGN KEY (schedule_id, task_id) REFERENCES public.schedule_tasks(schedule_id, id);


--
-- Name: schedule_task_locations schedule_task_locations_site_id_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task_locations
    ADD CONSTRAINT schedule_task_locations_site_id_location_id_fkey FOREIGN KEY (site_id, location_id) REFERENCES public.locations(site_id, id);


--
-- Name: schedule_task_locations schedule_task_locations_site_id_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task_locations
    ADD CONSTRAINT schedule_task_locations_site_id_schedule_id_fkey FOREIGN KEY (site_id, schedule_id) REFERENCES public.schedules(site_id, id);


--
-- Name: schedule_tasks schedule_tasks_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_schedule_id_fkey FOREIGN KEY (schedule_id) REFERENCES public.schedules(id);


--
-- Name: schedule_tasks schedule_tasks_schedule_id_predecessor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_schedule_id_predecessor_id_fkey FOREIGN KEY (schedule_id, predecessor_id) REFERENCES public.schedule_tasks(schedule_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: schedule_tasks schedule_tasks_schedule_id_schedule_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_schedule_id_schedule_source_id_fkey FOREIGN KEY (schedule_id, schedule_source_id) REFERENCES public.schedule_sources(schedule_id, id);


--
-- Name: schedule_tasks schedule_tasks_schedule_id_successor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_tasks
    ADD CONSTRAINT schedule_tasks_schedule_id_successor_id_fkey FOREIGN KEY (schedule_id, successor_id) REFERENCES public.schedule_tasks(schedule_id, id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: schedules schedules_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id);


--
-- Name: site_memberships site_memberships_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.site_memberships
    ADD CONSTRAINT site_memberships_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: site_memberships site_memberships_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.site_memberships
    ADD CONSTRAINT site_memberships_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id);


--
-- Name: site_memberships site_memberships_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.site_memberships
    ADD CONSTRAINT site_memberships_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: task_event_files task_event_files_site_id_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_event_files
    ADD CONSTRAINT task_event_files_site_id_event_id_fkey FOREIGN KEY (site_id, event_id) REFERENCES public.task_events(site_id, id);


--
-- Name: task_event_files task_event_files_site_id_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_event_files
    ADD CONSTRAINT task_event_files_site_id_file_id_fkey FOREIGN KEY (site_id, file_id) REFERENCES public.files(site_id, id);


--
-- Name: task_events task_events_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id);


--
-- Name: task_events task_events_schedule_id_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_schedule_id_task_id_fkey FOREIGN KEY (schedule_id, task_id) REFERENCES public.schedule_tasks(schedule_id, id);


--
-- Name: task_events task_events_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id);


--
-- Name: task_events task_events_site_id_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_site_id_schedule_id_fkey FOREIGN KEY (site_id, schedule_id) REFERENCES public.schedules(site_id, id);


--
-- Name: task_events task_events_site_id_schedule_id_task_id_observation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_site_id_schedule_id_task_id_observation_id_fkey FOREIGN KEY (site_id, schedule_id, task_id, observation_id) REFERENCES public.task_observations(site_id, schedule_id, task_id, id);


--
-- Name: task_events task_events_site_id_schedule_id_task_id_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_site_id_schedule_id_task_id_request_id_fkey FOREIGN KEY (site_id, schedule_id, task_id, request_id) REFERENCES public.change_requests(site_id, schedule_id, task_id, id);


--
-- Name: task_events task_events_site_id_target_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_site_id_target_event_id_fkey FOREIGN KEY (site_id, target_event_id) REFERENCES public.task_events(site_id, id);


--
-- Name: task_observations task_observations_schedule_id_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_observations
    ADD CONSTRAINT task_observations_schedule_id_task_id_fkey FOREIGN KEY (schedule_id, task_id) REFERENCES public.schedule_tasks(schedule_id, id);


--
-- Name: task_observations task_observations_site_id_daily_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_observations
    ADD CONSTRAINT task_observations_site_id_daily_record_id_fkey FOREIGN KEY (site_id, daily_record_id) REFERENCES public.daily_records(site_id, id);


--
-- Name: task_observations task_observations_site_id_overlay_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_observations
    ADD CONSTRAINT task_observations_site_id_overlay_file_id_fkey FOREIGN KEY (site_id, overlay_file_id) REFERENCES public.files(site_id, id);


--
-- Name: task_observations task_observations_site_id_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_observations
    ADD CONSTRAINT task_observations_site_id_schedule_id_fkey FOREIGN KEY (site_id, schedule_id) REFERENCES public.schedules(site_id, id);


--
-- Name: weekly_reports weekly_reports_metadata_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weekly_reports
    ADD CONSTRAINT weekly_reports_metadata_file_id_fkey FOREIGN KEY (metadata_file_id) REFERENCES public.files(id);


--
-- Name: weekly_reports weekly_reports_report_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weekly_reports
    ADD CONSTRAINT weekly_reports_report_file_id_fkey FOREIGN KEY (report_file_id) REFERENCES public.files(id);


--
-- Name: weekly_reports weekly_reports_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weekly_reports
    ADD CONSTRAINT weekly_reports_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id);


--
-- Name: weekly_reports weekly_reports_site_id_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weekly_reports
    ADD CONSTRAINT weekly_reports_site_id_schedule_id_fkey FOREIGN KEY (site_id, schedule_id) REFERENCES public.schedules(site_id, id);


--
-- PostgreSQL database dump complete
--


REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
