"""Automatic ZIP annotation repair and classified export. No browser required."""
from __future__ import annotations
import argparse
import contextlib
import csv
from copy import deepcopy
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from console_progress import ProgressReporter, _retry_snapshot_io
from checkpoint_store import CheckpointStore

import cv2
import numpy as np
from PIL import Image
from shapely.geometry import Polygon

V2=Path(__file__).resolve().parent.parent/'qa_v2'
sys.path.insert(0,str(V2))
from qa_v2 import Dataset, MAX_JSON, atomic_json as _legacy_atomic_json, checked_regions, read_json, strict_loads
import legacy_checks as legacy
from repair import repair_annotation
from transfer import transfer

CATEGORIES=('auto_fixed','kept','needs_review','rejected')

def atomic_json(path,value):
    # A status observer may briefly hold the previous snapshot open on Windows.
    return _retry_snapshot_io(lambda:_legacy_atomic_json(path,value))

def image_descriptor(image):
    small=np.asarray(image.resize((96,64)),dtype=np.float32)/255.
    gray=cv2.cvtColor(small,cv2.COLOR_RGB2GRAY)
    return np.concatenate([cv2.resize(small,(24,16)).ravel(),cv2.resize(cv2.Laplacian(gray,cv2.CV_32F),(24,16)).ravel()])

def load_teachers(config,dataset):
    result=[]
    for raw in config.get('references',[]):
        image,sha=dataset.image(raw['source']['zip'],raw['source']['entry'])
        if sha!=raw['source'].get('sha256') or list(image.size)!=raw.get('size'):
            raise ValueError('Teacher photo source hash/size changed: '+raw['id'])
        if raw.get('reference_origin') not in {'human_reviewed','assistant_visual_draft','assistant_authorized_surface_draft'}:
            raise ValueError('Unsupported teacher origin: '+raw['id'])
        if raw.get('reference_origin')=='human_reviewed' and (raw.get('reviewed') is not True or not str(raw.get('reviewer','')).strip()):
            raise ValueError('Human-reviewed origin requires actual reviewer confirmation')
        regions=checked_regions(raw['regions'],image.size)
        result.append(dict(raw,image=image,regions=regions,descriptor=image_descriptor(image)))
    if not result:raise ValueError('Automatic batch requires configured teacher regions')
    return result

def effective_region(region):
    geometry=Polygon(region['points'])
    for exclusion in region.get('exclusions',[]):geometry=geometry.difference(Polygon(exclusion))
    return geometry

def reconcile_candidates(candidates):
    winner=max(candidates,key=lambda p:p['score'])
    blocked=set()
    for ordinal,region in enumerate(winner['regions']):
        if not region.get('auto_transfer_ok'):continue
        a=effective_region(region)
        for alternative in candidates:
            if alternative is winner:continue
            for other in alternative['regions']:
                if not other.get('auto_transfer_ok'):continue
                incompatible=(other['label'].casefold() in [v.casefold() for v in region.get('conflict_labels',[])] or region['label'].casefold() in [v.casefold() for v in other.get('conflict_labels',[])])
                if incompatible:
                    b=effective_region(other)
                    if min(a.area,b.area)>0 and a.intersection(b).area/min(a.area,b.area)>.25:blocked.add(ordinal)
    if blocked:
        winner['regions']=[dict(r,auto_transfer_ok=False,full_surface_transfer_ok=False,cross_teacher_conflict=True) if n in blocked else r for n,r in enumerate(winner['regions'])]
        winner['evidence'].append({'status':'withheld','reason':'CONTRADICTORY_AUTOMATIC_TEACHERS','region_ids':[winner['regions'][n]['id'] for n in sorted(blocked)]})
    winner['attempted_references']=[{'id':p['reference_id'],'score':p['score']} for p in candidates]
    return winner

def compact_evidence(value):
    if isinstance(value,list):return [compact_evidence(item) for item in value]
    if not isinstance(value,dict):return value
    result={}
    for key,item in value.items():
        if key=='tiles' and isinstance(item,list):
            result['evaluated_tile_count']=len(item)
            result['sample_tiles']=compact_evidence(item[:2])
        else:result[key]=compact_evidence(item)
    return result

