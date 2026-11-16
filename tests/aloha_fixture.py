"""Disposable SQLite contract fixture; never connects to AWS."""
import hashlib
import io
import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'api'), str(ROOT / 'agent')]
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, JSON, MetaData, String, Table, Uuid, create_engine, insert
from sqlalchemy.pool import StaticPool
from project_app import create_app


def schema():
    meta = MetaData()
    def table(name, spec):
        columns = []
        for key, kind in spec.items():
            default = {} if kind is JSON else None
            columns.append(Column(key, kind, primary_key=key == 'id', default=default))
        return Table(name, meta, *columns)
    table('sites', dict(id=Uuid, code=String, name=String, address=String, timezone=String, description=String,
                       created_at=DateTime, latitude=Float, longitude=Float))
    table('organizations', dict(id=Uuid, name=String, organization_type=String))
    table('users', dict(id=Uuid, auth_provider=String, auth_subject=String, display_name=String, is_active=Boolean))
    table('site_memberships', dict(site_id=Uuid, user_id=Uuid, organization_id=Uuid, role=String, is_active=Boolean))
    table('schedules', dict(id=Uuid, site_id=Uuid, version=String, document_date=Date, description=String, created_at=DateTime))
    table('schedule_sources', dict(id=Uuid, schedule_id=Uuid, file_id=Uuid, display_order=Integer, description=String))
    table('schedule_tasks', dict(id=Uuid, schedule_id=Uuid, schedule_source_id=Uuid, source_task_id=String,
        display_order=Integer, week=String, task_name=String, start=Date, finish=Date, predecessor_id=Uuid,
        successor_id=Uuid, surface_type=String, category_codes=JSON, processing_route=String,
        effective_start=Date, effective_finish=Date))
    table('locations', dict(id=Uuid, site_id=Uuid, code=String, name=String, description=String,
        created_at=DateTime, camera_provider=String, camera_external_id=String, credential_ref=String))
    table('files', dict(id=Uuid, site_id=Uuid, relative_path=String, kind=String, size_bytes=Integer,
        sha256=String, status=String, visibility=String, uploaded_by=Uuid, created_at=DateTime))
    table('schedule_task_locations', dict(site_id=Uuid, schedule_id=Uuid, task_id=Uuid, location_id=Uuid))
    table('daily_records', dict(id=Uuid, site_id=Uuid, location_id=Uuid, observation_date=Date,
        captured_at=DateTime, photo_file_id=Uuid, model_version_id=Uuid, result_file_id=Uuid,
        changes_file_id=Uuid, report_file_id=Uuid, previous_record_id=Uuid, schedule_id=Uuid,
        comparison_rule_version=String, status=String, description=String, error_message=String,
        created_at=DateTime, updated_at=DateTime))
    table('task_observations', dict(id=Uuid, site_id=Uuid, schedule_id=Uuid, task_id=Uuid, daily_record_id=Uuid,
        target_key=String, view_key=String, revision=Integer, installed_ratio=Float, status=String,
        hold_reason=String, overlay_file_id=Uuid, created_at=DateTime))
    table('change_requests', dict(id=Uuid, site_id=Uuid, schedule_id=Uuid, task_id=Uuid, requester_id=Uuid,
        from_organization_id=Uuid, to_organization_id=Uuid, request_type=String, proposed_start=Date,
        proposed_finish=Date, proposed_work=String, reason=String, status=String, created_at=DateTime))
    table('task_events', dict(id=Uuid, site_id=Uuid, schedule_id=Uuid, task_id=Uuid, actor_id=Uuid,
        event_type=String, occurred_at=DateTime, request_id=Uuid, target_event_id=Uuid, observation_id=Uuid,
        body=String, details=JSON))
    table('task_event_files', dict(site_id=Uuid, event_id=Uuid, file_id=Uuid, sort_order=Integer))
    table('agent_reports', dict(id=Uuid, site_id=Uuid, schedule_id=Uuid, period_start=Date, period_finish=Date,
        revision=Integer, status=String, report_file_id=Uuid, generated_at=DateTime,
        approved_by=Uuid, approved_at=DateTime, provenance=JSON))
    # Unused canonical tables are present to detect accidental DDL/view dependencies.
    table('weekly_reports', dict(id=Uuid, site_id=Uuid, schedule_id=Uuid, week=String, period_start=Date,
        period_finish=Date, report_file_id=Uuid, metadata_file_id=Uuid, description=String, created_at=DateTime))
    table('question_archives', dict(id=Uuid, site_id=Uuid, title=String, asked_at=DateTime,
        archive_file_id=Uuid, description=String, created_at=DateTime))
    table('gt_datasets', dict(id=Uuid, name=String, version=String, archive_file_id=Uuid, manifest_file_id=Uuid,
        label_schema_version=String, preprocessing_tool=String, review_status=String, description=String, created_at=DateTime))
    table('model_versions', dict(id=Uuid, model_type=String, version=String, backbone=String, gt_dataset_id=Uuid,
        weights_file_id=Uuid, config_file_id=Uuid, classes_file_id=Uuid, metrics_file_id=Uuid, trained_at=DateTime,
        code_version=String, library_versions=JSON, description=String, created_at=DateTime))
    return meta


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='alo-api-contract-')
        self.root = Path(self.tmp.name)
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.meta = schema(); self.meta.create_all(self.engine)
        self.tables = self.meta.tables
        self.now = datetime(2025, 2, 23, 18, tzinfo=timezone.utc)
        self.start = date(2025, 2, 17)
        self.site, self.other_site, self.schedule, self.location = [uuid4() for _ in range(4)]
        self.org, self.hq_org, self.field, self.hq, self.viewer = [uuid4() for _ in range(5)]
        self.code = 'test-site'
        self.tokens = {key: 'local-fixture-' + key for key in ('field', 'hq', 'viewer', 'vision', 'agent', 'agent2', 'other')}
        identities = []
        for role, user, org in [('field', self.field, self.org), ('hq', self.hq, self.hq_org), ('viewer', self.viewer, self.org)]:
            self.add('users', id=user, display_name=role, auth_provider='test', auth_subject=role, is_active=True)
            self.add('site_memberships', site_id=self.site, user_id=user, organization_id=org, role=role, is_active=True)
            identities.append({'token_sha256': self.hash(self.tokens[role].encode()), 'user_id': str(user), 'organization_id': str(org)})
        for key in ('vision', 'agent', 'agent2', 'other'):
            identities.append({'token_sha256': self.hash(self.tokens[key].encode()), 'kind': 'vision' if key == 'vision' else 'agent',
                               'site_ids': [str(self.other_site if key == 'other' else self.site)]})
        self.add('sites', id=self.site, code=self.code, name='Example project', address='Example address',
                 timezone='America/Los_Angeles', created_at=self.now)
        self.add('sites', id=self.other_site, code='test-other', name='Other site', timezone='America/Los_Angeles', created_at=self.now)
        self.add('organizations', id=self.org, name='Field test', organization_type='partner')
        self.add('organizations', id=self.hq_org, name='HQ test', organization_type='hq')
        self.add('schedules', id=self.schedule, site_id=self.site, version='v001', created_at=self.now)
        self.add('locations', id=self.location, site_id=self.site, code='camera-1', name='Test camera', created_at=self.now,
                 credential_ref='synthetic-camera-reference')
        self.tasks = {}
        for index, (key, title, surface, route) in enumerate([
            ('wall', 'Drywall installation', 'wall', 'ai'), ('floor', 'Floor installation', 'floor', 'ai'),
            ('ceiling', 'Ceiling installation', 'ceiling', 'ai'), ('manager', 'In-Wall Inspection', None, 'field')]):
            tid = uuid4(); self.tasks[key] = tid
            self.add('schedule_tasks', id=tid, schedule_id=self.schedule, schedule_source_id=uuid4(), source_task_id=str(index+1),
                     display_order=index, task_name=title, start=self.start, finish=self.start+timedelta(days=4),
                     surface_type=surface, category_codes=[surface] if surface else ['other'], processing_route=route)
            self.add('schedule_task_locations', site_id=self.site, schedule_id=self.schedule, task_id=tid, location_id=self.location)
        self.photo_ids, self.daily_ids = [], []
        for i in range(5):
            photo = self.photo((80+i*25, 130, 160)); daily = uuid4()
            self.photo_ids.append(photo); self.daily_ids.append(daily)
            self.add('daily_records', id=daily, site_id=self.site, schedule_id=self.schedule, location_id=self.location,
                observation_date=self.start+timedelta(days=i), captured_at=datetime(2025,2,17+i,18,tzinfo=timezone.utc),
                photo_file_id=photo, status='pending', created_at=self.now, updated_at=self.now)
        self.observation_ids = []
        self.app = create_app(engine=self.engine, tables=self.tables, identities=identities, storage_root=self.root,
                              clock=lambda: self.now)
        self.client = TestClient(self.app); self.client.__enter__()
        self.base = f'/api/v1/sites/{self.code}/schedules/{self.schedule}'

    @staticmethod
    def hash(data):
        return hashlib.sha256(data).hexdigest()

    def add(self, table, **row):
        with self.engine.begin() as c:
            c.execute(insert(self.tables[table]).values(**row))

    def photo(self, color=(40,100,160), visibility='site', site=None, code=None):
        buf=io.BytesIO(); Image.new('RGB',(320,180),color).save(buf,format='JPEG')
        return self.file(buf.getvalue(),'photo',visibility,site,code)

    def file(self, data, kind, visibility='site', site=None, code=None):
        fid=uuid4(); path=self.root/'sites'/(code or self.code)/str(fid); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        self.add('files', id=fid, site_id=site or self.site, relative_path=path.relative_to(self.root).as_posix(), kind=kind,
            size_bytes=len(data), sha256=self.hash(data), status='ready', visibility=visibility, uploaded_by=self.field, created_at=self.now)
        return fid

    def request(self, method, path, body=None, actor='hq', **kwargs):
        return self.client.request(method,path,json=body,headers={'Authorization':'Bearer '+self.tokens[actor]},**kwargs)

    def get_input(self):
        response=self.request('GET',self.base+'/report-input?week_start='+str(self.start))
        assert response.status_code==200,response.text
        return response.json()

    def observe(self, key='wall', index=0, value=.5, status='observed', revision=1, overlay=False, **extra):
        oid=uuid4()
        body={'id':str(oid),'daily_record_id':str(self.daily_ids[index]),'target_key':'W1','view_key':'front',
            'revision':revision,'status':status,'installed_ratio':value if status=='observed' else None,
            'hold_reason':None if status=='observed' else 'Occluded', **extra}
        if overlay: body['overlay_file_id']=str(self.photo((180,60,80)))
        r=self.request('POST',self.base+f'/tasks/{self.tasks[key]}/observations',body,'vision')
        assert r.status_code==201,r.text
        self.observation_ids.append(oid)
        return oid

    def deliver(self, ids=None, revision=1):
        return self.request('POST',self.base+'/vision-deliveries',{'id':str(uuid4()),'week_start':str(self.start),
            'revision':revision,'observation_ids':[str(i) for i in (self.observation_ids if ids is None else ids)]},'vision')

    def submit(self, key='manager', progress=75, photos=None, **extra):
        body={'id':str(uuid4()),'photo_file_ids':[str(i) for i in (photos or [self.photo_ids[-1]])],
            'captured_at':'2025-02-21T10:00:00-08:00','progress_percent':progress,'body':'Test manager evidence',**extra}
        r=self.request('POST',self.base+f'/tasks/{self.tasks[key]}/submissions',body,'field')
        assert r.status_code==201,r.text
        return r.json()

    def create(self, mode='draft', rid=None, path='reports'):
        return self.request('POST',self.base+'/'+path,{'id':str(rid or uuid4()),'week_start':str(self.start),'mode':mode},
                            'agent' if path.endswith('/auto') else 'hq')

    def close(self):
        self.client.__exit__(None,None,None); self.engine.dispose(); self.tmp.cleanup()


class WorkerClient:
    def __init__(self, fixture): self.f=fixture
    def get(self,path):
        r=self.f.request('GET',path,actor='agent'); r.raise_for_status(); return r.json()
    def post(self,path,body):
        r=self.f.request('POST',path,body,'agent'); r.raise_for_status(); return r.json()
    def file(self,site,fid):
        r=self.f.request('GET',f'/api/v1/sites/{site}/files/{fid}',actor='agent'); r.raise_for_status(); return r.content
    def upload(self,site,fid,data):
        r=self.f.client.post(f'/api/v1/sites/{site}/files',params={'file_id':fid,'filename':'weekly-report.pdf','purpose':'report'},
            content=data,headers={'Authorization':'Bearer '+self.f.tokens['agent'],'X-Content-SHA256':self.f.hash(data),
                                  'Content-Type':'application/octet-stream'})
        r.raise_for_status(); return r.json()
