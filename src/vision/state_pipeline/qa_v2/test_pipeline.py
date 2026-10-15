import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from qa_v2 import batch, atomic_json, Dataset, load_references, strict_loads
from test_registration import textured_image, region

class PipelineTests(unittest.TestCase):
    def test_nonfinite_constants_and_numeric_overflow_are_rejected(self):
        for text in ['{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}', '{"value":1e999}']:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):strict_loads(text)

    def test_nonfinite_json_is_reported_without_interrupting_other_images(self):
        temp_root=Path(__file__).resolve().parent.parent/'tmp';temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as name:
            root=Path(name);workspace=root/'workspace';workspace.mkdir()
            photos=root/'photos.zip';annotations=root/'annotations.zip'
            stream=io.BytesIO();Image.new('RGB',(100,80)).save(stream,format='PNG')
            doc={'imagePath':'good.png','imageWidth':100,'imageHeight':80,'shapes':[]}
            with zipfile.ZipFile(photos,'w') as z:
                for filename in ['good.png','bad.png']:z.writestr(filename,stream.getvalue())
            with zipfile.ZipFile(annotations,'w') as z:
                z.writestr('good.json',json.dumps(doc));z.writestr('bad.json',json.dumps(dict(doc,imagePath='bad.png',custom=float('nan'))))
            atomic_json(workspace/'project.json',{'photos':[str(photos)],'json_zip':str(annotations),'references':[]})
            args=argparse.Namespace(workspace=workspace,out=root/'results',limit=0,entries_file=None,check_images=True,hash_duplicates=False,write_corrections=True,allow_ai_references=False,raster_diagnostics=False)
            with contextlib.redirect_stdout(io.StringIO()):run=batch(args)
            result=json.loads((run/'results.json').read_text(encoding='utf-8'))
            self.assertEqual(result['summary']['processed_json'],2)
            self.assertEqual(result['summary']['json_errors'],{'JSON_NONFINITE_CONSTANT':1})
            self.assertEqual(result['summary']['image_decode_successes'],2)
            self.assertTrue((run/'quality.csv').exists())

    def test_zip_teacher_projection_missing_conflict_and_draft_preservation(self):
        temp_root=Path(__file__).resolve().parent.parent/'tmp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as name:
            root=Path(name);workspace=root/'workspace';workspace.mkdir()
            ref=textured_image();transform=np.float64([[1,0,14],[0,1,-8],[0,0,1]])
            moved=Image.fromarray(cv2.warpPerspective(np.asarray(ref),transform,ref.size))
            photos=root/'photos.zip';annotations=root/'annotations.zip';payloads={}
            for filename,image in [('ref.png',ref),('missing.png',moved),('wrong.png',moved),('blank.png',Image.new('RGB',ref.size,'white'))]:
                stream=io.BytesIO();image.save(stream,format='PNG');payloads[filename]=stream.getvalue()
            with zipfile.ZipFile(photos,'w') as z:
                for filename,payload in payloads.items():z.writestr(filename,payload)
            ref_region=region();poly={'label':'wall','shape_type':'polygon','points':ref_region['points'],'group_id':None,'flags':{}}
            shifted=(np.asarray(ref_region['points'])+[14,-8]).tolist()
            docs={filename:{'imagePath':filename,'imageWidth':600,'imageHeight':400,'shapes':shapes} for filename,shapes in [('ref.png',[poly,poly]),('missing.png',[]),('wrong.png',[dict(poly,label='ceiling',points=shifted)]),('blank.png',[])]}
            with zipfile.ZipFile(annotations,'w') as z:
                for filename,doc in docs.items():z.writestr(filename.replace('.png','.json'),json.dumps(doc))
            before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in [photos,annotations]]
            config={'photos':[str(photos)],'json_zip':str(annotations),'references':[{'id':'teacher','source':{'zip':str(photos),'entry':'ref.png','sha256':hashlib.sha256(payloads['ref.png']).hexdigest()},'size':[600,400],'regions':[ref_region],'reviewed':True,'reviewer':'synthetic test','reference_origin':'human_reviewed'}]}
            atomic_json(workspace/'project.json',config)
            args=argparse.Namespace(workspace=workspace,out=root/'results',limit=0,entries_file=None,check_images=True,hash_duplicates=True,write_corrections=True,allow_ai_references=False)
            with contextlib.redirect_stdout(io.StringIO()):run=batch(args)
            results=json.loads((run/'results.json').read_text(encoding='utf-8'))
            by_name={r['photo_entry']:r for r in results['results']}
            self.assertIn('ROI_0_EXPECTED_LABEL_ABSENT',by_name['missing.png']['issues'])
            self.assertIn('ROI_0_CONFLICTING_LABEL_ceiling',by_name['wrong.png']['issues'])
            self.assertEqual(by_name['missing.png']['semantic_status'],'provisional_reference_rules_only')
            self.assertEqual(by_name['blank.png']['semantic_status'],'abstain')
            draft=Path(by_name['ref.png']['correction_file'])
            self.assertEqual(len(json.loads(draft.read_text(encoding='utf-8'))['shapes']),1)
            provenance=json.loads(draft.with_suffix('.provenance.json').read_text(encoding='utf-8'))
            self.assertTrue(provenance['needs_review']);self.assertFalse(provenance['training_eligible'])
            self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in [photos,annotations]],before)
            self.assertTrue((run/'quality.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(results['summary']['image_decode_successes'],4)
            with contextlib.closing(Dataset(config['photos'],config['json_zip'])) as dataset:
                config['references'][0].update(reviewed=False,reference_origin='assistant_visual_draft')
                with self.assertRaises(ValueError):load_references(config,dataset,False)
                self.assertFalse(load_references(config,dataset,True)[0]['human_confirmed'])

if __name__=='__main__':unittest.main()
