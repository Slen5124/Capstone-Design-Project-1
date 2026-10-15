"""ZIP annotation QA with reviewed references, conservative projection, and draft corrections."""
import argparse
import contextlib
import csv
from copy import deepcopy
import getpass
import hashlib
import io
import json
import math
import os
import posixpath
import secrets
import sys
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyzipper
from PIL import Image
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import legacy_checks as legacy
from semantic import analyze
from registration import project_regions

MAX_JSON = 64 * 1024 * 1024
MAX_IMAGE = 128 * 1024 * 1024

def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        with tmp.open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()

def reject_nonfinite(value):
    raise ValueError('JSON_NONFINITE_CONSTANT_'+value)

def finite_float(value):
    result=float(value)
    if not math.isfinite(result):
        reject_nonfinite(value)
    return result

def strict_loads(payload):
    return json.loads(payload,parse_constant=reject_nonfinite,parse_float=finite_float)

def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as handle:
        return json.load(handle,parse_constant=reject_nonfinite,parse_float=finite_float)

def checked_regions(regions, size):
    if not isinstance(regions, list) or not regions:
        raise ValueError('At least one reviewed visible region is required')
    seen = set()
    result = []
    for n, raw in enumerate(regions):
        if not isinstance(raw, dict):
            raise ValueError('Region must be an object')
        item = dict(raw)
        name = item.get('id', f'R{n}')
        label = item.get('label')
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError('Unique region id required')
        seen.add(name)
        if not isinstance(label, str) or not label.strip():
            raise ValueError('Nonempty label required')
        points = item.get('points')
        def check_polygon(pts):
            if not isinstance(pts, list) or len(pts) < 3 or not all(
                isinstance(p, (list, tuple)) and len(p)==2 and all(legacy.number(v) for v in p)
                and 0<=p[0]<=size[0] and 0<=p[1]<=size[1] for p in pts):
                raise ValueError('Invalid or out-of-frame region coordinates')
            poly = Polygon(pts)
            if not poly.is_valid or poly.area<=1:
                raise ValueError('Invalid/empty region polygon')
        check_polygon(points)
        if 'registration_points' in item:
            check_polygon(item['registration_points'])
            if Polygon(item['points']).difference(Polygon(item['registration_points'])).area>1e-6:
                raise ValueError('Registration anchor must contain semantic ROI on same surface')
        exclusions = item.get('exclusions', [])
        if not isinstance(exclusions, list):
            raise ValueError('Exclusions must be polygons')
        for exclusion in exclusions:
            check_polygon(exclusion)
        threshold = item.get('min_coverage', .6)
        conflict_threshold = item.get('max_conflict', .25)
        if not all(legacy.number(x) and 0<=x<=1 for x in [threshold, conflict_threshold]):
            raise ValueError('Coverage thresholds must be within 0..1')
        conflicts=item.get('conflict_labels', ['ceiling'] if label.casefold()=='wall' else ['wall'] if label.casefold()=='ceiling' else [])
        if not isinstance(conflicts,list) or not all(isinstance(x,str) and x.strip() for x in conflicts):
            raise ValueError('Conflict labels must be strings')
        result.append(dict(item,id=name,label=label.strip(),exclusions=exclusions,
                           min_coverage=threshold,max_conflict=conflict_threshold,conflict_labels=conflicts))
    return result