def choose_transfer(photo,image,sha,teachers,max_refs):
    exact=[r for r in teachers if r['source']['sha256']==sha]
    if exact:
        candidates=[]
        for teacher in exact:
            mapped=transfer(teacher['image'],image,teacher['regions'],source_identity=True)
            score=sum(effective_region(r).area for r in mapped['regions'] if r.get('full_surface_transfer_ok'))
            candidates.append(dict(mapped,reference_id=teacher['id'],score=score))
        return reconcile_candidates(candidates)
    same_zip=[r for r in teachers if str(Path(r['source']['zip']).resolve())==photo['zip']]
    descriptor=image_descriptor(image)
    ranked=sorted(same_zip,key=lambda r:float(np.mean(np.abs(r['descriptor']-descriptor))))[:max_refs]
    candidates=[]
    for teacher in ranked:
        mapped=transfer(teacher['image'],image,teacher['regions'])
        usable=[r for r in mapped['regions'] if r.get('auto_transfer_ok')]
        surface_area=sum(abs(cv2.contourArea(np.asarray(r['points'],dtype=np.float32))) for r in usable if r.get('full_surface_transfer_ok'))/(image.width*image.height)
        score=surface_area+sum(1/(1+float(r.get('appearance_error_score',100))) for r in usable)*.01
        candidates.append(dict(mapped,reference_id=teacher['id'],score=score))
    if not candidates:return {'regions':[],'evidence':[{'reason':'NO_REFERENCE_IN_SAME_SOURCE_ZIP'}],'reference_id':'','accepted':False}
    return reconcile_candidates(candidates)

def selected_photos(dataset,args):
    if args.entries_file:
        wanted=read_json(args.entries_file)
        keys={(str(Path(p['zip']).resolve()),p['entry']) for p in wanted}
        selected=[p for p in dataset.photos if (p['zip'],p['entry']) in keys]
        if len(selected)!=len(keys):raise ValueError('Requested photos missing or duplicated in ZIP')
        return selected
    groups=[[p for p in dataset.photos if p['zip']==str(path)] for path in dataset.paths]
    ordered=[]
    for n in range(max(map(len,groups))):
        for group in groups:
            if n<len(group):ordered.append(group[n])
    start=getattr(args,'start',0)
    return ordered[start:start+args.limit] if args.limit else ordered[start:]

def index_json(dataset,workspace,selected,limited,reporter=None,stop_check=None):
    """Cache only source metadata; do not copy source JSON/embedded photos."""
    workspace.mkdir(parents=True,exist_ok=True)
    stat=dataset.json_path.stat()
    signature=hashlib.sha256(json.dumps([str(dataset.json_path),stat.st_size,stat.st_mtime_ns,[(i.filename,i.CRC,i.file_size,i.compress_size) for _,i in dataset.json_infos]],ensure_ascii=False).encode()).hexdigest()
    db=sqlite3.connect(workspace/'json_index.sqlite')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS entries (ordinal INTEGER PRIMARY KEY, name TEXT,image_path TEXT,sha TEXT,error TEXT)')
        old=db.execute("SELECT value FROM metadata WHERE key='signature'").fetchone()
        if not old or old[0]!=signature:
            db.execute('DELETE FROM entries');db.execute("INSERT OR REPLACE INTO metadata VALUES ('signature',?)",(signature,));db.commit()
        cached={r[0] for r in db.execute('SELECT ordinal FROM entries')}
        stems={legacy.stem(legacy.base(p['entry'])) for p in selected}
        pending=[(n,i) for n,i in dataset.json_infos if n not in cached and (not limited or legacy.stem(legacy.base(i.filename)) in stems)]
        if reporter:reporter.update(phase='JSON_INDEX',done=0,total=len(pending),message=f'cached={len(cached)}; archive_entries={len(dataset.json_infos)}')
        else:print(f'JSON index: cached={len(cached)}, read={len(pending)}, total={len(dataset.json_infos)}',flush=True)
        for count,(n,info) in enumerate(pending,1):
            if stop_check:stop_check()
            if reporter:reporter.update(current=info.filename)
            image_path=None;sha='';error=None
            try:
                if info.file_size>MAX_JSON:raise ValueError('JSON_SIZE_LIMIT')
                with dataset.archives[str(dataset.json_path)].open(info) as handle:raw=handle.read(MAX_JSON+1)
                if len(raw)>MAX_JSON:raise ValueError('JSON_SIZE_LIMIT')
                sha=hashlib.sha256(raw).hexdigest();doc=strict_loads(raw)
                if not isinstance(doc,dict):raise ValueError('JSON_OBJECT_REQUIRED')
                image_path=doc.get('imagePath') if isinstance(doc.get('imagePath'),str) else None
            except Exception as exc:error=type(exc).__name__+':'+str(exc)[:120]
            db.execute('INSERT INTO entries VALUES (?,?,?,?,?)',(n,info.filename,image_path,sha,error))
            if count%250==0:
                db.commit()
                if not reporter:print(f'JSON index read: {count}/{len(pending)}',flush=True)
            if reporter:reporter.update(done=count)
        db.commit()
        rows=list(db.execute('SELECT ordinal,name,image_path,sha,error FROM entries ORDER BY ordinal'))
        full=len(rows)==len(dataset.json_infos)
        lookup={n:i for n,i in dataset.json_infos};by_photo=defaultdict(list);unmatched=[];uncertain=set();selected_ids={p['id'] for p in selected};unreadable_orphan=False
        for ordinal,name,image_path,sha,error in rows:
            choices,method=dataset.match(lookup[ordinal],{'imagePath':image_path})
            item={'ordinal':ordinal,'entry':name,'sha256':sha,'error':error,'match_method':method}
            if len(choices)==1 and choices[0]['id'] in selected_ids:by_photo[choices[0]['id']].append(item)
            elif len(choices)!=1:
                uncertain.update(p['id'] for p in choices)
                if not choices and error:
                    # An unreadable orphan may be an otherwise missing photo's
                    # annotation. A completed metadata scan cannot prove that
                    # this unknown link is absent.
                    unreadable_orphan=True
                if not limited:unmatched.append(dict(item,reason='AMBIGUOUS_IMAGE_PAIR' if choices else 'ORPHAN_JSON'))
        if unreadable_orphan:uncertain.update(selected_ids-set(by_photo))
        return by_photo,unmatched,full,signature,uncertain
    finally:db.close()

