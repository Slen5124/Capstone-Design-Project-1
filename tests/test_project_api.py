"""HTTP contracts against canonical table names without any AWS connection."""
import hashlib
import io
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Integer, MetaData,
                        Numeric, String, Table, Uuid, create_engine, insert, select)
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))


def table(metadata, table_name, **columns):
    return Table(table_name, metadata, *(Column(k, v, primary_key=k=='id') for k,v in columns.items()))


@pytest.fixture
def api(tmp_path):
    from project_app import create_app
    m=MetaData()
    table(m,'sites',id=Uuid,code=String,name=String,timezone=String,address=String,description=String,latitude=Numeric,longitude=Numeric,created_at=DateTime(timezone=True))
    table(m,'organizations',id=Uuid,name=String,organization_type=String)
    table(m,'users',id=Uuid,auth_provider=String,auth_subject=String,display_name=String,is_active=Boolean)
    table(m,'site_memberships',site_id=Uuid,user_id=Uuid,organization_id=Uuid,role=String,is_active=Boolean)
    table(m,'schedules',id=Uuid,site_id=Uuid,version=String,document_date=Date,description=String,created_at=DateTime(timezone=True))
    table(m,'schedule_tasks',id=Uuid,schedule_id=Uuid,source_task_id=String,task_name=String,display_order=Integer,start=Date,finish=Date,effective_start=Date,effective_finish=Date,surface_type=String,processing_route=String,category_codes=JSON)
    table(m,'files',id=Uuid,site_id=Uuid,relative_path=String,kind=String,size_bytes=Integer,sha256=String,status=String,visibility=String,uploaded_by=Uuid,created_at=DateTime(timezone=True))
    table(m,'task_observations',id=Uuid,site_id=Uuid,schedule_id=Uuid,task_id=Uuid,daily_record_id=Uuid,target_key=String,view_key=String,revision=Integer,installed_ratio=Numeric,status=String,hold_reason=String,overlay_file_id=Uuid,created_at=DateTime(timezone=True))
    table(m,'schedule_task_locations',site_id=Uuid,schedule_id=Uuid,task_id=Uuid,location_id=Uuid)
    table(m,'daily_records',id=Uuid,site_id=Uuid,location_id=Uuid,photo_file_id=Uuid,observation_date=Date)
    table(m,'task_events',id=Uuid,site_id=Uuid,schedule_id=Uuid,task_id=Uuid,actor_id=Uuid,event_type=String,occurred_at=DateTime(timezone=True),request_id=Uuid,target_event_id=Uuid,observation_id=Uuid,body=String,details=JSON)
    table(m,'task_event_files',site_id=Uuid,event_id=Uuid,file_id=Uuid,sort_order=Integer)
    table(m,'change_requests',id=Uuid,site_id=Uuid,schedule_id=Uuid,task_id=Uuid,requester_id=Uuid,from_organization_id=Uuid,to_organization_id=Uuid,request_type=String,proposed_start=Date,proposed_finish=Date,proposed_work=String,reason=String,status=String,created_at=DateTime(timezone=True))
    table(m,'agent_reports',id=Uuid,site_id=Uuid,schedule_id=Uuid,period_start=Date,period_finish=Date,revision=Integer,status=String,report_file_id=Uuid,generated_at=DateTime(timezone=True),approved_by=Uuid,approved_at=DateTime(timezone=True),provenance=JSON)
    engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
    m.create_all(engine)
    ids={k:uuid4() for k in ['site','other','schedule','task','hq','field','viewer','hqorg','fieldorg','photo','otherphoto','daily','location']}
    identities=[]
    for role in ['hq','field','viewer']:
        identities.append({'token_sha256':hashlib.sha256(('test-'+role).encode()).hexdigest(),'user_id':str(ids[role]),'organization_id':str(ids['hqorg' if role=='hq' else 'fieldorg'])})
    for kind in ['vision','agent']:
        identities.append({'token_sha256':hashlib.sha256(('test-'+kind).encode()).hexdigest(),'kind':kind,'site_ids':[str(ids['site'])]})
    now=datetime(2025,2,20,9,tzinfo=timezone.utc)
    with engine.begin() as c:
        for key,code in [('site','brea'),('other','other')]:
            c.execute(insert(m.tables['sites']).values(id=ids[key],code=code,name=code,timezone='America/Los_Angeles',created_at=now))
        for role in ['hq','field','viewer']:
            org=ids['hqorg' if role=='hq' else 'fieldorg']
            c.execute(insert(m.tables['users']).values(id=ids[role],auth_provider='local',auth_subject=role,display_name=role,is_active=True))
            c.execute(insert(m.tables['site_memberships']).values(site_id=ids['site'],user_id=ids[role],organization_id=org,role=role,is_active=True))
        for key in ['hqorg','fieldorg']:
            c.execute(insert(m.tables['organizations']).values(id=ids[key],name=key,organization_type='partner'))
        c.execute(insert(m.tables['schedules']).values(id=ids['schedule'],site_id=ids['site'],version='v001',document_date=date(2025,1,6),created_at=now))
        c.execute(insert(m.tables['schedule_tasks']).values(id=ids['task'],schedule_id=ids['schedule'],source_task_id='45',task_name='벽체 보드 설치',display_order=1,start=date(2025,2,18),finish=date(2025,2,21),surface_type='wall',processing_route='ai',category_codes=['wall']))
        for key,site in [('photo','site'),('otherphoto','other')]:
            stored=tmp_path / f'sites/{"brea" if site=="site" else "other"}/{key}.jpg'
            stored.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(10,10),'white').save(stored,format='JPEG')
            c.execute(insert(m.tables['files']).values(id=ids[key],site_id=ids[site],relative_path=f'sites/{"brea" if site=="site" else "other"}/{key}.jpg',kind='photo',status='ready',visibility='site',uploaded_by=ids['field'],size_bytes=stored.stat().st_size,sha256=hashlib.sha256(stored.read_bytes()).hexdigest()))
        c.execute(insert(m.tables['schedule_task_locations']).values(site_id=ids['site'],schedule_id=ids['schedule'],task_id=ids['task'],location_id=ids['location']))
        c.execute(insert(m.tables['daily_records']).values(id=ids['daily'],site_id=ids['site'],location_id=ids['location'],photo_file_id=ids['photo'],observation_date=date(2025,2,20)))
    app=create_app(engine=engine,tables=m.tables,identities=identities,storage_root=tmp_path,clock=lambda:now)
    with TestClient(app) as client:
        yield client,ids,engine,m.tables,tmp_path
    engine.dispose()


