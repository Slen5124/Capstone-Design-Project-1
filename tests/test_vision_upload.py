from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/llm_agent_module/vision_delivery'))
from vision_upload import VisionClient,submit

TASK='11111111-1111-4111-8111-111111111111'
PHOTO='22222222-2222-4222-8222-222222222222'
FILE='33333333-3333-4333-8333-333333333333'
BATCH='44444444-4444-4444-8444-444444444444'


def fixture():
    task={'task_id':TASK,'source_task_id':'45','task_name':'Hang Drywall','planned_start':'2031-02-18','planned_finish':'2031-02-24'}
    contract={'site_code':'sample-site','schedule_version':'v001','week':7,'period_start':'2031-02-17','period_end':'2031-02-21','tasks':[task],'schedule_tasks':[task]}
    delivery={k:contract[k] for k in ['site_code','schedule_version','week','period_start','period_end']};delivery.update(revision=1,batch_id=BATCH,status='completed')
    raw={'schema_version':'vision_output/1.0','site_id':'SAMPLE-SITE','model':{'source':'model','model_version':'seg-v1'},'delivery':delivery,'targets':[{'target_id':'W01','surface_type':'wall','name':'Left wall','schedule_link':{'wall_board_installation':{'source_task_id':'45'}}}],'views':[{'view_id':'V01','targets':[{'target_id':'W01'}]}],'daily_progress':[{'site_id':'SAMPLE-SITE','date':'2031-02-21','target_id':'W01','view_ids':['V01'],'activity_id':'wall_board_installation','source':'model','status':'ok','provisional':False,'revision':1,'installed_ratio':0.5,'photo_file_id':PHOTO}]}
    raw['daily_progress'][0]['box']=[0.02,0.1,0.45,0.9]
    return contract,raw


class FakeClient:
    def __init__(self,contract,raw=None):self.contract=contract;self.raw=raw;self.calls=[]
    def json(self,path,body=None):
        self.calls.append(('GET' if body is None else 'POST',path,body))
        return self.contract if body is None else {'status':'completed','source_json_file_id':body['result_file_id']}
    def request(self,path,data=None):
        self.calls.append(('GET' if data is None else 'POST',path,data));return self.raw
    def publish_raw(self,path,raw):
        self.calls.append(('POST',path,raw));return {'status':'completed'}