def process_photo(photo,sources,dataset,teachers,args,index_complete=True,uncertain_pair=False):
    records=[]
    try:image,sha=dataset.image(photo['zip'],photo['entry'])
    except Exception as exc:
        return [{'id':photo['id'],'photo':photo,'classification':'rejected','source_json':{'entries':sources},'unresolved':[{'code':'IMAGE_DECODE_FAILED','detail':type(exc).__name__}],'actions':[],'doc':None}]
    mapping=choose_transfer(photo,image,sha,teachers,args.max_references)
    mapping['evidence']=compact_evidence(mapping.get('evidence',[]))
    if not sources and (not index_complete or uncertain_pair):
        return [{'id':photo['id'],'photo':photo,'photo_sha256':sha,'classification':'needs_review','source_json':{'entry':None},'reference_id':mapping['reference_id'],'transfer_evidence':mapping['evidence'],'unresolved':[{'code':'JSON_PAIR_NOT_PROVEN_RECOVERY_WITHHELD'}],'actions':[],'doc':None}]
    if not sources:sources=[{'entry':None,'sha256':None,'error':None}]
    multiple=len(sources)>1
    for source in sources:
        record={'id':photo['id']+(':'+str(source.get('ordinal','missing'))),'photo':photo,'photo_sha256':sha,'source_json':source,'reference_id':mapping['reference_id'],'transfer_evidence':mapping.get('evidence',[]),'attempted_references':mapping.get('attempted_references',[]),'actions':[],'unresolved':[],'doc':None}
        try:
            if source['error']:raise ValueError(source['error'])
            if source['entry']:
                raw=dataset.bytes(dataset.json_path,source['entry'],MAX_JSON)
                if hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('JSON_SOURCE_CHANGED_AFTER_INDEX')
                doc=strict_loads(raw)
            else:
                doc={'version':'5.8.1','flags':{},'shapes':[],'imagePath':legacy.base(photo['entry']),'imageData':None,'imageWidth':image.width,'imageHeight':image.height}
            if not isinstance(doc,dict) or not isinstance(doc.get('shapes'),list):raise ValueError('LABELME_SHAPES_LIST_REQUIRED')
            if (doc.get('imageWidth'),doc.get('imageHeight'))!=image.size:
                record['unresolved'].append({'code':'IMAGE_SIZE_MISMATCH','declared':[doc.get('imageWidth'),doc.get('imageHeight')],'actual':list(image.size)})
                outcome={'corrected_doc':None,'actions':[],'unresolved':[],'coverage':[],'changed':False,'quality_inputs':{'semantic_assessment':'unassessed'}}
            elif uncertain_pair:
                record['unresolved'].append({'code':'JSON_PAIR_AMBIGUITY_AUTOMATIC_EDIT_WITHHELD'})
                outcome={'corrected_doc':None,'actions':[],'unresolved':[],'coverage':[],'changed':False,'quality_inputs':{'semantic_assessment':'unassessed'}}
            elif multiple:
                record['unresolved'].append({'code':'MULTIPLE_JSON_FOR_IMAGE_AUTOMATIC_EDIT_WITHHELD'})
                outcome={'corrected_doc':None,'actions':[],'unresolved':[],'coverage':[],'changed':False,'quality_inputs':{'semantic_assessment':'unassessed'}}
            else:outcome=repair_annotation(doc,image.size,mapping['regions'],mapping.get('evidence',[]),
                                           policy={'allow_raster_hole_decomposition':True,'max_components':512})
            record['actions']=outcome['actions'];record['unresolved'].extend(outcome['unresolved'])
            for action in record['actions']:
                if 'teacher_evidence' in action:
                    action.pop('teacher_evidence');action['evidence_reference']='record.transfer_evidence'
            record['coverage']=outcome.get('coverage',[]);record['quality_inputs']=outcome.get('quality_inputs',{})
            if not source['entry']:record['unresolved'].append({'code':'NEW_DOCUMENT_RECOVERS_KNOWN_SURFACES_ONLY'})
            if not any(r.get('auto_transfer_ok') for r in mapping['regions']):record['unresolved'].append({'code':'NO_CONFIDENT_SEMANTIC_REFERENCE'})
            if any(r.get('partial_surface_transfer') for r in mapping['regions']):
                record['unresolved'].append({'code':'PARTIAL_SURFACE_TRANSFER_WITH_WITHHELD_SECTIONS',
                                            'region_ids':[r['id'] for r in mapping['regions'] if r.get('partial_surface_transfer')]})
            output=deepcopy(outcome['corrected_doc'] or doc)
            output['imageData']=None;output['imagePath']=legacy.base(photo['entry'])
            if not record['unresolved']:
                record['classification']='auto_fixed' if outcome['changed'] else 'kept'
            else:record['classification']='needs_review'
            record['doc']=output
        except Exception as exc:
            record['classification']='rejected';record['unresolved'].append({'code':'JSON_NOT_USABLE','detail':type(exc).__name__+':'+str(exc)[:180]})
        records.append(record)
    return records