def headers(role='field'):
    return {'Authorization':'Bearer test-'+role}


def base(ids):
    return '/api/v1/sites/brea/schedules/'+str(ids['schedule'])


def submit(client,ids,**extra):
    body={'id':str(uuid4()),'photo_file_ids':[str(ids['photo'])],'captured_at':'2025-02-20T00:00:00-08:00',**extra}
    return client.post(base(ids)+'/tasks/'+str(ids['task'])+'/submissions',json=body,headers=headers())


def test_auth_membership_and_field_week(api):
    c,ids,_,_,_=api
    assert c.get('/api/v1/sites').status_code==401
    assert c.get('/api/v1/sites',headers=headers()).status_code==200
    assert c.get('/api/v1/sites/other/schedules',headers=headers()).status_code==403
    response=c.get(base(ids)+'/field-home?week_start=2025-02-17',headers=headers())
    assert response.status_code==200,response.text
    assert response.json()['tasks'][0]['source_task_id']=='45'
    assert response.json()['period_finish']=='2025-02-23'


def test_progress_default_partial_validation_and_site_files(api):
    c,ids,_,_,_=api
    assert submit(c,ids,progress_percent=75).status_code==422
    assert submit(c,ids,progress_percent=101).status_code==422
    assert submit(c,ids,photo_file_ids=[str(ids['otherphoto'])]).status_code==404
    result=submit(c,ids)
    assert result.status_code==201,result.text
    assert result.json()['details']['progress_percent']==100
    result=submit(c,ids,progress_percent=75,body='남은 보드 설치 예정')
    assert result.status_code==201,result.text
    assert result.json()['event_type']=='resubmission'
    assert result.json()['details']['progress_percent']==75


