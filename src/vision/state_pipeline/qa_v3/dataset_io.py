"""Read exported annotations and original ZIP photos without extracting photos."""
from pathlib import Path
import getpass
import hashlib
import io
import json
import os
import zipfile
import pyzipper
from PIL import Image

class ExportDataset:
    def __init__(self,run_dir,categories=('auto_fixed','kept'),password=None,verify_hashes=True):
        self.run_dir=Path(run_dir);self.verify_hashes=verify_hashes;self.handles={}
        self.password=password if password is not None else os.environ.get('QA_ZIP_PASSWORD')
        self.records=[]
        with (self.run_dir/'manifest.jsonl').open(encoding='utf-8') as handle:
            for line in handle:
                record=json.loads(line)
                if record['classification'] in categories and record.get('annotation_output'):
                    self.records.append(record)
    def __len__(self):return len(self.records)
    def archive(self,path,encrypted=False):
        key=str(path)
        if key not in self.handles:
            z=pyzipper.AESZipFile(path,'r') if encrypted else zipfile.ZipFile(path,'r')
            if encrypted and any(i.flag_bits&1 for i in z.infolist()):
                if self.password is None:self.password=getpass.getpass('ZIP password: ')
                z.setpassword(self.password.encode('utf-8'))
            self.handles[key]=z
        return self.handles[key]
    def __getitem__(self,index):
        record=self.records[index];photo=record['source_photo'];out=record['annotation_output']
        pixels=self.archive(photo['zip'],True).read(photo['entry'])
        payload=Path(out['path']).read_bytes() if 'path' in out else self.archive(out['zip']).read(out['entry'])
        if self.verify_hashes:
            if hashlib.sha256(pixels).hexdigest()!=photo['sha256']:raise ValueError('Original photo hash changed')
            if hashlib.sha256(payload).hexdigest()!=record['output_json_sha256']:raise ValueError('Output annotation hash changed')
        with Image.open(io.BytesIO(pixels)) as im:im.load();image=im.convert('RGB')
        return image,json.loads(payload),record
    def close(self):
        for archive in self.handles.values():archive.close()
        self.handles.clear()
        self.password=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