class Dataset:
    def __init__(self, photos, json_zip, ask_password=True):
        self.paths = [Path(p).resolve() for p in photos]
        self.json_path = Path(json_zip).resolve()
        paths=self.paths+[self.json_path]
        if len(set(paths))!=len(paths):
            raise ValueError('ZIP inputs must be distinct')
        self.archives={}
        try:
            for path in paths:
                self.archives[str(path)]=pyzipper.AESZipFile(path, 'r')
            encrypted=any(i.flag_bits&1 for z in self.archives.values() for i in z.infolist())
            if encrypted:
                password=os.environ.get('QA_ZIP_PASSWORD')
                if password is None and ask_password:
                    password=getpass.getpass('ZIP password: ')
                if password is not None:
                    for z in self.archives.values():
                        z.setpassword(password.encode('utf-8'))
                del password
            self.photos=[]
            self.by_path,self.by_base,self.by_stem,self.by_base_stem=(defaultdict(list) for _ in range(4))
            self.entry_map=defaultdict(list)
            for a,path in enumerate(self.paths):
                z=self.archives[str(path)]
                for n,info in enumerate(z.infolist()):
                    self.entry_map[(str(path),info.filename)].append(info)
                    if info.is_dir() or posixpath.splitext(legacy.base(info.filename))[1] not in legacy.IMAGE_EXT:
                        continue
                    photo={'id':f'P{a}:{n}','zip':str(path),'entry':info.filename,'info':info}
                    self.photos.append(photo)
                    for mapping,key in [(self.by_path,legacy.norm(info.filename)),(self.by_base,legacy.base(info.filename)),(self.by_stem,legacy.stem(info.filename)),(self.by_base_stem,legacy.stem(legacy.base(info.filename)))]:
                        mapping[key].append(photo)
            self.json_infos=[]
            for n,info in enumerate(self.archives[str(self.json_path)].infolist()):
                self.entry_map[(str(self.json_path),info.filename)].append(info)
                if not info.is_dir() and legacy.base(info.filename).endswith('.json'):
                    self.json_infos.append((n,info))
            if not self.photos or not self.json_infos:
                raise ValueError('Supported photos and JSON entries are required')
        except Exception:
            self.close()
            raise

    def close(self):
        for z in self.archives.values():
            z.close()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()

    def bytes(self, zip_path, entry, limit=MAX_IMAGE):
        key=(str(Path(zip_path).resolve()),entry)
        choices=self.entry_map.get(key,[])
        if len(choices)!=1:
            raise ValueError('Archive entry is missing or ambiguous')
        info=choices[0]
        if info.file_size>limit:
            raise ValueError('Entry size limit exceeded')
        with self.archives[key[0]].open(info) as handle:
            payload=handle.read(limit+1)
        if len(payload)>limit:
            raise ValueError('Entry size limit exceeded')
        return payload

    def image(self, zip_path, entry):
        payload=self.bytes(zip_path,entry)
        with Image.open(io.BytesIO(payload)) as im:
            im.load()
            image=im.convert('RGB')
        return image,hashlib.sha256(payload).hexdigest()

    def match(self, info, doc):
        image_path=doc.get('imagePath') if isinstance(doc,dict) else None
        if isinstance(image_path,str) and image_path.strip():
            relative=legacy.norm(posixpath.join(posixpath.dirname(legacy.norm(info.filename)),image_path.replace('\\','/')))
            strict={p['id']:p for key in (legacy.norm(image_path),relative) for p in self.by_path.get(key,[])}
            if strict:
                return list(strict.values()),'strict_imagePath_or_json_relative'
            return self.by_base.get(legacy.base(image_path),[]),'basename_imagePath'
        for mapping,key,method in [(self.by_stem,legacy.stem(info.filename),'json_relative_stem'),(self.by_base_stem,legacy.stem(legacy.base(info.filename)),'json_basename_stem')]:
            if mapping.get(key):
                return mapping[key],method
        return [],'unmatched'

def init_workspace(args):
    # Inventory only; never decrypt or copy photos for workspace initialization.
    with contextlib.closing(Dataset(args.photos,args.json_zip,ask_password=False)) as dataset:
        workspace=args.workspace.resolve()
        workspace.mkdir(parents=True,exist_ok=True)
        config={'schema_version':2,'photos':[str(p) for p in dataset.paths], 'json_zip':str(dataset.json_path),
                'references':[], 'targets':[], 'reference_policy':'human reviewed visible per-plane patches; automatic projections remain provisional'}
        path=workspace/'project.json'
        with path.open('x',encoding='utf-8') as handle:
            json.dump(config,handle,ensure_ascii=False,indent=2)
        print(f'Prepared: {path}; photos={len(dataset.photos)}, JSON={len(dataset.json_infos)}; reviewed references=0')

def load_references(config,dataset,allow_ai=False):
    refs=[]
    for raw in config.get('references',[]):
        human=raw.get('reviewed') is True and raw.get('reference_origin','human_reviewed')=='human_reviewed'
        ai=raw.get('reference_origin')=='assistant_visual_draft' and allow_ai
        if (not human and not ai) or not str(raw.get('reviewer','')).strip():
            raise ValueError('Each reference must be explicitly human reviewed with reviewer')
        source=raw['source']
        image,sha=dataset.image(source['zip'],source['entry'])
        if sha!=source.get('sha256') or list(image.size)!=raw.get('size'):
            raise ValueError('Reference source hash/size changed; review again')
        regions=checked_regions(raw['regions'],image.size)
        refs.append(dict(raw,image=image,regions=regions,human_confirmed=human))
    return refs

def reviewed_target(config,photo,image,sha):
    entries=[t for t in config.get('targets',[]) if str(Path(t.get('source',{}).get('zip','')).resolve())==photo['zip'] and t.get('source',{}).get('entry')==photo['entry']]
    if len(entries)>1:
        raise ValueError('Duplicate reviewed target specifications')
    if not entries:
        return None
    raw=entries[0]
    if raw.get('reviewed') is not True or not str(raw.get('reviewer','')).strip() or raw['source'].get('sha256')!=sha or raw.get('size')!=list(image.size):
        raise ValueError('Target ROI lacks matching hash, size or human review')
    return checked_regions(raw['regions'],image.size)