def test_automatic_handoff_is_atomic_idempotent_and_unobserved_is_not_zero(api):
    c,ids,engine,t,_=api
    oid=str(uuid4())
    body={'id':oid,'daily_record_id':str(ids['daily']),'target_key':'W-01','view_key':'camera-1','status':'unobserved','hold_reason':'자재 가림'}
    url=base(ids)+'/tasks/'+str(ids['task'])+'/observations'
    for _ in range(2):
        r=c.post(url,json=body,headers=headers('vision'))
        assert r.status_code in [200,201],r.text
    assert r.json()['installed_ratio'] is None
    with engine.connect() as conn:
        rows=conn.execute(select(t['task_events'])).mappings().all()
        assert len([r for r in rows if r['details'].get('kind')=='field_handoff'])==1
    home=c.get(base(ids)+'/field-home?week_start=2025-02-17',headers=headers()).json()
    assert home['tasks'][0]['status']=='field_confirmation_required'
    changed=c.post(url,json={**body,'hold_reason':'다른 사유'},headers=headers('vision'))
    assert changed.status_code==409


def test_review_latest_submission_and_completion_are_distinct(api):
    c,ids,_,_,_=api
    first=submit(c,ids).json()
    latest=submit(c,ids,progress_percent=75,body='잔여 작업').json()
    url=base(ids)+'/tasks/'+str(ids['task'])+'/reviews'
    body={'id':str(uuid4()),'target_event_id':first['id'],'outcome':'complete'}
    assert c.post(url,json=body,headers=headers('hq')).status_code==409
    body.update(id=str(uuid4()),target_event_id=latest['id'])
    assert c.post(url,json=body,headers=headers('hq')).status_code==422
    body.update(id=str(uuid4()),outcome='reject',body='')
    assert c.post(url,json=body,headers=headers('hq')).status_code==422
    body.update(id=str(uuid4()),outcome='accept')
    assert c.post(url,json=body,headers=headers()).status_code==403
    r=c.post(url,json=body,headers=headers('hq'))
    assert r.status_code==201,r.text
    assert r.json()['event_type']=='review_accept'


def test_submission_retries_do_not_duplicate_or_overwrite(api):
    c,ids,engine,t,_=api
    key=str(uuid4())
    for _ in range(2):
        result=submit(c,ids,id=key)
        assert result.status_code in [200,201],result.text
    assert submit(c,ids,id=key,progress_percent=75,body='changed').status_code==409
    with engine.connect() as conn:
        assert len(conn.execute(select(t['task_events']).where(t['task_events'].c.event_type=='submission')).all())==1


def test_unknown_fields_cannot_spoof_actor(api):
    c,ids,_,_,_=api
    assert submit(c,ids,actor_id=str(ids['hq'])).status_code==422
    assert submit(c,ids,progress_percent=True).status_code==422


def change(client, ids, **extra):
    body={'id':str(uuid4()),'recipient_org_id':str(ids['hqorg']),'request_type':'extension',
          'expected_start':'2025-02-18','expected_finish':'2025-02-21',
          'proposed_finish':'2025-02-24','reason':'검사 기관 재방문과 보완 작업에 3일 필요',**extra}
    return client.post(base(ids)+'/tasks/'+str(ids['task'])+'/change-requests',json=body,headers=headers())


def test_request_participants_and_atomic_decision_apply(api):
    c,ids,engine,t,_=api
    request=change(c,ids)
    assert request.status_code==201,request.text
    request_id=request.json()['id']
    url=base(ids)+'/change-requests/'+request_id
    decision={'id':str(uuid4()),'outcome':'accept'}
    assert c.post(url+'/decision',json=decision,headers=headers()).status_code==403
    result=c.post(url+'/decision',json=decision,headers=headers('hq'))
    assert result.status_code==200,result.text
    assert result.json()['status']=='applied'
    assert c.post(url+'/decision',json=decision,headers=headers('hq')).status_code==200
    with engine.connect() as conn:
        row=conn.execute(select(t['schedule_tasks'])).mappings().one()
        assert row['finish']==date(2025,2,21)
        assert row['effective_finish']==date(2025,2,24)
        events=conn.execute(select(t['task_events'])).mappings().all()
        assert len([e for e in events if e['event_type']=='schedule_apply'])==1
    assert c.post(url+'/messages',json={'id':str(uuid4()),'body':'2월 24일 검사 후 보고하겠습니다.'},headers=headers()).status_code==201
    assert c.get(url,headers=headers('viewer')).status_code==403
    assert c.get(url,headers=headers('vision')).status_code==403
    assert c.get(url,headers=headers('agent')).status_code==403