def bounded_map(executor,function,items,window,stop_check=None):
    iterator=iter(items);pending=deque()
    for _ in range(window):
        try:pending.append(executor.submit(function,next(iterator)))
        except StopIteration:break
    while pending:
        future=pending.popleft()
        while True:
            if stop_check:stop_check()
            try:
                result=future.result(timeout=.25 if stop_check else None)
                break
            except FutureTimeout:
                if future.done():raise
                continue
        yield result
        try:pending.append(executor.submit(function,next(iterator)))
        except StopIteration:pass

def output_name(record):
    entry=record.get('source_json',{}).get('entry')
    base=legacy.base(entry or record['photo']['entry'])
    base=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',Path(base).stem)[:160]
    return record['photo']['id'].split(':')[0]+'/'+base+'__'+hashlib.sha256(record['id'].encode()).hexdigest()[:8]+'.json'

def normalize_options(args):
    values=dict(vars(args))
    defaults=dict(start=0,limit=0,entries_file=None,workers=2,max_references=2,
                  layout='zip',min_free_mb=1024,resume=None,checkpoint_every=20,
                  progress_seconds=2.,max_minutes=0,stop_after=0)
    for key,value in defaults.items():values.setdefault(key,value)
    return argparse.Namespace(**values)

def finalize_record(record,dataset):
    photo=record.pop('photo');doc=record.pop('doc',None)
    record['source_photo']={'zip':photo['zip'],'entry':photo['entry'],'sha256':record.pop('photo_sha256',None)}
    record['source_json_zip']=str(dataset.json_path)
    record['label_origin']='automatic_repair_pseudo_label' if record['actions'] else 'source_annotation_preserved'
    record['human_confirmed']=False;record['training_eligible']=False
    record['changed']=bool(record['actions'])
    payload=None if doc is None else json.dumps(doc,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    return record,payload,output_name(dict(record,photo=photo))

def run(args):
    from operations_control import ControlledStop,RunLock,StopChecks,acknowledge_stop,source_signature,implementation_signature
    args=normalize_options(args)
    args.workspace=args.workspace.resolve();args.out=args.out.resolve()
    started=time.monotonic();config_path=args.workspace/'project.json'
    config_bytes=config_path.read_bytes();config=strict_loads(config_bytes)
    config_sha=hashlib.sha256(config_bytes).hexdigest()
    resuming=bool(args.resume)
    if resuming:
        run_dir=Path(args.resume).resolve()
        if not run_dir.is_dir() or not run_dir.is_relative_to(args.out) or run_dir==args.out:
            raise ValueError('Resume folder must be an existing run inside the selected output folder')
        if not (run_dir/'job.json').exists():
            raise ValueError('This run has no resumable checkpoint plan; previous v3 results remain readable')
    else:
        run_dir=args.out/datetime.now(timezone.utc).strftime('run_%Y%m%dT%H%M%S_%fZ')
        run_dir.mkdir(parents=True,exist_ok=False)
    with RunLock(run_dir/'run.lock'):
        with Dataset(config['photos'],config['json_zip']) as dataset:
            binding={'source_signature':source_signature(dataset),
                     'teacher_config_sha256':config_sha,
                     'implementation_sha256':implementation_signature()}
            if hashlib.sha256(config_path.read_bytes()).hexdigest()!=config_sha:
                raise ValueError('REFERENCE_CONFIG_CHANGED_DURING_STARTUP')
            by_id={p['id']:p for p in dataset.photos}
            if resuming:
                job=read_json(run_dir/'job.json')
                if job.get('schema_version')!=4 or any(job.get(key)!=value for key,value in binding.items()):
                    raise ValueError('RESUME_INPUT_OR_CODE_CHANGED: original ZIP, reference settings or processing code changed')
                ids=job['selected_photo_ids']
                if len(ids)!=len(set(ids)) or any(key not in by_id for key in ids):
                    raise ValueError('Saved photo selection changed or is invalid')
                selected=[by_id[key] for key in ids]
                for name in ['layout','checkpoint_every','max_references','start','limit']:
                    setattr(args,name,job['options'][name])
                limited=job['limited_json_index'];scope=job['scope']
                acknowledge_stop(run_dir)
            else:
                if args.entries_file and args.start:raise ValueError('Entries-file selection cannot be combined with start')
                selected=selected_photos(dataset,args)
                if not selected:raise ValueError('No photos selected; check start/limit')
                limited=bool(args.entries_file or args.limit or args.start)
                scope='selected_entries' if args.entries_file else 'limited_round_robin' if limited else 'full_dataset'
                job=dict(binding,schema_version=4,created_utc=datetime.now(timezone.utc).isoformat(),
                         selected_photo_ids=[p['id'] for p in selected],limited_json_index=limited,scope=scope,
                         options={key:getattr(args,key) for key in ['layout','checkpoint_every','max_references','start','limit']})
                atomic_json(run_dir/'job.json',job)
            previous=read_json(run_dir/'summary.json') if (run_dir/'summary.json').exists() else {}
            elapsed_before=float(previous.get('elapsed_seconds',0))
            summary={'schema_version':4,'status':'running','layout':args.layout,'scope':scope,
                     'selected_photos':len(selected),'total_photos':len(dataset.photos),
                     'total_json_entries':len(dataset.json_infos),'source_originals_modified':False,'photo_copies':0,
                     'teacher_config_sha256':binding['teacher_config_sha256'],
                     'meaning':'automatic pseudo-label outputs; checkpoint completion is not a visual pass',
                     'resume_supported':True,'checkpoint_every':args.checkpoint_every}
            def pointer(status):
                value={'path':str(run_dir),'run_dir':str(run_dir),'status':status,'updated_utc':datetime.now(timezone.utc).isoformat()}
                atomic_json(args.workspace/'latest_active_run.json',value)
                atomic_json(args.out/'latest_active_run.json',value)
            pointer('running')
            atomic_json(run_dir/'summary.json',summary)
            reporter=ProgressReporter(run_dir,interval=args.progress_seconds)
            store=None;executor=None;stop_reason=None;failure=None;full_index=False;signature='';orphan=[];teachers=[]
            selected_ids={p['id'] for p in selected};session_photos=0
            def discard_pending():
                nonlocal failure
                if store:
                    try:store.close(commit=False)
                    except Exception as exc:
                        if failure is None:failure=exc
            with reporter:
                reporter.update(phase='CHECKPOINT_VERIFY' if resuming else 'PREPARE',done=0,total=0,
                                message='Output: '+str(run_dir))
                try:
                    store=CheckpointStore(run_dir,layout=args.layout,checkpoint_every=args.checkpoint_every,resume=resuming)
                    if resuming:
                        allowed_orphans={'J:'+str(n) for n,_ in dataset.json_infos}
                        if store.completed_photo_ids-(selected_ids|allowed_orphans):raise ValueError('Checkpoint completion IDs are outside the saved job')
                        for record in store.iter_records():
                            key=record['checkpoint_photo_id']
                            if key in by_id:
                                expected=by_id[key];actual=record['source_photo']
                                if actual['zip']!=expected['zip'] or actual['entry']!=expected['entry']:
                                    raise ValueError('Checkpoint photo binding changed')
                            elif record.get('source_json',{}).get('ordinal')!=int(key.split(':')[1]):
                                raise ValueError('Checkpoint orphan binding changed')
                        store.regenerate_catalogs()
                    stop=StopChecks(run_dir,args,started);stop()
                    reporter.update(phase='PREPARE',done=0,total=len(config.get('references',[])),message='Verify teacher photo hashes')
                    teachers=load_teachers(config,dataset)
                    reporter.update(done=len(teachers))
                    with RunLock(args.workspace/'index.lock'):
                        source_map,orphan,full_index,signature,uncertain=index_json(dataset,args.workspace,selected,limited,reporter,stop)
                    remaining=[p for p in selected if p['id'] not in store.completed_photo_ids]
                    durable_done=len(selected_ids&store.completed_photo_ids)
                    counts=Counter(store.counters['classes']);actions=Counter(store.counters['actions'])
                    reporter.update(phase='PHOTO_REPAIR',done=durable_done,total=len(selected),classes=counts,actions=actions,
                                    export_bytes=store.bytes_written,checkpoint_done=durable_done,message=f'checkpointed={durable_done}; remaining={len(remaining)}')
                    cv2.setNumThreads(1)
                    executor=ThreadPoolExecutor(max_workers=args.workers)
                    def process(photo):
                        reporter.update(current=photo['entry'])
                        return process_photo(photo,source_map.get(photo['id'],[]),dataset,teachers,args,full_index,photo['id'] in uncertain)
                    for photo,records in zip(remaining,bounded_map(executor,process,remaining,args.workers*2,stop)):
                        for record in records:
                            record,payload,name=finalize_record(record,dataset)
                            result=store.write_record(record,payload,name)
                            counts[result['classification']]+=1
                            actions.update(a['action'] for a in result.get('actions',[]))
                        committed=store.complete_photo(photo['id']);session_photos+=1
                        reporter.update(done=durable_done+session_photos,classes=counts,actions=actions,
                                        export_bytes=store.bytes_written,current=photo['entry'],
                                        checkpoint_done=len(selected_ids&store.completed_photo_ids),
                                        message=f"checkpointed={len(selected_ids&store.completed_photo_ids)}; part={store.chunk_count}")
                        stop()
                        if args.stop_after and session_photos>=args.stop_after:
                            raise ControlledStop('SESSION_PHOTO_LIMIT_REACHED')
                    executor.shutdown(wait=True,cancel_futures=True);executor=None
                    reporter.update(phase='ORPHAN_EXPORT',done=0,total=len(orphan),current='',message='Save unpaired JSON reasons')
                    for number,item in enumerate(orphan,1):
                        key='J:'+str(item['ordinal'])
                        if key not in store.completed_photo_ids:
                            record={'id':'J'+str(item['ordinal']),'classification':'rejected','photo':{'id':key,'zip':'','entry':''},
                                    'source_json':item,'actions':[],'unresolved':[{'code':item['reason']}],'doc':None}
                            record,payload,name=finalize_record(record,dataset)
                            store.write_record(record,payload,name);store.complete_photo(key)
                        reporter.update(done=number);stop()
                    store.commit()
                except ControlledStop as exc:
                    stop_reason=str(exc)
                    reporter.update(phase='STOPPING',done=0,total=0,status='stopping',message=stop_reason+'; waiting for current worker operations')
                    try:
                        if store:store.commit()
                    except KeyboardInterrupt:
                        stop_reason='KEYBOARD_INTERRUPT';discard_pending()
                    except Exception as exc:
                        failure=exc;discard_pending()
                except KeyboardInterrupt:
                    stop_reason='KEYBOARD_INTERRUPT'
                    reporter.update(phase='STOPPING',done=0,total=0,status='stopping',message='Ctrl+C; retain committed parts and wait for workers')
                    # Ctrl+C may arrive inside a ZIP write or between two JSON
                    # records. Only previously committed parts are durable.
                    discard_pending()
                except Exception as exc:
                    failure=exc
                    reporter.update(phase='STOPPING',done=0,total=0,status='stopping',message=type(exc).__name__+'; preserving committed parts')
                    discard_pending()
                finally:
                    if executor:executor.shutdown(wait=True,cancel_futures=True)
                if store:
                    discard_pending()
                    try:store.regenerate_catalogs()
                    except Exception as exc:
                        if failure is None:failure=exc
                    durable=store.counters
                    completed_photos=len(selected_ids&store.completed_photo_ids)
                    summary.update(durable,completed_photos=completed_photos,checkpoint_parts=store.chunk_count,
                                   output_bytes=store.bytes_written,remaining_photos=len(selected)-completed_photos)
                else:
                    summary.update(classes={},actions={},unresolved={},exported_json=0,completed_photos=0,remaining_photos=len(selected))
                status='incomplete' if failure else 'paused' if stop_reason else 'complete'
                summary.update(status=status,stop_reason=stop_reason,elapsed_seconds=round(elapsed_before+time.monotonic()-started,2),
                               session_elapsed_seconds=round(time.monotonic()-started,2),global_json_index_complete=full_index,
                               json_source_signature=signature,reference_count=len(teachers),orphans=len(orphan),
                               full_surface_count=sum(sum(r.get('role')=='complete_surface' for r in t['regions']) for t in teachers))
                if failure:summary['error']=type(failure).__name__+':'+str(failure)
                atomic_json(run_dir/'summary.json',summary);pointer(status)
                if status=='complete':atomic_json(args.workspace/'latest_automatic_run.json',{'path':str(run_dir)})
                reporter.update(phase='FINISHED',done=summary['completed_photos'],total=len(selected),classes=summary['classes'],
                                actions=summary['actions'],export_bytes=summary.get('output_bytes',0),
                                checkpoint_done=summary['completed_photos'],
                                message=summary.get('error') or stop_reason or 'Committed output and catalogs complete')
                reporter.finish(status=status)
            print('Automatic outputs: '+str(run_dir),flush=True)
            print(json.dumps(summary,ensure_ascii=False),flush=True)
            if failure:raise failure
            return run_dir

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--limit',type=int,default=0);parser.add_argument('--start',type=int,default=0)
    parser.add_argument('--entries-file',type=Path);parser.add_argument('--resume',type=Path)
    parser.add_argument('--workers',type=int,default=2);parser.add_argument('--max-references',type=int,default=2)
    parser.add_argument('--layout',choices=['zip','files'],default='zip');parser.add_argument('--min-free-mb',type=int,default=1024)
    parser.add_argument('--checkpoint-every',type=int,default=20);parser.add_argument('--progress-seconds',type=float,default=2.)
    parser.add_argument('--max-minutes',type=float,default=0);parser.add_argument('--stop-after',type=int,default=0,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.limit<0 or args.start<0 or not 1<=args.workers<=8 or not 1<=args.max_references<=12 or args.min_free_mb<0 or args.checkpoint_every<1 or not .1<=args.progress_seconds<=60 or args.max_minutes<0 or args.stop_after<0:
        parser.error('Invalid numeric option')
    if args.resume and any(flag in sys.argv for flag in ['--limit','--start','--entries-file','--layout','--checkpoint-every']):
        parser.error('Resume restores selection/layout/checkpoint options from the saved job')
    try:run(args)
    except KeyboardInterrupt:print('Interrupted before resumable processing began.',flush=True);return 130
    except Exception as exc:print('ERROR: '+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