def reference_for(photo,image,sha,refs,config):
    direct=reviewed_target(config,photo,image,sha)
    if direct is not None:
        return {'regions':direct,'evidence':[{'status':'reviewed_target_roi'}],'basis':'reviewed_image','reference_id':'per_image_review','accepted':True}
    for ref in refs:
        if sha==ref['source']['sha256']:
            projected=project_regions(ref['image'],image,ref['regions'],source_identity=True)
            if not ref['human_confirmed']:
                projected['basis']='provisional_reference_projection'
                projected['regions']=[dict(region,basis='provisional_reference_projection',human_confirmed=False,reference_origin='assistant_visual_draft') for region in projected['regions']]
                projected['evidence']=[dict(e,basis='provisional_reference_projection',human_confirmed=False,reference_origin='assistant_visual_draft') for e in projected['evidence']]
                projected['evidence'].append({'status':'assistant_visual_draft','human_confirmed':False,'visibility':'assistant reviewed this exact source image'})
            return dict(projected,reference_id=ref['id'])
    # Never guess the site from text prefixes: only compare refs from the same ZIP.
    candidates=[]
    failed=[]
    for ref in refs:
        if str(Path(ref['source']['zip']).resolve())!=photo['zip']:
            continue
        proposal=project_regions(ref['image'],image,ref['regions'])
        proposal['reference_id']=ref['id']
        if proposal.get('regions'):
            candidates.append(proposal)
        else:
            failed.append(proposal)
    if len(candidates)==1:
        return candidates[0]
    if len(candidates)>1:
        # Pool separately fitted patches; reject mutually contradictory teacher labels.
        regions=[];evidence=[];identifiers=[]
        for proposal in candidates:
            identifiers.append(proposal['reference_id'])
            evidence.extend(dict(e,reference_id=proposal['reference_id']) for e in proposal['evidence'])
            for region in proposal['regions']:
                regions.append(dict(region,id=proposal['reference_id']+':'+region['id']))
        contradictory=set()
        for i,first in enumerate(regions):
            for j,second in enumerate(regions[i+1:],i+1):
                if second['label'].casefold() in [x.casefold() for x in first.get('conflict_labels',[])] or first['label'].casefold() in [x.casefold() for x in second.get('conflict_labels',[])]:
                    a,b=Polygon(first['points']),Polygon(second['points'])
                    if a.intersection(b).area/min(a.area,b.area)>.25:
                        contradictory.update([i,j])
        regions=[r for n,r in enumerate(regions) if n not in contradictory]
        if contradictory:evidence.append({'status':'abstain','reason':'CONTRADICTORY_REFERENCE_REGIONS','region_indices':sorted(contradictory)})
        return {'regions':regions,'evidence':evidence,'basis':'provisional_reference_projection','reference_id':';'.join(identifiers),'accepted':not contradictory and all(c.get('accepted') for c in candidates)}
    return {'regions':[],'evidence':[{'status':'abstain','reason':'NO_USABLE_REFERENCE_MATCH','attempts':[{'reference_id':p['reference_id'],'evidence':p['evidence']} for p in failed]}], 'basis':'provisional_reference_projection','reference_id':'','accepted':False}