def test_stale_request_does_not_leave_accept_event_or_changed_status(api):
    c,ids,_,_,_=api
    first, second=change(c,ids).json(),change(c,ids).json()
    url=base(ids)+'/change-requests/'
    assert c.post(url+first['id']+'/decision',json={'id':str(uuid4()),'outcome':'accept'},headers=headers('hq')).status_code==200
    result=c.post(url+second['id']+'/decision',json={'id':str(uuid4()),'outcome':'accept'},headers=headers('hq'))
    assert result.status_code==409,result.text
    assert c.get(url+second['id'],headers=headers()).json()['status']=='pending'


def test_report_job_freezes_inputs_and_only_agent_can_finish(api):
    c,ids,_,_,_=api
    submission=submit(c,ids).json()
    review={'id':str(uuid4()),'target_event_id':submission['id'],'outcome':'complete'}
    assert c.post(base(ids)+'/tasks/'+str(ids['task'])+'/reviews',json=review,headers=headers('hq')).status_code==201
    job=c.post(base(ids)+'/reports',json={'id':str(uuid4()),'week_start':'2025-02-17'},headers=headers('hq'))
    assert job.status_code==201,job.text
    data=job.json()
    assert data['status']=='pending'
    contract=c.get('/api/v1/sites/brea/report-jobs/'+data['id']+'/input',headers=headers('agent'))
    assert contract.status_code==200,contract.text
    assert contract.json()['tasks'][0]['latest_submission']['details']['progress_percent']==100
    assert contract.json()['next_week_tasks']==[]
    lease=str(uuid4())
    claimed=c.post('/api/v1/sites/brea/report-jobs/'+data['id']+'/claim',json={'id':lease},headers=headers('agent'))
    assert claimed.status_code==200,claimed.text
    assert c.post('/api/v1/sites/brea/report-jobs/'+data['id']+'/claim',json={'id':str(uuid4())},headers=headers('agent')).status_code==409
    completion={'claim_id':lease,'status':'failed','input_digest':data['input_digest'],'error':'모델 서버 연결 실패'}
    url='/api/v1/sites/brea/report-jobs/'+data['id']+'/result'
    assert c.post(url,json=completion,headers=headers('field')).status_code==403
    assert c.post(url,json=completion,headers=headers('agent')).status_code==200
    assert c.post(url,json=completion,headers=headers('agent')).status_code==200


def test_file_upload_integrity_private_download_and_traversal(api):
    c,ids,_,_,tmp=api
    image=io.BytesIO();Image.new('RGB',(10,10),'white').save(image,format='JPEG');content=image.getvalue()
    fid=str(uuid4());url='/api/v1/sites/brea/files?file_id='+fid+'&filename=wall.jpg&purpose=photo'
    request_headers={**headers(),'Content-Type':'image/jpeg','X-Content-SHA256':hashlib.sha256(content).hexdigest()}
    result=c.post(url,content=content,headers=request_headers)
    assert result.status_code==201,result.text
    assert 'relative_path' not in result.json()
    download=result.json()['download_url']
    assert c.get(download).status_code==401
    assert c.get(download,headers=headers()).content==content
    assert c.post(url,content=content,headers=request_headers).status_code==200
    assert c.post(url.replace('wall.jpg','..%2Fwall.jpg'),content=content,headers=request_headers).status_code==422
    bad_headers={**request_headers,'X-Content-SHA256':'0'*64}
    assert c.post(url.replace(fid,str(uuid4())),content=content,headers=bad_headers).status_code==422
    broken=b'not a photo';bad_headers['X-Content-SHA256']=hashlib.sha256(broken).hexdigest()
    assert c.post(url.replace(fid,str(uuid4())),content=broken,headers=bad_headers).status_code==422


def test_observed_revision_supersedes_handoff_and_cannot_confirm_completion(api):
    c,ids,_,_,_=api
    url=base(ids)+'/tasks/'+str(ids['task'])+'/observations'
    common={'daily_record_id':str(ids['daily']),'target_key':'W-01','view_key':'camera-1'}
    first=c.post(url,json={**common,'id':str(uuid4()),'status':'unobserved','hold_reason':'가림'},headers=headers('vision'))
    assert first.status_code==201,first.text
    current={'id':str(uuid4()),**common,'revision':2,'status':'observed','installed_ratio':0.75}
    result=c.post(url,json=current,headers=headers('vision'))
    assert result.status_code==201,result.text
    state=c.get(base(ids)+'/tasks/'+str(ids['task']),headers=headers('hq')).json()
    assert state['handoffs']==[]
    assert state['status']=='observation_review_pending'
    review_url=base(ids)+'/tasks/'+str(ids['task'])+'/reviews'
    body={'id':str(uuid4()),'observation_id':current['id'],'outcome':'complete'}
    assert c.post(review_url,json=body,headers=headers('hq')).status_code==422
    body.update(id=str(uuid4()),outcome='accept')
    assert c.post(review_url,json=body,headers=headers('hq')).status_code==201
    state=c.get(base(ids)+'/tasks/'+str(ids['task']),headers=headers('hq')).json()
    assert state['status']=='ai_observation_accepted'


