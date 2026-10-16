import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from PIL import Image
from auto_preprocess import run,atomic_json,choose_transfer,image_descriptor
from dataset_io import ExportDataset

class AutomaticBatchTest(unittest.TestCase):
    def test_exact_photo_conflicting_teachers_do_not_authorize_relabel(self):
        image=Image.new('RGB',(100,80),'gray');points=[[5,5],[90,5],[90,70],[5,70]]
        teachers=[{'id':label,'source':{'sha256':'same'},'image':image,'regions':[{'id':label,'label':label,'points':points,'role':'complete_surface','conflict_labels':[other]}]} for label,other in [('wall','ceiling'),('ceiling','wall')]]
        result=choose_transfer({'zip':'unused'},image,'same',teachers,2)
        self.assertTrue(result['regions']);self.assertTrue(all(not r['auto_transfer_ok'] for r in result['regions']))
        self.assertTrue(any(e.get('reason')=='CONTRADICTORY_AUTOMATIC_TEACHERS' for e in result['evidence']))

    def test_limited_unread_json_is_not_rebuilt_as_missing_annotation(self):
        temp_root=Path(__file__).resolve().parent.parent/'tmp';temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as name:
            root=Path(name);workspace=root/'workspace';workspace.mkdir();photos=root/'photos.zip';jsons=root/'annotations.zip'
            stream=io.BytesIO();Image.new('RGB',(100,80),'gray').save(stream,format='PNG');pixels=stream.getvalue()
            with zipfile.ZipFile(photos,'w') as z:z.writestr('target.png',pixels)
            with zipfile.ZipFile(jsons,'w') as z:z.writestr('different_name.json',json.dumps({'imagePath':'target.png','imageWidth':100,'imageHeight':80,'shapes':[]}))
            teacher={'id':'test','source':{'zip':str(photos),'entry':'target.png','sha256':hashlib.sha256(pixels).hexdigest()},'size':[100,80],'reference_origin':'assistant_authorized_surface_draft','regions':[{'id':'wall','label':'wall','points':[[5,5],[90,5],[90,70],[5,70]],'role':'complete_surface'}]}
            atomic_json(workspace/'project.json',{'photos':[str(photos)],'json_zip':str(jsons),'references':[teacher]})
            args=argparse.Namespace(workspace=workspace,out=root/'out',entries_file=None,limit=1,workers=1,max_references=2,layout='files',min_free_mb=0)
            with contextlib.redirect_stdout(io.StringIO()):result=run(args)
            summary=json.loads((result/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['classes'],{'needs_review':1});self.assertEqual(summary['exported_json'],0)
            self.assertIn('JSON_PAIR_NOT_PROVEN_RECOVERY_WITHHELD',summary['unresolved'])

    def test_automatic_fix_export_and_zip_photo_pair_loader(self):
        temp_root=Path(__file__).resolve().parent.parent/'tmp';temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as name:
            root=Path(name);workspace=root/'workspace';workspace.mkdir()
            photos=root/'photos.zip';jsons=root/'annotations.zip'
            stream=io.BytesIO();Image.new('RGB',(100,80),'gray').save(stream,format='PNG');pixels=stream.getvalue()
            shape={'label':'wall','shape_type':'polygon','points':[[5,5],[90,5],[90,70],[5,70]],'group_id':None,'flags':{}}
            docs={filename:{'imagePath':filename,'imageWidth':100,'imageHeight':80,'imageData':'discard this embedded replica','shapes':shapes} for filename,shapes in [('good.png',[shape,shape]),('missing.png',[]),('wrong.png',[dict(shape,label='ceiling')]),('ambiguous.png',[dict(shape,label='tools'),dict(shape,label='construction materials')]),('broken.png',[shape])]}
            with zipfile.ZipFile(photos,'w') as z:
                for filename in docs:z.writestr(filename,pixels if filename!='broken.png' else b'corrupt')
            with zipfile.ZipFile(jsons,'w') as z:
                for filename,doc in docs.items():z.writestr(filename.replace('.png','.json'),json.dumps(doc))
            before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in [photos,jsons]]
            teacher={'id':'synthetic','source':{'zip':str(photos),'entry':'good.png','sha256':hashlib.sha256(pixels).hexdigest()},'size':[100,80],'reference_origin':'assistant_authorized_surface_draft','regions':[{'id':'wall','label':'wall','points':shape['points'],'role':'complete_surface','min_coverage':.6,'conflict_labels':['ceiling']} ]}
            atomic_json(workspace/'project.json',{'photos':[str(photos)],'json_zip':str(jsons),'references':[teacher]})
            args=argparse.Namespace(workspace=workspace,out=root/'out',entries_file=None,limit=0,workers=2,max_references=2,layout='zip',min_free_mb=0)
            with contextlib.redirect_stdout(io.StringIO()):result=run(args)
            summary=json.loads((result/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['status'],'complete');self.assertEqual(summary['classes'],{'auto_fixed':3,'needs_review':1,'rejected':1})
            self.assertEqual(summary['exported_json'],4)
            self.assertGreater(summary['actions'].get('restore_missing_complete_surface',0),0)
            self.assertGreater(summary['actions'].get('relabel_full_surface_supported_shape',0),0)
            self.assertTrue((result/'classification.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
            with ExportDataset(result) as dataset:
                self.assertEqual(len(dataset),3)
                for image,doc,record in (dataset[n] for n in range(len(dataset))):
                    self.assertEqual(image.size,(100,80));self.assertIsNone(doc['imageData']);self.assertEqual({s['label'] for s in doc['shapes']},{'wall'})
                    self.assertTrue(record['changed']);self.assertFalse(record['human_confirmed'])
            self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in [photos,jsons]],before)
            self.assertFalse(any(p.suffix=='.png' for p in result.rglob('*')))

if __name__=='__main__':unittest.main()