def batch(args):
    config=read_json(args.workspace/'project.json')
    run_dir=args.out.resolve()/datetime.now(timezone.utc).strftime('run_%Y%m%dT%H%M%S_%fZ')
    run_dir.mkdir(parents=True,exist_ok=False)
    with contextlib.closing(Dataset(config['photos'],config['json_zip'])) as dataset:
        refs=load_references(config,dataset,args.allow_ai_references)
        selected=dataset.photos
        limited=args.limit>0 or args.entries_file is not None
        if args.entries_file is not None:
            wanted=read_json(args.entries_file)
            keys={(str(Path(p['zip']).resolve()),p['entry']) for p in wanted}
            selected=[p for p in dataset.photos if (p['zip'],p['entry']) in keys]
            if len(selected)!=len(keys):raise ValueError('Selected entries are missing or ambiguous')
        elif limited:
            # Round-robin ZIPs; enough to smoke all sites without claiming representative sampling.
            groups=[[p for p in dataset.photos if p['zip']==str(path)] for path in dataset.paths]
            selected=[]
            for n in range(max(len(g) for g in groups)):
                for group in groups:
                    if n<len(group):
                        selected.append(group[n])
                    if len(selected)>=args.limit:
                        break
                if len(selected)>=args.limit:
                    break
        selected_ids={p['id'] for p in selected}
        selected_stems={legacy.stem(legacy.base(p['entry'])) for p in selected}
        records={p['id']:{'id':p['id'],'record_type':'image','photo_zip':p['zip'],'photo_entry':p['entry'],'json_zip':str(dataset.json_path),'json_entry':'','json_entries':[], 'issues':[], 'warnings':[], 'semantic_status':'not_checked','regions':[],'metrics':[],'registration':[],'duplicate_groups':[],'reference_id':'','correction_file':None} for p in selected}
        extra=[]; uncertain=set(); index_complete=True; correction_drafts={}; decoded={}; errors=Counter(); processed=0
        json_infos=[(n,i) for n,i in dataset.json_infos if not limited or legacy.stem(legacy.base(i.filename)) in selected_stems]
        def decode(photo):
            if photo['id'] not in decoded or decoded[photo['id']][0] is None:
                decoded[photo['id']]=dataset.image(photo['zip'],photo['entry'])
            return decoded[photo['id']]
        for entry_n,info in json_infos:
            processed+=1
            doc=None; error=None
            try:
                if info.file_size>MAX_JSON:
                    raise ValueError('JSON_SIZE_LIMIT')
                # ZipInfo preserves duplicate ZIP-entry identity while pairing remains conservative.
                with dataset.archives[str(dataset.json_path)].open(info) as handle:
                    payload=handle.read(MAX_JSON+1)
                if len(payload)>MAX_JSON:
                    raise ValueError('JSON_SIZE_LIMIT')
                doc=strict_loads(payload)
            except Exception as exc:
                error='JSON_NONFINITE_CONSTANT' if str(exc).startswith('JSON_NONFINITE_CONSTANT_') else 'JSON_PARSE_OR_READ_ERROR_'+type(exc).__name__
                errors[error]+=1; index_complete=False
            choices,method=dataset.match(info,doc)
            if limited and choices and all(p['id'] not in selected_ids for p in choices):
                continue
            photo=choices[0] if len(choices)==1 and choices[0]['id'] in records else None
            if photo:
                r=records[photo['id']]
            else:
                r={'id':f'J{entry_n}','record_type':'unmatched_json','photo_zip':'','photo_entry':'','json_zip':str(dataset.json_path),'json_entry':info.filename,'json_entries':[], 'issues':[], 'warnings':[], 'semantic_status':'abstain','regions':[],'metrics':[],'registration':[],'duplicate_groups':[],'reference_id':'','correction_file':None}
                extra.append(r)
                uncertain.update(p['id'] for p in choices)
                r['issues'].append('AMBIGUOUS_IMAGE_PAIR' if choices else 'ORPHAN_JSON' if isinstance(doc,dict) and isinstance(doc.get('imagePath'),str) and doc['imagePath'].strip() else 'UNRESOLVED_JSON_PAIR')
            r['json_entries'].append(f'J{entry_n}:{info.filename}')
            r['json_entry']=info.filename
            r['warnings'].append('MATCH_METHOD_'+method)
            if error:
                r['issues'].append(error);r['semantic_status']='abstain'
                continue
            r['source_json_sha256']=hashlib.sha256(payload).hexdigest()
            legacy_record=legacy.row('image')
            _,declared,_=legacy.validate(doc,legacy_record)
            r['issues'].extend(legacy_record['issues']);r['warnings'].extend(legacy_record['warnings'])
            regions=[];basis='reviewed_image'; actual=declared; sha=None
            if photo and (refs or config.get('targets') or args.check_images):
                try:
                    image,sha=decode(photo);actual=image.size
                    if declared and tuple(declared)!=tuple(actual):
                        r['issues'].append('IMAGE_SIZE_MISMATCH');r['semantic_status']='abstain'
                    elif refs or config.get('targets'):
                        mapping=reference_for(photo,image,sha,refs,config)
                        regions=mapping['regions'];basis=mapping['basis']
                        r['regions']=regions;r['registration']=mapping['evidence'];r['reference_id']=mapping['reference_id']
                        if not regions:
                            r['semantic_status']='abstain';r['warnings'].append('REFERENCE_UNUSABLE_OR_AMBIGUOUS')
                        elif not mapping.get('accepted', False):
                            r['warnings'].append('SOME_REFERENCE_REGIONS_UNUSABLE')
                except Exception as exc:
                    r['issues'].append('IMAGE_OR_REFERENCE_ERROR_'+type(exc).__name__);r['semantic_status']='abstain'
            if actual:
                outcome=analyze(doc,regions,actual,basis=basis,raster_diagnostics=getattr(args,'raster_diagnostics',False))
                r['issues'].extend(outcome['issues']);r['warnings'].extend(outcome['warnings'])
                r['metrics'].extend(outcome['metrics']);r['duplicate_groups'].extend(outcome['duplicate_groups'])
                if regions:
                    r['semantic_status']=outcome['semantic_status']
                    if 'SOME_REFERENCE_REGIONS_UNUSABLE' in r['warnings']:
                        r['semantic_status']='partial_abstain'
                elif not refs and not config.get('targets'):
                    r['warnings'].append('NO_REVIEWED_TEACHER_CRITERIA')
                if args.write_corrections and photo and outcome['corrected_doc'] is not None and len(dataset.entry_map[(str(dataset.json_path),info.filename)])==1:
                    correction_drafts[photo['id']]={'doc':outcome['corrected_doc'],'actions':outcome['correction_actions'],'source_json_sha256':hashlib.sha256(payload).hexdigest(),'source_json_entry':info.filename}
            # Keep only teacher RGB and current target; never retain all decoded RGBs.
            if photo and photo['id'] in decoded:
                decoded[photo['id']]=(None,decoded[photo['id']][1])
            if processed%100==0:
                print(f'JSON processed: {processed}/{len(json_infos)}',flush=True)
        decode_success=0;decode_failure=0
        for photo in selected:
            r=records[photo['id']]
            if not r['json_entries']:
                r['issues'].append('AMBIGUOUS_JSON_PAIR' if photo['id'] in uncertain else 'JSON_INDEX_INCOMPLETE' if not index_complete else 'UNRESOLVED_JSON_PAIR_IN_LIMITED_SCOPE' if limited else 'MISSING_JSON')
                if refs or config.get('targets'):
                    try:
                        image,sha=dataset.image(photo['zip'],photo['entry'])
                        mapping=reference_for(photo,image,sha,refs,config)
                        r['regions']=mapping['regions'];r['registration']=mapping['evidence'];r['reference_id']=mapping['reference_id']
                        # Missing JSON != missing annotation. No semantic absence inference here.
                        r['semantic_status']='abstain';r['warnings'].append('ANNOTATION_MISSING_CANNOT_MEASURE_LABEL_COVERAGE')
                    except Exception as exc:
                        r['issues'].append('IMAGE_OR_REFERENCE_ERROR_'+type(exc).__name__);r['semantic_status']='abstain'
            if len(r['json_entries'])>1:
                r['issues'].append('MULTIPLE_JSON_FOR_IMAGE');r['semantic_status']='abstain';correction_drafts.pop(photo['id'],None)
            if args.check_images:
                if photo['id'] in decoded:
                    decode_success+=1
                else:
                    try:
                        dataset.image(photo['zip'],photo['entry']);decode_success+=1
                    except Exception as exc:
                        decode_failure+=1;r['issues'].append('IMAGE_DECODE_ERROR_'+type(exc).__name__)
        hash_candidates=[];digest_groups=defaultdict(list)
        groups=defaultdict(list)
        for photo in selected:
            # Same bytes => same size; size grouping also covers encrypted zero CRC metadata.
            groups[photo['info'].file_size].append(photo)
        for group in groups.values():
            if len(group)>1:
                hash_candidates.extend(group)
        hashed=0
        if args.hash_duplicates:
            for photo in hash_candidates:
                try:
                    digest=hashlib.sha256()
                    with dataset.archives[photo['zip']].open(photo['info']) as handle:
                        while True:
                            chunk=handle.read(1024*1024)
                            if not chunk:break
                            digest.update(chunk)
                    digest_groups[digest.hexdigest()].append(photo);hashed+=1
                except Exception as exc:
                    records[photo['id']]['issues'].append('HASH_READ_ERROR_'+type(exc).__name__)
        exact_groups=[g for g in digest_groups.values() if len(g)>1]
        for n,group in enumerate(exact_groups,1):
            for p in group:
                records[p['id']]['issues'].append(f'EXACT_DUPLICATE_IMAGE_GROUP_{n}')
        if args.write_corrections:
            for identifier,draft in correction_drafts.items():
                r=records[identifier]
                correction_path=run_dir/'corrections'/f'{identifier.replace(":","_")}.json'
                correction_path.parent.mkdir(exist_ok=True)
                atomic_json(correction_path,draft['doc'])
                atomic_json(correction_path.with_suffix('.provenance.json'),{'needs_review':True,'training_eligible':False,'label_origin':'exact_duplicate_cleanup_draft','source_photo_zip':r['photo_zip'],'source_photo_entry':r['photo_entry'],'source_json_zip':str(dataset.json_path),'source_json_entry':draft['source_json_entry'],'source_json_sha256':draft['source_json_sha256'],'actions':draft['actions'],'semantic_status':r['semantic_status'],'reference_id':r['reference_id'],'note':'Only redundant equivalent shapes removed; no wall/ceiling relabel or invented missing mask'})
                r['correction_file']=str(correction_path)
        output=list(records.values())+extra
        uncertain_codes=('ERROR','AMBIGUOUS','INCOMPLETE','UNRESOLVED','UNSUPPORTED')
        for r in output:
            r['issues']=list(dict.fromkeys(r['issues']));r['warnings']=list(dict.fromkeys(r['warnings']))
            candidate=any(not any(x in issue for x in uncertain_codes) for issue in r['issues'])
            r['quality_status']='review_candidate' if candidate else 'abstain' if r['issues'] or r['semantic_status'] in {'abstain','partial_abstain'} else 'no_rule_trigger'
            r['visual_quality_pass']=False
        with (run_dir/'quality.csv').open('x',encoding='utf-8-sig',newline='') as handle:
            fields=['id','record_type','photo_zip','photo_entry','json_zip','json_entry','quality_status','semantic_status','reference_id','issues','warnings','correction_file']
            writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
            for r in output:
                writer.writerow({k:';'.join(r[k]) if isinstance(r[k],list) else r[k] for k in fields})
        summary={'schema_version':2,'scope':'selected_entries' if args.entries_file else 'limited_round_robin_smoke' if limited else 'full_dataset','requested_limit':args.limit,
                 'selected_photos':len(selected),'total_photo_entries':len(dataset.photos),'processed_json':processed,'total_json_entries':len(dataset.json_infos),
                 'human_reviewed_reference_count':sum(r['human_confirmed'] for r in refs),'assistant_reference_draft_count':sum(not r['human_confirmed'] for r in refs),'loaded_reference_count':len(refs),'reviewed_target_count':len(config.get('targets',[])),
                 'json_index_complete_in_scope':index_complete,'json_errors':dict(errors),
                 'image_quality_counts':dict(Counter(r['quality_status'] for r in records.values())),
                 'semantic_counts':dict(Counter(r['semantic_status'] for r in records.values())),
                 'issue_counts':dict(Counter(code for r in output for code in r['issues'])),
                 'image_decode_successes':decode_success,'image_decode_failures':decode_failure,'hash_candidate_images':len(hash_candidates),'hashed_images':hashed,'exact_duplicate_groups':len(exact_groups),'correction_drafts':sum(bool(r['correction_file']) for r in output),
                 'meaning':{'no_rule_trigger':'NOT a visual pass','automatic_reference_projection':'provisional; target visibility not verified; candidates require review','corrections':'equivalent redundant annotation removal only; original preserved; not semantic GT'},
                 'settings':{'check_images':args.check_images,'hash_duplicates':args.hash_duplicates,'write_corrections':args.write_corrections,'allow_ai_references':args.allow_ai_references,'raster_diagnostics':getattr(args,'raster_diagnostics',False),'workspace':str(args.workspace.resolve())}}
        atomic_json(run_dir/'results.json',{'summary':summary,'results':output})
        atomic_json(run_dir/'summary.json',summary)
        atomic_json(args.workspace/'latest_run.json',{'path':str(run_dir)})
        print(f'Saved: {run_dir}',flush=True)
        print(json.dumps(summary,ensure_ascii=False),flush=True)
    return run_dir