def test_disabled_members_and_upload_request_permissions(api):
    c,ids,engine,t,_=api
    from sqlalchemy import update
    with engine.begin() as conn:
        conn.execute(update(t['users']).where(t['users'].c.id==ids['field']).values(is_active=False))
    assert c.get(base(ids)+'/tasks/'+str(ids['task']),headers=headers()).status_code==403
    assert submit(c,ids).status_code==403


def test_report_comparison_excludes_current_week_from_previous_explanation(api):
    c,ids,engine,t,_=api
    from sqlalchemy import update
    with engine.begin() as conn:
        conn.execute(update(t['schedule_tasks']).values(start=date(2025,2,10)))
    assert submit(c,ids,body='이번 주에 새로 제출한 내용').status_code==201
    result=c.get(base(ids)+'/report-input?week_start=2025-02-17',headers=headers('hq'))
    assert result.status_code==200,result.text
    old=result.json()['previous_week'][str(ids['task'])]
    assert old['latest_submission'] is None


def test_request_upload_can_only_be_read_by_participants(api):
    c,ids,_,_,_=api
    data=b'company attachment';fid=str(uuid4())
    result=c.post('/api/v1/sites/brea/files?file_id='+fid+'&filename=notice.txt&purpose=attachment',content=data,
                  headers={**headers(),'X-Content-SHA256':hashlib.sha256(data).hexdigest()})
    assert result.status_code==201,result.text
    url=result.json()['download_url']
    assert c.get(url,headers=headers('hq')).status_code==403
    request=change(c,ids,file_ids=[fid])
    assert request.status_code==201,request.text
    assert c.get(url,headers=headers('hq')).content==data
    assert c.get(url,headers=headers('viewer')).status_code==403
    assert c.get(url,headers=headers('vision')).status_code==403
    assert c.get(url,headers=headers('agent')).status_code==403


def test_vision_contract_uses_task_camera_links_and_no_credentials(api):
    c,ids,_,_,_=api
    result=c.get(base(ids)+'/vision-input?week_start=2025-02-17',headers=headers('vision'))
    assert result.status_code==200,result.text
    assert result.json()['tasks'][0]['photos'][0]['id']==str(ids['daily'])
    assert 'credential_ref' not in result.text


def test_file_type_and_role_failures_leave_no_orphan_files(api):
    c,ids,_,_,tmp=api
    bad=b'executable content';fid=str(uuid4())
    url='/api/v1/sites/brea/files?file_id='+fid+'&filename=bad.exe&purpose=attachment'
    response=c.post(url,content=bad,headers={**headers(),'X-Content-SHA256':hashlib.sha256(bad).hexdigest()})
    assert response.status_code==422
    assert not any('uploads' in f.parts and f.is_file() for f in tmp.rglob('*'))


def test_ai_handoff_must_reference_linked_camera_location(api):
    c,ids,engine,t,_=api
    from sqlalchemy import delete
    with engine.begin() as conn:
        conn.execute(delete(t['schedule_task_locations']))
    body={'id':str(uuid4()),'daily_record_id':str(ids['daily']),'target_key':'W-01','view_key':'camera-1','status':'unobserved','hold_reason':'가림'}
    result=c.post(base(ids)+'/tasks/'+str(ids['task'])+'/observations',json=body,headers=headers('vision'))
    assert result.status_code==422


def test_later_schedule_change_does_not_enter_old_report_next_week_plan(api):
    c,ids,_,_,_=api
    service=c.app.state.project_service
    service.clock=lambda:datetime(2025,3,1,9,tzinfo=timezone.utc)
    request=change(c,ids,proposed_finish='2025-03-03')
    assert request.status_code==201,request.text
    result=c.post(base(ids)+'/change-requests/'+request.json()['id']+'/decision',
                  json={'id':str(uuid4()),'outcome':'accept'},headers=headers('hq'))
    assert result.status_code==200,result.text
    report=c.get(base(ids)+'/report-input?week_start=2025-02-17',headers=headers('hq'))
    assert report.status_code==200,report.text
    assert report.json()['next_week_tasks']==[]


