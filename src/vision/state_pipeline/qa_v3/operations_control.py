"""Run guards and graceful-stop checks; no source data is written."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

class ControlledStop(Exception):
    pass

class RunLock:
    """Kernel lock is released after process death, without a stale-PID guess."""
    def __init__(self,path):self.path=Path(path);self.handle=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.handle=self.path.open('a+b')
        if self.path.stat().st_size==0:self.handle.write(b'0');self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close();self.handle=None
            raise RuntimeError('RUN_OR_INDEX_ALREADY_ACTIVE: '+str(self.path)) from exc
        return self
    def __exit__(self,*args):
        if self.handle is not None:
            self.handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN)
            self.handle.close();self.handle=None

def source_signature(dataset):
    digest=hashlib.sha256()
    for path in [*dataset.paths,dataset.json_path]:
        stat=path.stat()
        entry=[str(path),stat.st_size,stat.st_mtime_ns,
               [(i.filename,i.CRC,i.file_size,i.compress_size,i.flag_bits) for i in dataset.archives[str(path)].infolist()]]
        digest.update(json.dumps(entry,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    return digest.hexdigest()

def implementation_signature():
    root=Path(__file__).resolve().parent
    names=[root/n for n in ['auto_preprocess.py','repair.py','transfer.py','checkpoint_store.py','operations_control.py']]
    names += [root.parent/'qa_v2'/n for n in ['qa_v2.py','legacy_checks.py','semantic.py','registration.py']]
    digest=hashlib.sha256()
    for path in names:digest.update(path.name.encode());digest.update(path.read_bytes())
    return digest.hexdigest()

class StopChecks:
    def __init__(self,run_dir,args,started):self.run_dir=run_dir;self.args=args;self.started=started
    def __call__(self):
        if (self.run_dir/'STOP').exists():raise ControlledStop('STOP_REQUESTED')
        minutes=getattr(self.args,'max_minutes',0)
        if minutes and time.monotonic()-self.started>=minutes*60:raise ControlledStop('TIME_BUDGET_REACHED')
        if shutil.disk_usage(self.run_dir).free<self.args.min_free_mb*1024*1024:
            raise ControlledStop('DISK_SPACE_RESERVE_REACHED')

def acknowledge_stop(run_dir):
    path=run_dir/'STOP'
    if path.exists():
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        path.rename(run_dir/('STOP.acknowledged_'+stamp))
