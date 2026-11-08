-- Run only on the restored verification database. Fixtures always roll back.
BEGIN;
SET LOCAL ROLE alo_app;
DO $$
DECLARE s uuid; sc uuid; task uuid; other_site uuid; hq_org uuid; field_org uuid;
    hq uuid; field_user uuid; evidence uuid; foreign_file uuid; request uuid;
BEGIN
    IF (SELECT count(*) FROM pg_tables WHERE schemaname='public')<>20 THEN
        RAISE EXCEPTION 'Expected exactly 20 base tables';
    END IF;
    SELECT schedule.site_id,schedule.id,t.id INTO STRICT s,sc,task
    FROM schedules schedule JOIN schedule_tasks t ON t.schedule_id=schedule.id LIMIT 1;
    INSERT INTO sites(code,name,timezone) VALUES('db20-test-site','DB20 fixture','UTC') RETURNING id INTO other_site;
    INSERT INTO organizations(name) VALUES('HQ fixture') RETURNING id INTO hq_org;
    INSERT INTO organizations(name) VALUES('Field fixture') RETURNING id INTO field_org;
    INSERT INTO users(auth_subject,display_name) VALUES('db20-hq','HQ') RETURNING id INTO hq;
    INSERT INTO users(auth_subject,display_name) VALUES('db20-field','Field') RETURNING id INTO field_user;
    INSERT INTO site_memberships(site_id,user_id,organization_id,role) VALUES(s,hq,hq_org,'hq'),(s,field_user,field_org,'field');
    INSERT INTO task_events(site_id,schedule_id,task_id,actor_id,event_type,details)
        VALUES(s,sc,task,field_user,'submission','{"completion_requested":true}') RETURNING id INTO evidence;
    IF NOT EXISTS (SELECT 1 FROM task_review_queue WHERE item_id=evidence AND status='completion_requested') THEN
        RAISE EXCEPTION 'Submitted evidence is missing from the review queue';
    END IF;
    BEGIN
        INSERT INTO task_events(site_id,schedule_id,task_id,actor_id,event_type,target_event_id)
            VALUES(s,sc,task,field_user,'review_accept',evidence);
        RAISE EXCEPTION 'Field user was allowed to review' USING ERRCODE='ZX001';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    INSERT INTO task_events(site_id,schedule_id,task_id,actor_id,event_type,target_event_id)
        VALUES(s,sc,task,hq,'completion_confirm',evidence);
    IF EXISTS (SELECT 1 FROM task_review_queue WHERE item_id=evidence) THEN
        RAISE EXCEPTION 'Completed evidence remains pending';
    END IF;
    INSERT INTO files(site_id,relative_path,kind) VALUES(other_site,'db20-fixture/other.txt','file') RETURNING id INTO foreign_file;
    BEGIN
        INSERT INTO task_event_files(site_id,event_id,file_id) VALUES(s,evidence,foreign_file);
        RAISE EXCEPTION 'Cross-site attachment was accepted' USING ERRCODE='ZX001';
    EXCEPTION WHEN foreign_key_violation THEN NULL; END;
    BEGIN
        INSERT INTO task_events(site_id,schedule_id,task_id,actor_id,event_type,target_event_id,body)
            VALUES(s,sc,task,hq,'review_reject',evidence,'');
        RAISE EXCEPTION 'Empty rejection reason was accepted' USING ERRCODE='ZX001';
    EXCEPTION WHEN check_violation THEN NULL; END;
    INSERT INTO change_requests(site_id,schedule_id,task_id,requester_id,from_organization_id,to_organization_id,
        request_type,reason,proposed_finish)
        SELECT s,sc,task,field_user,field_org,hq_org,'extension','Fixture extension',finish+7
        FROM schedule_tasks WHERE id=task RETURNING id INTO request;
    BEGIN
        INSERT INTO task_events(site_id,schedule_id,task_id,actor_id,event_type,request_id)
            VALUES(s,sc,task,field_user,'request_accept',request);
        RAISE EXCEPTION 'Sender decided its own request' USING ERRCODE='ZX001';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    INSERT INTO task_events(site_id,schedule_id,task_id,actor_id,event_type,request_id)
        VALUES(s,sc,task,hq,'request_accept',request);
    IF (SELECT status FROM change_requests WHERE id=request)<>'accepted' THEN RAISE EXCEPTION 'Request status did not commit with decision'; END IF;
    BEGIN
        INSERT INTO task_events(site_id,schedule_id,task_id,actor_id,event_type,request_id)
            VALUES(s,sc,task,hq,'request_accept',request);
        RAISE EXCEPTION 'Duplicate decision accepted' USING ERRCODE='ZX001';
    EXCEPTION WHEN raise_exception THEN NULL; END;
    INSERT INTO task_events(site_id,schedule_id,task_id,actor_id,event_type,request_id)
        VALUES(s,sc,task,hq,'schedule_apply',request);
    IF (SELECT effective_finish<>finish+7 FROM schedule_tasks WHERE id=task)
        OR (SELECT status FROM change_requests WHERE id=request)<>'applied' THEN
        RAISE EXCEPTION 'Accepted schedule did not apply atomically';
    END IF;
END $$;
ROLLBACK;