def test_classification_uses_the_canonical_lowercase_codes(api):
    c,ids,_,_,_=api
    url=base(ids)+'/tasks/'+str(ids['task'])+'/classification'
    r=c.patch(url,json={'processing_route':'ai','category_codes':['wall']},headers=headers('hq'))
    assert r.status_code==200,r.text
    assert c.patch(url,json={'processing_route':'ai','category_codes':['fixture']},headers=headers('hq')).status_code==422
    assert c.patch(url,json={'processing_route':'field','category_codes':['wall']},headers=headers()).status_code==403


def test_completion_cannot_confirm_a_missing_proof_photo(api):
    c,ids,_,_,tmp=api
    submitted=submit(c,ids)
    assert submitted.status_code==201,submitted.text
    (tmp/'sites/brea/photo.jpg').unlink()
    url=base(ids)+'/tasks/'+str(ids['task'])+'/reviews'
    result=c.post(url,json={'id':str(uuid4()),'target_event_id':submitted.json()['id'],'outcome':'complete'},headers=headers('hq'))
    assert result.status_code==409,result.text


def test_work_change_from_hq_is_accepted_by_field_and_preserves_original_name(api):
    c,ids,_,_,_=api
    body={'id':str(uuid4()),'recipient_org_id':str(ids['fieldorg']),'request_type':'work',
          'expected_start':'2025-02-18','expected_finish':'2025-02-21','proposed_work':'벽체 보드 이중 시공으로 변경','reason':'설계 변경'}
    request=c.post(base(ids)+'/tasks/'+str(ids['task'])+'/change-requests',json=body,headers=headers('hq'))
    assert request.status_code==201,request.text
    result=c.post(base(ids)+'/change-requests/'+request.json()['id']+'/decision',
                  json={'id':str(uuid4()),'outcome':'accept'},headers=headers())
    assert result.status_code==200,result.text
    task=c.get(base(ids)+'/tasks/'+str(ids['task']),headers=headers()).json()
    assert task['task_name']=='벽체 보드 설치'
    assert task['effective_work']=='벽체 보드 이중 시공으로 변경'


def test_report_artifact_and_headquarters_approval(api):
    c,ids,_,_,_=api
    submission=submit(c,ids).json()
    assert c.post(base(ids)+'/tasks/'+str(ids['task'])+'/reviews',json={
        'id':str(uuid4()),'target_event_id':submission['id'],'outcome':'complete'},headers=headers('hq')).status_code==201
    job=c.post(base(ids)+'/reports',json={'id':str(uuid4()),'week_start':'2025-02-17'},headers=headers('hq')).json()
    claim=str(uuid4());jroot='/api/v1/sites/brea/report-jobs/'+job['id']
    assert c.post(jroot+'/claim',json={'id':claim},headers=headers('agent')).status_code==200
    data=b'%PDF-1.4\nreport artifact fixture';fid=str(uuid4())
    uploaded=c.post('/api/v1/sites/brea/files?file_id='+fid+'&filename=weekly.pdf&purpose=report',content=data,
                    headers={**headers('agent'),'X-Content-SHA256':hashlib.sha256(data).hexdigest()})
    assert uploaded.status_code==201,uploaded.text
    result=c.post(jroot+'/result',json={'claim_id':claim,'status':'validated','input_digest':job['input_digest'],'report_file_id':fid},headers=headers('agent'))
    assert result.status_code==200,result.text
    approved=c.post('/api/v1/sites/brea/reports/'+job['id']+'/decision',json={'id':str(uuid4()),'outcome':'accept'},headers=headers('hq'))
    assert approved.status_code==200,approved.text
    assert approved.json()['status']=='approved'


def test_openapi_exposes_bearer_and_binary_upload_contract(api):
    c,_,_,_,_=api
    spec=c.app.openapi()
    for path, methods in spec['paths'].items():
        if path.startswith('/api/v1/'):
            for operation in methods.values():
                assert {'ProjectBearer':[]} in operation['security'],path
    upload=spec['paths']['/api/v1/sites/{site_code}/files']['post']
    assert 'application/octet-stream' in upload['requestBody']['content']


