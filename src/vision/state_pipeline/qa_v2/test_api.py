"""Local review API integration, using fictional ZIP data only."""
import argparse
import contextlib
import hashlib
import io
import json
import queue
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image
from qa_v2 import atomic_json, batch


class ReviewApiTests(unittest.TestCase):
    def test_local_review_correction_reload_and_source_preservation(self):
        temp_root=Path(__file__).resolve().parent.parent/'tmp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as name:
            root=Path(name);workspace=root/'workspace';workspace.mkdir()
            image=Image.new('RGB',(100,80),'gray');stream=io.BytesIO();image.save(stream,format='PNG')
            photos=root/'photos.zip';annotations=root/'annotations.zip'
            shape={'label':'wall','shape_type':'rectangle','points':[[5,5],[90,70]],'flags':{},'group_id':None}
            doc={'imagePath':'test.png','imageWidth':100,'imageHeight':80,'shapes':[shape,shape,dict(shape,label='ceiling')]}
            with zipfile.ZipFile(photos,'w') as z:z.writestr('test.png',stream.getvalue())
            with zipfile.ZipFile(annotations,'w') as z:z.writestr('test.json',json.dumps(doc))
            atomic_json(workspace/'project.json',{'photos':[str(photos)],'json_zip':str(annotations),'references':[]})
            before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in [photos,annotations]]
            args=argparse.Namespace(workspace=workspace,out=root/'runs',limit=0,entries_file=None,check_images=True,hash_duplicates=False,write_corrections=False,allow_ai_references=False,raster_diagnostics=False)
            with contextlib.redirect_stdout(io.StringIO()):batch(args)
            proc=subprocess.Popen([sys.executable,str(Path(__file__).with_name('qa_v2.py')),'serve','--workspace',str(workspace),'--port','0'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
            lines=queue.Queue()
            def reader():
                for line in proc.stdout:lines.put(line)
            threading.Thread(target=reader,daemon=True).start()
            try:
                address=lines.get(timeout=20).strip().split('Local review: ',1)[1].rstrip('/')
                def request(route,body=None,headers=None):
                    payload=None if body is None else json.dumps(body).encode()
                    h={'Content-Type':'application/json'};h.update(headers or {})
                    with urlopen(Request(address+route,data=payload,headers=h),timeout=5) as response:
                        return response.read(),response.headers
                html,_=request('/')
                token=re.search(r"['\"]X-QA-Token['\"]\s*:\s*['\"]([^'\"]+)",html.decode()).group(1)
                self.assertNotIn('__QA_TOKEN__',html.decode())
                state=json.loads(request('/api/state')[0]);record=state['results'][0]
                self.assertEqual(len(record['duplicate_groups']),3)
                self.assertTrue(any(g['kind']=='cross_label_overlap' for g in record['duplicate_groups']))
                self.assertFalse(record['correction_file'])
                self.assertEqual(len(json.loads(request('/api/search?q=test')[0])),1)
                original=json.loads(request('/api/annotation?'+urlencode({'zip':str(annotations),'entry':'test.json'}))[0])
                self.assertEqual(len(original['shapes']),3)
                pixels=request('/api/image?'+urlencode({'zip':str(photos),'entry':'test.png'}))[0]
                self.assertEqual(Image.open(io.BytesIO(pixels)).size,(100,80))
                with self.assertRaises(HTTPError) as denied:request('/api/review',{'result_id':record['id'],'decision':'needs_review'})
                self.assertEqual(denied.exception.code,403)
                with self.assertRaises(HTTPError) as denied:request('/api/state',headers={'Host':'untrusted.invalid'})
                self.assertEqual(denied.exception.code,403)
                headers={'X-QA-Token':token}
                self.assertTrue(json.loads(request('/api/review',{'result_id':record['id'],'decision':'needs_review'},headers)[0])['saved'])
                body={'result_id':record['id'],'keep_shape_index':0,'remove_shape_indices':[1],'reviewer':'fictional API test','confirmed_same_object':True}
                run_file=Path(json.loads((workspace/'latest_run.json').read_text(encoding='utf-8'))['path'])/'results.json'
                audited=json.loads(run_file.read_text(encoding='utf-8'));source_sha=audited['results'][0]['source_json_sha256']
                audited['results'][0]['source_json_sha256']='0'*64;atomic_json(run_file,audited)
                with self.assertRaises(HTTPError) as stale:request('/api/correct',body,headers)
                self.assertIn('differs from audited bytes',stale.exception.read().decode())
                audited['results'][0]['source_json_sha256']=source_sha;atomic_json(run_file,audited)
                response=json.loads(request('/api/correct',body,headers)[0])
                correction=json.loads(request(response['url'])[0])
                self.assertEqual(len(correction['shapes']),2)
                reloaded=json.loads(request('/api/state')[0])['results'][0]
                self.assertEqual(reloaded['correction_file'],response['correction_file'])
                self.assertEqual(reloaded['human_reviewed_removed_source_indices'],[1])
                cross_label=dict(body,remove_shape_indices=[2])
                response=json.loads(request('/api/correct',cross_label,headers)[0])
                correction=json.loads(request(response['url'])[0])
                self.assertEqual(len(correction['shapes']),1)
                self.assertEqual(correction['shapes'][0]['label'],'wall')
                self.assertEqual(json.loads(request('/api/state')[0])['results'][0]['human_reviewed_removed_source_indices'],[1,2])
                invalid=dict(body,keep_shape_index=1,remove_shape_indices=[0])
                with self.assertRaises(HTTPError):request('/api/correct',invalid,headers)
                ref={'reviewed':True,'reviewer':'fictional API test','source':{'zip':str(photos),'entry':'test.png'},'regions':[{'id':'R0','label':'wall','points':[[10,10],[80,10],[80,60],[10,60]]}]}
                saved=json.loads(request('/api/reference',ref,headers)[0])
                self.assertTrue(saved['human_confirmed']);self.assertEqual(saved['reference_origin'],'human_reviewed')
                self.assertEqual(len(json.loads(request('/api/state')[0])['references']),1)
                self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in [photos,annotations]],before)
            finally:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
                proc.stdout.close()


if __name__=='__main__':unittest.main()
