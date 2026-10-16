"""Integration checks for useful resume/range behavior, not annotation accuracy."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image
import auto_preprocess as app
from dataset_io import ExportDataset
from operations_control import RunLock

class OperationsTests(unittest.TestCase):
    def setUp(self):
        temp=Path(__file__).resolve().parent.parent/'tmp';temp.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=temp);self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.photos=self.root/'photos.zip';self.jsons=self.root/'annotations.zip'
        stream=io.BytesIO();Image.new('RGB',(100,80),'gray').save(stream,format='PNG');pixels=stream.getvalue()
        self.names=[f'{n}.png' for n in range(6)]
        with zipfile.ZipFile(self.photos,'w') as z:
            for name in self.names:z.writestr(name,pixels)
        with zipfile.ZipFile(self.jsons,'w') as z:
            for name in self.names:z.writestr(name.replace('.png','.json'),json.dumps({'imagePath':name,'imageWidth':100,'imageHeight':80,'shapes':[]}))
        teacher={'id':'T','source':{'zip':str(self.photos),'entry':'0.png','sha256':hashlib.sha256(pixels).hexdigest()},'size':[100,80],
                 'reference_origin':'assistant_authorized_surface_draft','regions':[{'id':'W','label':'wall','role':'complete_surface','points':[[5,5],[90,5],[90,70],[5,70]]}]}
        app.atomic_json(self.workspace/'project.json',{'photos':[str(self.photos)],'json_zip':str(self.jsons),'references':[teacher]})
    def tearDown(self):self.temp.cleanup()
    def args(self,**kwargs):
        values=dict(workspace=self.workspace,out=self.root/'out',limit=0,start=0,workers=1,max_references=2,layout='zip',min_free_mb=0,
                    entries_file=None,resume=None,checkpoint_every=2,progress_seconds=.1,max_minutes=0,stop_after=0)
        values.update(kwargs);return argparse.Namespace(**values)
    def run_quiet(self,**kwargs):
        with contextlib.redirect_stdout(io.StringIO()):return app.run(self.args(**kwargs))
    def test_pause_resume_processes_only_unfinished_photos_and_reuses_output(self):
        source_hash=[hashlib.sha256(p.read_bytes()).hexdigest() for p in [self.photos,self.jsons]]
        run=self.run_quiet(stop_after=3)
        first=json.loads((run/'summary.json').read_text());self.assertEqual(first['status'],'paused');self.assertEqual(first['completed_photos'],3)
        old_archives={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (run/'chunks').rglob('*.zip')}
        with patch.object(app,'process_photo',wraps=app.process_photo) as process:
            resumed=self.run_quiet(resume=run)
            self.assertEqual(process.call_count,3)
        self.assertEqual(resumed,run)
        self.assertEqual(old_archives,{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in old_archives})
        final=json.loads((run/'summary.json').read_text());self.assertEqual(final['status'],'complete');self.assertEqual(final['completed_photos'],6)
        records=[json.loads(line) for line in (run/'manifest.jsonl').read_text().splitlines()]
        self.assertEqual(len(records),6);self.assertEqual(len({r['id'] for r in records}),6)
        with ExportDataset(run) as dataset:self.assertEqual(len(dataset),6);self.assertEqual(dataset[5][0].size,(100,80))
        self.assertEqual(source_hash,[hashlib.sha256(p.read_bytes()).hexdigest() for p in [self.photos,self.jsons]])
        with patch.object(app,'process_photo',wraps=app.process_photo) as process:self.run_quiet(resume=run);self.assertEqual(process.call_count,0)
    def test_reference_change_rejects_resume_without_changing_saved_results(self):
        run=self.run_quiet(stop_after=2);before=(run/'manifest.jsonl').read_bytes()
        config=app.read_json(self.workspace/'project.json');config['references'][0]['regions'][0]['points'][0]=[6,5]
        app.atomic_json(self.workspace/'project.json',config)
        with self.assertRaisesRegex(ValueError,'RESUME_INPUT_OR_CODE_CHANGED'):self.run_quiet(resume=run)
        self.assertEqual(before,(run/'manifest.jsonl').read_bytes())
    def test_stable_start_range_is_disjoint(self):
        first=self.run_quiet(start=0,limit=2);second=self.run_quiet(start=2,limit=2)
        entries=lambda folder:{json.loads(s)['source_photo']['entry'] for s in (folder/'manifest.jsonl').read_text().splitlines()}
        self.assertEqual(entries(first),{'0.png','1.png'});self.assertEqual(entries(second),{'2.png','3.png'})
    def test_time_budget_and_disk_guard_pause_without_false_completion(self):
        run=self.run_quiet(max_minutes=.00000001)
        summary=app.read_json(run/'summary.json');self.assertEqual(summary['status'],'paused');self.assertEqual(summary['completed_photos'],0)
        self.assertEqual(summary['stop_reason'],'TIME_BUDGET_REACHED')
        run=self.run_quiet(min_free_mb=10**10)
        summary=app.read_json(run/'summary.json');self.assertEqual(summary['status'],'paused');self.assertEqual(summary['stop_reason'],'DISK_SPACE_RESERVE_REACHED')
    def test_stop_file_is_acknowledged_and_resume_finishes(self):
        run=self.run_quiet(stop_after=2);(run/'STOP').write_text('stop',encoding='utf-8')
        self.run_quiet(resume=run)
        self.assertEqual(app.read_json(run/'summary.json')['status'],'complete');self.assertFalse((run/'STOP').exists())
        self.assertTrue(list(run.glob('STOP.acknowledged_*')))
    def test_run_lock_prevents_simultaneous_writer_and_releases(self):
        path=self.root/'test.lock'
        with RunLock(path):
            with self.assertRaisesRegex(RuntimeError,'ALREADY_ACTIVE'):
                with RunLock(path):pass
        with RunLock(path):pass
    def test_configuration_change_during_dataset_startup_is_rejected(self):
        real_dataset=app.Dataset
        def factory(*args,**kwargs):
            result=real_dataset(*args,**kwargs)
            config=app.read_json(self.workspace/'project.json');config['revision']='changed while waiting for password'
            app.atomic_json(self.workspace/'project.json',config)
            return result
        with patch.object(app,'Dataset',side_effect=factory):
            with self.assertRaisesRegex(ValueError,'REFERENCE_CONFIG_CHANGED_DURING_STARTUP'):self.run_quiet()
    def test_stop_during_slow_worker_records_stopping_and_reprocesses_on_resume(self):
        original=app.process_photo
        def slow(*args,**kwargs):
            pointer=app.read_json(self.root/'out/latest_active_run.json')
            (Path(pointer['path'])/'STOP').write_text('stop',encoding='utf-8')
            time.sleep(.4)
            return original(*args,**kwargs)
        with patch.object(app,'process_photo',side_effect=slow):run=self.run_quiet()
        state=app.read_json(run/'summary.json')
        self.assertEqual(state['status'],'paused');self.assertEqual(state['completed_photos'],0)
        self.assertIn('STOPPING',(run/'console.log').read_text(encoding='utf-8'))
        self.run_quiet(resume=run)
        self.assertEqual(app.read_json(run/'summary.json')['completed_photos'],6)
    def test_stop_checkpoint_write_failure_reports_incomplete_and_preserves_sources(self):
        source_hash=[hashlib.sha256(p.read_bytes()).hexdigest() for p in [self.photos,self.jsons]]
        with patch.object(app.CheckpointStore,'commit',side_effect=OSError('injected full disk')):
            with self.assertRaisesRegex(OSError,'injected full disk'):self.run_quiet(stop_after=1)
        run=Path(app.read_json(self.root/'out/latest_active_run.json')['path'])
        state=app.read_json(run/'summary.json')
        self.assertEqual(state['status'],'incomplete');self.assertEqual(state['completed_photos'],0)
        self.assertEqual(state['remaining_photos'],6)
        self.assertTrue(list((run/'chunks').glob('.abandoned_*')))
        self.assertEqual(source_hash,[hashlib.sha256(p.read_bytes()).hexdigest() for p in [self.photos,self.jsons]])
        self.run_quiet(resume=run)
        self.assertEqual(app.read_json(run/'summary.json')['completed_photos'],6)
    def test_catalog_write_failure_reports_incomplete_then_resume_regenerates(self):
        with patch.object(app.CheckpointStore,'regenerate_catalogs',side_effect=OSError('catalog write failed')):
            with self.assertRaisesRegex(OSError,'catalog write failed'):self.run_quiet()
        run=Path(app.read_json(self.root/'out/latest_active_run.json')['path'])
        state=app.read_json(run/'summary.json')
        self.assertEqual(state['status'],'incomplete');self.assertEqual(state['completed_photos'],6)
        with patch.object(app,'process_photo',wraps=app.process_photo) as process:self.run_quiet(resume=run);self.assertEqual(process.call_count,0)
        self.assertEqual(app.read_json(run/'summary.json')['status'],'complete')
    def test_run_status_writer_retries_transient_sharing_collision(self):
        original=app._legacy_atomic_json
        collision_count=0
        def colliding(path,value):
            nonlocal collision_count
            if Path(path).name=='summary.json' and collision_count<2:
                collision_count+=1;raise PermissionError('Windows sharing collision')
            return original(path,value)
        with patch.object(app,'_legacy_atomic_json',side_effect=colliding):run=self.run_quiet(limit=1)
        self.assertEqual(collision_count,2)
        self.assertEqual(app.read_json(run/'summary.json')['status'],'complete')

if __name__=='__main__':unittest.main()