def test_stale_observation_review_is_rejected(api):
    c,ids,_,_,_=api
    url=base(ids)+'/tasks/'+str(ids['task'])
    common={'daily_record_id':str(ids['daily']),'target_key':'W-01','view_key':'camera-1','status':'observed','installed_ratio':0.5}
    old_id=str(uuid4())
    assert c.post(url+'/observations',json={**common,'id':old_id},headers=headers('vision')).status_code==201
    assert c.post(url+'/observations',json={**common,'id':str(uuid4()),'revision':2},headers=headers('vision')).status_code==201
    assert c.post(url+'/reviews',json={'id':str(uuid4()),'observation_id':old_id,'outcome':'accept'},headers=headers('hq')).status_code==409


def test_mutated_file_bytes_cannot_be_confirmed(api):
    c,ids,_,_,tmp=api
    submitted=submit(c,ids).json()
    path=tmp/'sites/brea/photo.jpg';content=bytearray(path.read_bytes());content[-5]^=1;path.write_bytes(content)
    r=c.post(base(ids)+'/tasks/'+str(ids['task'])+'/reviews',json={
        'id':str(uuid4()),'target_event_id':submitted['id'],'outcome':'complete'},headers=headers('hq'))
    assert r.status_code==409,r.text


def test_expired_report_lease_rejects_result_and_can_be_reclaimed(api):
    c,ids,_,_,_=api
    service=c.app.state.project_service
    job=c.post(base(ids)+'/reports',json={'id':str(uuid4()),'week_start':'2025-02-17'},headers=headers('hq')).json()
    url='/api/v1/sites/brea/report-jobs/'+job['id'];claim=str(uuid4())
    assert c.post(url+'/claim',json={'id':claim},headers=headers('agent')).status_code==200
    service.clock=lambda:datetime(2025,2,20,9,16,tzinfo=timezone.utc)
    result=c.post(url+'/result',json={'claim_id':claim,'status':'failed','input_digest':job['input_digest'],'error':'실행 실패'},headers=headers('agent'))
    assert result.status_code==409,result.text
    assert c.post(url+'/claim',json={'id':str(uuid4())},headers=headers('agent')).status_code==200


def test_classification_derives_primary_surface_for_vision_and_rejects_duplicates(api):
    c,ids,engine,t,_=api
    from sqlalchemy import update
    with engine.begin() as conn:
        conn.execute(update(t['schedule_tasks']).values(surface_type=None))
    url=base(ids)+'/tasks/'+str(ids['task'])+'/classification'
    result=c.patch(url,json={'processing_route':'ai','category_codes':['wall']},headers=headers('hq'))
    assert result.status_code==200,result.text
    assert result.json()['surface_type']=='wall'
    assert c.patch(url,json={'processing_route':'ai','category_codes':['wall','wall']},headers=headers('hq')).status_code==422


def test_overview_and_request_recipients_are_site_scoped(api):
    c,ids,_,_,_=api
    r=c.get('/api/v1/sites/brea/overview',headers=headers())
    assert r.status_code==200,r.text
    assert r.json()['total_tasks']==1
    recipients=c.get('/api/v1/sites/brea/participants',headers=headers())
    assert recipients.status_code==200,recipients.text
    assert recipients.json()==[{'id':str(ids['hqorg']),'name':'hqorg'}]
    assert c.get('/api/v1/sites/brea/participants',headers=headers('viewer')).status_code==403


def test_old_photo_does_not_satisfy_this_week_ai_evidence(api):
    c,ids,engine,t,_=api
    from sqlalchemy import update
    with engine.begin() as conn:
        conn.execute(update(t['schedule_tasks']).values(start=date(2025,2,10)))
        conn.execute(update(t['daily_records']).values(observation_date=date(2025,2,14)))
    oid=str(uuid4());root=base(ids)+'/tasks/'+str(ids['task'])
    r=c.post(root+'/observations',json={'id':oid,'daily_record_id':str(ids['daily']),'target_key':'W-01','view_key':'camera-1','status':'observed','installed_ratio':0.75},headers=headers('vision'))
    assert r.status_code==201,r.text
    assert c.post(root+'/reviews',json={'id':str(uuid4()),'observation_id':oid,'outcome':'accept'},headers=headers('hq')).status_code==201
    report=c.get(base(ids)+'/report-input?week_start=2025-02-17',headers=headers('hq'))
    assert report.status_code==200,report.text
    assert report.json()['ready_for_approval'] is False
    assert report.json()['tasks'][0]['status']=='ai_observing'