class VisionUploadTests(unittest.TestCase):
    def test_remote_credentials_require_https_and_origin(self):
        synthetic_userinfo = 'https://' + 'synthetic:fixture' + '@example.invalid'
        for base in ['http://example.invalid', 'https://example.invalid/unexpected', synthetic_userinfo]:
            with self.subTest(base=base), self.assertRaises(ValueError):
                VisionClient(base=base, token='synthetic-fixture')

    def setUp(self):
        self.contract,self.payload=fixture()
        scratch=Path(__file__).resolve().parents[1]/'upload_test_outputs';scratch.mkdir(exist_ok=True)
        self.directory=tempfile.TemporaryDirectory(dir=scratch);self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'vision.json';self.write()
    def write(self):self.path.write_text(json.dumps(self.payload,indent=2),encoding='utf-8')
    def test_contract_uses_vision_read_path_without_mutation(self):
        c=FakeClient(self.contract);self.assertEqual(submit(site='sample-site',week=7,client=c,contract_only=True),self.contract)
        self.assertEqual(c.calls,[('GET','/workflow/sites/sample-site/weeks/7/vision-contract',None)])
    def test_dry_run_validates_without_upload_or_completion(self):
        c=FakeClient(self.contract);result=submit(site='sample-site',week=7,client=c,input_path=self.path,dry_run=True)
        self.assertEqual(result['task_ids'],[TASK]);self.assertEqual(result['observation_count'],1)
        self.assertEqual([x[0] for x in c.calls],['GET'])
    def test_final_posts_exact_original_once(self):
        c=FakeClient(self.contract);submit(site='sample-site',week=7,client=c,input_path=self.path)
        posts=[x for x in c.calls if x[0]=='POST'];self.assertEqual(len(posts),1)
        self.assertEqual(posts[0][1],'/workflow/sites/sample-site/weeks/7/vision-results');self.assertEqual(posts[0][2],self.path.read_bytes())
    def test_stored_file_validated_before_import_and_dryrun_is_readonly(self):
        for dry in [True,False]:
            c=FakeClient(self.contract,self.path.read_bytes());submit(site='sample-site',week=7,client=c,stored_file_id=FILE,dry_run=dry)
            self.assertEqual(c.calls[1],('GET','/files/'+FILE+'/download',None))
            posts=[x for x in c.calls if x[0]=='POST'];self.assertEqual(len(posts),0 if dry else 1)
            if posts:self.assertEqual(posts[0],('POST','/workflow/sites/sample-site/weeks/7/vision-imports',{'result_file_id':FILE}))
    def test_label_and_invalid_media_do_not_publish(self):
        for mutation in ['label','media']:
            p=deepcopy(self.payload)
            if mutation=='label':p['model']['source']='label';p['daily_progress'][0]['source']='label'
            else:p['daily_progress'][0]['photo_file_id']='overlays/SAMPLE-SITE/photo.jpg'
            self.path.write_text(json.dumps(p),encoding='utf-8');c=FakeClient(self.contract)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):submit(site='sample-site',week=7,client=c,input_path=self.path)
            self.assertFalse(any(x[0]=='POST' for x in c.calls))
    def test_mismatched_contract_and_nonfinite_values_do_not_publish(self):
        wrong={**self.contract,'week':8};c=FakeClient(wrong)
        with self.assertRaises(ValueError):submit(site='sample-site',week=7,client=c,input_path=self.path)
        self.payload['daily_progress'][0]['installed_ratio']=float('nan');self.write();c=FakeClient(self.contract)
        with self.assertRaises(ValueError):submit(site='sample-site',week=7,client=c,input_path=self.path)
        self.assertFalse(any(x[0]=='POST' for x in c.calls))
    def test_manager_secret_does_not_substitute_for_vision_secret(self):
        with patch.dict(os.environ,{'ALO_WORKFLOW_MANAGER_TOKEN':'manager-only'},clear=True):
            with self.assertRaisesRegex(ValueError,'ALO_WORKFLOW_VISION_TOKEN'):VisionClient()
    def test_vision_token_is_used_for_contract_read_without_manager(self):
        class Opener:
            def open(self,request,timeout):
                self.request=request;return io.BytesIO(b'{}')
        opener=Opener()
        with patch.dict(os.environ,{'ALO_WORKFLOW_VISION_TOKEN':'vision-only','ALO_API_BASE':'http://127.0.0.1:8000'},clear=True):
            client=VisionClient(opener=opener);client.json('/workflow/sites/sample-site/weeks/7/vision-contract')
        self.assertEqual(opener.request.headers['Authorization'],'Bearer vision-only')
    def test_conflicting_modes_rejected_before_request(self):
        c=FakeClient(self.contract)
        with self.assertRaises(ValueError):submit(site='sample-site',week=7,client=c,input_path=self.path,stored_file_id=FILE)
        self.assertEqual(c.calls,[])
    def test_team_template_cannot_be_published_as_real_vision(self):
        template=self.path
        payload=deepcopy(self.payload)
        payload['is_test']=True
        template.write_text(json.dumps(payload),encoding='utf-8')
        c=FakeClient(self.contract)
        with self.assertRaises(ValueError):submit(site='sample-site',week=7,client=c,input_path=template)
        self.assertFalse(any(x[0]=='POST' for x in c.calls))
    def test_duplicate_metadata_keys_rejected_before_final_or_dryrun(self):
        raw=json.dumps(self.payload)[:-1]+',"metadata":{"revision":1,"revision":2}}'
        self.path.write_text(raw,encoding='utf-8')
        for dry in [True,False]:
            c=FakeClient(self.contract)
            with self.subTest(dry_run=dry),self.assertRaisesRegex(ValueError,'duplicate object key'):
                submit(site='sample-site',week=7,client=c,input_path=self.path,dry_run=dry)
            self.assertFalse(any(x[0]=='POST' for x in c.calls))
    def test_overflow_in_metadata_is_not_accepted_by_dryrun(self):
        raw=json.dumps(self.payload)[:-1]+',"metadata":{"score":1e999}}'
        self.path.write_text(raw,encoding='utf-8');c=FakeClient(self.contract)
        with self.assertRaisesRegex(ValueError,'non-finite'):
            submit(site='sample-site',week=7,client=c,input_path=self.path,dry_run=True)
        self.assertFalse(any(x[0]=='POST' for x in c.calls))


if __name__=='__main__':unittest.main()