def serve(args):
    workspace=args.workspace.resolve();project_path=workspace/'project.json';config=read_json(project_path)
    dataset=Dataset(config['photos'],config['json_zip'])
    token=secrets.token_urlsafe(24)
    decisions_path=workspace/'review_decisions.json'
    corrections_index_path=workspace/'reviewed_corrections.json'
    save_lock=threading.RLock()
    def current_results():
        latest=workspace/'latest_run.json'
        if not latest.exists():return []
        run_dir=Path(read_json(latest)['path']).resolve()
        # latest points only to runs created by this app, validated before correction downloads.
        records=read_json(run_dir/'results.json')['results']
        overrides=read_json(corrections_index_path) if corrections_index_path.exists() else {}
        for record in records:
            reviewed=overrides.get(str(run_dir)+'\n'+record['id'])
            if reviewed:
                record['correction_file']=reviewed['correction_file']
                record['human_reviewed_removed_source_indices']=reviewed['removed_source_indices']
        return records
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def send(self,status,payload,content_type='application/json; charset=utf-8'):
            if isinstance(payload,(dict,list)):
                payload=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
            elif isinstance(payload,str):payload=payload.encode('utf-8')
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def args(self):
            u=urlparse(self.path);return u.path,{k:v[0] for k,v in parse_qs(u.query).items()}
        def do_GET(self):
            try:
                if self.headers.get('Host') not in {f'127.0.0.1:{server.server_address[1]}',f'localhost:{server.server_address[1]}'}:
                    return self.send(403,{'error':'Local loopback host required'})
                route,query=self.args()
                if route=='/':
                    html=(Path(__file__).parent/'review.html').read_text(encoding='utf-8').replace('__QA_TOKEN__',token)
                    return self.send(200,html,'text/html; charset=utf-8')
                if route=='/api/state':
                    current=read_json(project_path)
                    return self.send(200,{'references':current.get('references',[]),'results':current_results(),'photos':[{'zip':p['zip'],'entry':p['entry']} for p in dataset.photos[:50]],'json_zip':current['json_zip']})
                if route=='/api/search':
                    query_text=query.get('q','').casefold()
                    return self.send(200,[{'zip':p['zip'],'entry':p['entry']} for p in dataset.photos if query_text in p['entry'].casefold()][:50])
                if route=='/api/image':
                    if str(Path(query['zip']).resolve()) not in {str(p) for p in dataset.paths}:
                        raise ValueError('Photo ZIP is not configured')
                    image,_=dataset.image(query['zip'],query['entry'])
                    # View JPEG is generated in memory, never stored as a copy of original.
                    stream=io.BytesIO();image.save(stream,format='JPEG',quality=90)
                    return self.send(200,stream.getvalue(),'image/jpeg')
                if route=='/api/annotation':
                    if str(Path(query['zip']).resolve())!=str(dataset.json_path):
                        raise ValueError('JSON ZIP is not configured')
                    return self.send(200,strict_loads(dataset.bytes(query['zip'],query['entry'],MAX_JSON)))
                if route=='/api/correction':
                    record=next((r for r in current_results() if r['id']==query.get('id')),None)
                    overrides=read_json(corrections_index_path) if corrections_index_path.exists() else {}
                    latest=Path(read_json(workspace/'latest_run.json')['path']).resolve()
                    key=str(latest)+'\n'+query.get('id','')
                    correction=overrides.get(key,{}).get('correction_file') or (record.get('correction_file') if record else None)
                    if not correction:
                        raise ValueError('No correction draft for this record')
                    file=Path(correction).resolve()
                    if file.parent not in {latest/'corrections',workspace/'reviewed_corrections'} or file.suffix!='.json':
                        raise ValueError('Invalid correction path')
                    return self.send(200,file.read_bytes())
                self.send(404,{'error':'Not found'})
            except Exception as exc:
                self.send(400,{'error':str(exc)})
        def do_POST(self):
            try:
                if self.headers.get('Host') not in {f'127.0.0.1:{server.server_address[1]}',f'localhost:{server.server_address[1]}'}:
                    return self.send(403,{'error':'Local loopback host required'})
                if self.headers.get('X-QA-Token')!=token:
                    return self.send(403,{'error':'Missing local review token'})
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024*1024:
                    raise ValueError('Request size limit')
                body=strict_loads(self.rfile.read(length));route,_=self.args()
                if route=='/api/reference':
                    if body.get('reviewed') is not True or not isinstance(body.get('reviewer'),str) or not body['reviewer'].strip():
                        raise ValueError('Explicit human review and reviewer required')
                    source=body['source'];source_zip=str(Path(source['zip']).resolve())
                    if source_zip not in {str(p) for p in dataset.paths}:
                        raise ValueError('Reference ZIP not configured')
                    image,sha=dataset.image(source_zip,source['entry'])
                    regions=checked_regions(body['regions'],image.size)
                    source_key=source_zip+'\n'+source['entry']
                    identifier='REF_'+hashlib.sha256(source_key.encode()).hexdigest()[:16]
                    item={'id':identifier,'source':{'zip':source_zip,'entry':source['entry'],'sha256':sha},'size':list(image.size),'regions':regions,'reviewed':True,'human_confirmed':True,'reference_origin':'human_reviewed','reviewer':body['reviewer'].strip(),'reviewed_at':datetime.now(timezone.utc).isoformat()}
                    with save_lock:
                        current=read_json(project_path)
                        current['references']=[r for r in current.get('references',[]) if (str(Path(r['source']['zip']).resolve()),r['source']['entry'])!=(source_zip,source['entry'])]+[item]
                        atomic_json(project_path,current)
                    return self.send(200,item)
                if route=='/api/review':
                    if body.get('decision') not in ['confirmed_candidate','false_positive','needs_review']:
                        raise ValueError('Invalid review decision')
                    if body.get('result_id') not in {r['id'] for r in current_results()}:
                        raise ValueError('Unknown result')
                    with save_lock:
                        decisions=read_json(decisions_path) if decisions_path.exists() else []
                        decisions.append(dict(body,run=str(read_json(workspace/'latest_run.json')['path']),reviewed_at=datetime.now(timezone.utc).isoformat()))
                        atomic_json(decisions_path,decisions)
                    return self.send(200,{'saved':True})
                if route=='/api/correct':
                    if body.get('confirmed_same_object') is not True or not isinstance(body.get('reviewer'),str) or not body['reviewer'].strip():
                        raise ValueError('Human confirmation of same object/correct chosen shape and reviewer required')
                    record=next((r for r in current_results() if r['id']==body.get('result_id')),None)
                    if not record or len(record['json_entries'])!=1 or not record['json_entry']:
                        raise ValueError('Unique annotation association required')
                    keep=body.get('keep_shape_index');remove=body.get('remove_shape_indices')
                    if not isinstance(keep,int) or isinstance(keep,bool) or not isinstance(remove,list) or not remove or not all(isinstance(n,int) and not isinstance(n,bool) for n in remove) or keep in remove or len(set(remove))!=len(remove):
                        raise ValueError('Invalid shape selection')
                    proposed={keep,*remove}
                    # User may resolve only groups that the geometric checker actually proposed.
                    groups=[set(g.get('indices',g.get('shape_indices',[]))) for g in record['duplicate_groups']]
                    if not any(proposed.issubset(group) for group in groups):
                        raise ValueError('Selection is not an audited duplicate group')
                    payload=dataset.bytes(record['json_zip'],record['json_entry'],MAX_JSON)
                    if hashlib.sha256(payload).hexdigest()!=record.get('source_json_sha256'):
                        raise ValueError('Source JSON differs from audited bytes; rerun audit before correction')
                    original=strict_loads(payload);shapes=original.get('shapes')
                    if not isinstance(shapes,list) or not all(0<=n<len(shapes) for n in proposed):
                        raise ValueError('Source shape indices changed')
                    latest=Path(read_json(workspace/'latest_run.json')['path']).resolve()
                    key=str(latest)+'\n'+record['id']
                    with save_lock:
                        overrides=read_json(corrections_index_path) if corrections_index_path.exists() else {}
                        prior=overrides.get(key,{})
                        prior_removed=set(prior.get('removed_source_indices',[]))
                        if keep in prior_removed:
                            raise ValueError('Selected shape was already removed by an earlier review')
                        all_removed=prior_removed|set(remove)
                        draft=deepcopy(original);draft['shapes']=[s for n,s in enumerate(shapes) if n not in all_removed]
                        folder=workspace/'reviewed_corrections';folder.mkdir(exist_ok=True)
                        path=folder/(hashlib.sha256(key.encode()).hexdigest()[:20]+'_'+secrets.token_hex(4)+'.json')
                        atomic_json(path,draft)
                        provenance={'source_json_zip':record['json_zip'],'source_json_entry':record['json_entry'],'source_json_sha256':hashlib.sha256(payload).hexdigest(),'kept_source_index':keep,'removed_source_indices':sorted(all_removed),'reviewer':body['reviewer'].strip(),'human_confirmed_same_object':True,'needs_review':True,'training_eligible':False,'label_origin':'human_selected_duplicate_cleanup_draft','correction_file':str(path)}
                        atomic_json(path.with_suffix('.provenance.json'),provenance)
                        overrides[key]=provenance;atomic_json(corrections_index_path,overrides)
                    return self.send(200,{'correction_file':str(path),'url':'/api/correction?id='+record['id']})
                self.send(404,{'error':'Not found'})
            except Exception as exc:self.send(400,{'error':str(exc)})
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Local review: http://127.0.0.1:{server.server_address[1]}/',flush=True)
    try:server.serve_forever()
    finally:server.server_close();dataset.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--photos',nargs='+',required=True,type=Path);init.add_argument('--json-zip',required=True,type=Path);init.add_argument('--workspace',required=True,type=Path)
    run=sub.add_parser('run');run.add_argument('--workspace',required=True,type=Path);run.add_argument('--out',required=True,type=Path);run.add_argument('--limit',type=int,default=0);run.add_argument('--entries-file',type=Path);run.add_argument('--check-images',action='store_true');run.add_argument('--hash-duplicates',action='store_true');run.add_argument('--write-corrections',action='store_true');run.add_argument('--allow-ai-references',action='store_true');run.add_argument('--raster-diagnostics',action='store_true')
    view=sub.add_parser('serve');view.add_argument('--workspace',required=True,type=Path);view.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    if args.command=='init':init_workspace(args)
    elif args.command=='run':
        if args.limit<0:parser.error('--limit must be >=0')
        batch(args)
    else:serve(args)

if __name__=='__main__':main()
