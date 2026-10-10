"""Local five-class annotation drafts; neither states nor progress are inferred."""
from __future__ import annotations
import argparse, csv, hashlib, html, json, shutil, time
from collections import Counter
from pathlib import Path
from urllib.parse import quote
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw
from person_detector import PersonDetector
from dino_detector import GroundingDetector

CLASSES={'wall':1,'floor':2,'ceiling':3,'person':4,'other':5}
COLORS={1:[85,165,255],2:[75,210,110],3:[180,105,245],4:[255,95,80],5:[245,200,70]}
SURFACES=['wall','floor','ceiling']
OTHER_LABELS=['ladder','bucket','scaffolding','tools','cardboard box']
# Model paths are provided explicitly; no weights are downloaded or bundled.

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def save_json(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

class SamCPU:
    def __init__(self,encoder,decoder,threads=4):
        options=ort.SessionOptions();options.intra_op_num_threads=threads
        options.inter_op_num_threads=1;options.log_severity_level=3
        self.encoder=ort.InferenceSession(str(encoder),options,providers=['CPUExecutionProvider'])
        self.decoder=ort.InferenceSession(str(decoder),options,providers=['CPUExecutionProvider'])
        shape=self.encoder.get_inputs()[0].shape;self.h,self.w=int(shape[2]),int(shape[3])
        self.info={'encoder_path':str(encoder),'decoder_path':str(decoder),
                   'encoder_sha256':sha256(encoder),'decoder_sha256':sha256(decoder),
                   'provider':'CPUExecutionProvider','threads':threads}
    def encode(self,rgb):
        f=rgb.astype(np.float32)/255
        x=np.stack([np.asarray(Image.fromarray(f[:,:,c]).resize((self.w,self.h),Image.Resampling.BILINEAR),dtype=np.float32) for c in range(3)])
        x-=np.array([.485,.456,.406],dtype=np.float32)[:,None,None]
        x/=np.array([.229,.224,.225],dtype=np.float32)[:,None,None]
        return self.encoder.run(None,{self.encoder.get_inputs()[0].name:x[None]})
    def predict(self,features,box,size,prompt_points=None):
        w,h=size
        prompt_points=prompt_points or []
        points=np.array([[box[:2],box[2:]]+[p['xy'] for p in prompt_points]],dtype=np.float32)
        points[...,0]*=self.w/w;points[...,1]*=self.h/h
        values=[features[2],features[0],features[1],points,np.array([[2,3]+[p['label'] for p in prompt_points]],dtype=np.float32),
                np.zeros((1,1,self.h//4,self.w//4),dtype=np.float32),np.array([0],dtype=np.float32)]
        masks,scores=self.decoder.run(None,{v.name:x for v,x in zip(self.decoder.get_inputs(),values)})[:2]
        scores=np.asarray(scores).reshape(-1);selected=int(np.argmax(scores))
        mask=cv2.resize(masks[0,selected],(w,h),interpolation=cv2.INTER_LINEAR)>0
        return mask,scores.tolist(),selected

def fuse(class_masks,ignore):
    """Unknown is 255. Recognized foreground objects occlude surface proposals.

    An unassigned remainder is a candidate-other display only, never canonical GT.
    Cross-surface overlap and overlapping person/other object proposals stay unknown.
    """
    shape=ignore.shape
    semantic=np.full(shape,255,np.uint8)
    count=sum(class_masks[name].astype(np.uint8) for name in SURFACES)
    surface_conflict=count>1
    for name in SURFACES: semantic[class_masks[name]&(count==1)]=CLASSES[name]
    person,other=class_masks['person'],class_masks['other']
    object_conflict=person&other
    semantic[other&~person]=5;semantic[person&~other]=4
    conflict=(surface_conflict&~(person|other))|object_conflict
    semantic[conflict|ignore]=255
    remainder=(semantic==255)&~conflict&~ignore
    candidate=semantic.copy();candidate[remainder]=5
    return semantic,candidate,remainder,conflict

def polygon_shapes(mask,label,group):
    contours,hierarchy=cv2.findContours(mask.astype(np.uint8),cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
    shapes=[]
    if hierarchy is None: return shapes
    for i,(c,relation) in enumerate(zip(contours,hierarchy[0])):
        points=c.reshape(-1,2)
        if relation[3]>=0 or len(points)<3: continue
        holes=[v.reshape(-1,2).tolist() for v,r in zip(contours,hierarchy[0]) if r[3]==i]
        shapes.append({'label':label,'points':points.tolist(),'shape_type':'polygon',
          'group_id':group+i,'flags':{'draft_display_only':True},'score':None,
          'description':'UNREVIEWED: PNG is canonical; polygon holes may be filled by app',
          'hole_contours':holes})
    return shapes

def load_view_config(path):
    """Read optional private per-image/view settings, kept outside Git."""
    if path is None:
        return {'images': {}, 'time_ranges': []}
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('View settings must be a JSON object')
    if not isinstance(config.get('images', {}), dict) or not isinstance(config.get('time_ranges', []), list):
        raise ValueError('Expected images object and time_ranges list')
    return config


def view_guard(record, size, config=None):
    """Apply caller-provided geometry; no company coordinates are built in.

    Time ranges are only user-selected candidates, not verified camera identity.
    A per-image rule overrides the fields from matching time-range rules.
    All coordinates use the rule's declared reference_size, in pixel xy order.
    """
    config = config or {'images': {}, 'time_ranges': []}
    stamp = record.get('captured_at_candidate')
    rule = {}
    for entry in config.get('time_ranges', []):
        if not isinstance(entry, dict) or not isinstance(entry.get('rule'), dict):
            raise ValueError('Each time range needs start/end strings and a rule object')
        start, end = entry.get('start'), entry.get('end')
        if not isinstance(start, str) or not isinstance(end, str) or start > end:
            raise ValueError('Invalid private time range')
        if stamp and start <= stamp <= end:
            rule.update(entry['rule'])
    key = record.get('image_id') or Path(record['filename']).stem
    image_rule = config.get('images', {}).get(key, {})
    if not isinstance(image_rule, dict):
        raise ValueError('Image rule must be an object')
    rule.update(image_rule)
    w, h = size
    foreground = np.zeros((h, w), bool)
    if not rule:
        return {'mode': 'general_view', 'withheld_dino_surface_classes': [],
                'manual_prompts': [], 'foreground_platform_ignore_polygon_applied': False,
                'physical_camera_id_verified': False, 'human_reviewed': False}, [], foreground
    ref = np.asarray(rule.get('reference_size', size), dtype=float)
    if ref.shape != (2,) or not np.isfinite(ref).all() or (ref <= 0).any():
        raise ValueError('reference_size must contain positive width and height')
    scale = np.asarray([w, h], dtype=float) / ref
    def points_in_frame(values, minimum=1):
        points = np.asarray(values, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or len(points) < minimum:
            raise ValueError('Expected reference pixel xy points')
        if not np.isfinite(points).all() or (points < 0).any() or (points > ref).any():
            raise ValueError('Prompt coordinates exceed declared reference_size')
        return points * scale
    withhold = rule.get('withheld_surface_classes', [])
    if not isinstance(withhold, list) or any(label not in SURFACES for label in withhold):
        raise ValueError('Only wall/floor/ceiling can be withheld')
    manual = []
    for proposal in rule.get('manual_prompts', []):
        label = proposal['class_name']
        if label not in CLASSES:
            raise ValueError('Unknown manual prompt class')
        raw_box = np.asarray(proposal['xyxy'], dtype=float)
        if raw_box.shape != (4,):
            raise ValueError('Manual box must have four xyxy values')
        corners = points_in_frame(raw_box.reshape(2, 2))
        if (corners[1] <= corners[0]).any():
            raise ValueError('Manual box must have positive width and height')
        prompts = []
        for point in proposal.get('points', []):
            if point.get('label') not in (0, 1):
                raise ValueError('Point label must be 0 or 1')
            xy = points_in_frame([point['xy']])[0].tolist()
            prompts.append({'xy': xy, 'label': point['label']})
        manual.append({'class_name': label, 'output_class': label,
                       'origin': 'local_view_config', 'proposal_label': label,
                       'score': None, 'xyxy': corners.reshape(-1).tolist(), 'points': prompts,
                       'needs_review': True, 'label_origin': 'registered_prompt_not_ground_truth'})
    for polygon in rule.get('foreground_ignore_polygons', []):
        coords = points_in_frame(polygon, minimum=3)
        cv2.fillPoly(foreground.view(np.uint8), [np.rint(coords).astype(np.int32)], 1)
    info = {'mode': 'locally_registered_view', 'withheld_dino_surface_classes': withhold,
            'manual_prompts': manual,
            'foreground_platform_ignore_polygon_applied': bool(foreground.any()),
            'registration_origin': 'caller-provided local settings',
            'physical_camera_id_verified': False, 'human_reviewed': False,
            'notes': 'Geometry and matching timestamps require per-frame review.'}
    return info, manual, foreground


def ignored_rectangle(size, normalized):
    """Optional normalized xyxy exclusion, with no vendor-specific default."""
    w, h = size
    mask = np.zeros((h, w), bool)
    if normalized is None:
        return mask, None
    box = np.asarray(normalized, dtype=float)
    if box.shape != (4,) or not np.isfinite(box).all() or (box < 0).any() or (box > 1).any():
        raise ValueError('Ignore rectangle must have four finite values in [0, 1]')
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError('Ignore rectangle must have positive area')
    pixels = (box * np.asarray([w, h, w, h])).astype(int)
    x1, y1, x2, y2 = pixels.tolist()
    mask[y1:y2, x1:x2] = True
    return mask, pixels.tolist()

def preview(rgb,semantic,candidate,path):
    im=Image.fromarray(rgb);im.thumbnail((896,504));base=np.asarray(im).copy()
    maps=[cv2.resize(s,im.size,interpolation=cv2.INTER_NEAREST) for s in (semantic,candidate)]
    panels=[]
    for title,s in zip(('DRAFT: evidence masks; grey=unknown','DRAFT: residual yellow=OTHER CANDIDATE'),maps):
        overlay=base.copy()
        for num,color in COLORS.items():
            sel=s==num;overlay[sel]=(.50*overlay[sel]+.50*np.array(color)).astype(np.uint8)
        unknown=s==255;overlay[unknown]=(.75*overlay[unknown]+.25*np.array([140,140,140])).astype(np.uint8)
        p=Image.fromarray(overlay);draw=ImageDraw.Draw(p)
        draw.rectangle((0,0,p.width,28),fill=(20,20,20));draw.text((7,7),title,fill='white');panels.append(p)
    result=Image.new('RGB',(im.width*3,im.height+34),'white')
    result.paste(im,(0,34));result.paste(panels[0],(im.width,34));result.paste(panels[1],(im.width*2,34))
    draw=ImageDraw.Draw(result);draw.text((8,9),'ORIGINAL | blue wall / green floor / purple ceiling / red person / yellow other',fill='black')
    result.save(path,quality=90)

def create_gallery(out,records):
    cards=[]
    for r in records:
        stem=quote(r['image_id'],safe='');filename=quote(r['filename'],safe='')
        cards.append(f'<article data-priority="{r["priority"]}"><h3>{html.escape(r.get("captured_at_candidate") or "timestamp unknown")}</h3>'
          f'<p>{html.escape(r["priority"])} | {html.escape(", ".join(r["review_reasons"]))}</p>'
          f'<a href="previews/{stem}.jpg"><img loading="lazy" src="previews/{stem}.jpg"></a>'
          f'<p><a href="images/{filename}">원본 사본</a> · <a href="semantic_drafts/{stem}.png">근거 마스크 초안</a> · '
          f'<a href="candidate_5class/{stem}.png">5클래스 후보</a> · <a href="metadata/{stem}.json">메타데이터</a> · '
          f'<a href="annotations/{stem}.json">폴리곤 JSON</a></p></article>')
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>현장 이미지 전처리 초안</title>
    <style>body{font:16px sans-serif;margin:28px;color:#242424;background:#f5f4ef}article{padding:18px;background:white;margin:20px 0;border:1px solid #ddd}img{width:100%}p{line-height:1.6}a{color:#24629b}button{padding:9px;margin-right:8px}</style>
    <h1>현장 이미지 사진 전처리 초안</h1><p>파랑=벽 · 초록=바닥 · 보라=천장 · 빨강=사람 · 노랑=기타. 모든 결과는 미검수입니다.</p>
    <p>왼쪽: 원본 / 가운데: 근거가 있는 분할 후보와 회색 미분류 / 오른쪽: 미분류 잔여를 기타 후보로 표시한 전체 후보 지도.</p>
    <p>기타 후보 잔여는 정답이 아닙니다. 사람·표면 누락을 확인하세요. 시공 상태와 진행률은 생성하지 않았습니다.</p>
    <p><a href="README.md">사용 방법</a> · <a href="review_queue.csv">검수 목록</a> · <a href="inventory/inventory.csv">전체 사진 목록</a> · <a href="inventory/selection.csv">대표 사진 선정 근거</a> · <a href="inventory/scan_summary.json">전체 스캔 요약</a></p>
    <button onclick="filter('all')">전체</button><button onclick="filter('high')">우선 검수</button><button onclick="filter('normal')">일반 검수</button><section>'''+''.join(cards)+'''</section>
    <script>function filter(v){document.querySelectorAll('article').forEach(e=>e.hidden=v!='all'&&e.dataset.priority!=v)}</script></html>'''
    (out/'index.html').write_text(page,encoding='utf-8')
    for page,start in enumerate(range(0,len(records),12),1):
        group=records[start:start+12];sheet=Image.new('RGB',(1440,324*((len(group)+2)//3)),'white');draw=ImageDraw.Draw(sheet)
        for i,r in enumerate(group):
            p=Image.open(out/'previews'/f'{r["image_id"]}.jpg')
            # Use the evidence middle panel for compact contact sheets.
            w=p.width//3;p=p.crop((w,34,w*2,p.height));p.thumbnail((476,267))
            x,y=i%3*480,i//3*324;sheet.paste(p,(x,y+42))
            draw.text((x+4,y+6),(r.get('captured_at_candidate') or 'timestamp unknown')+' | '+r['priority'],fill='black')
            draw.text((x+4,y+23),f'unknown {r["unknown_fraction"]:.0%} | persons {r["person_box_count"]}',fill='black')
        sheet.save(out/'contact_sheets'/f'page_{page:02d}.jpg',quality=91)

def run(args):
    settings=json.loads(args.selection.read_text(encoding='utf-8'))
    selected=settings['selected']
    if not isinstance(selected, list) or not selected:
        raise ValueError('Selection must contain a nonempty selected list')
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError('Invalid shard index/count')
    if args.pilot_count < 0:
        raise ValueError('pilot-count must be nonnegative')
    if args.pilot_count:
        selected = selected[:args.pilot_count]
    view_config = load_view_config(args.view_config)
    ignored_rectangle((1, 1), args.ignore_rectangle)  # validate before any model work
    paths = [Path(record['source_path']).resolve(strict=True) for record in selected]
    stems = [path.stem for path in paths]
    if len(stems) != len(set(stems)):
        raise ValueError('Input image stems must be unique')
    for path in [args.sam_encoder, args.sam_decoder, args.dino_model, args.person_model, args.tokenizer]:
        if not path.is_file():
            raise FileNotFoundError(path)
    selected=selected[args.shard_index::args.shard_count]
    out=args.output.resolve()
    for source in paths + [args.selection.resolve()]:
        if out == source or out in source.parents:
            raise ValueError('Output cannot contain any source input')
    protected_inputs = [args.sam_encoder, args.sam_decoder, args.dino_model,
                        args.person_model, args.tokenizer]
    if args.view_config is not None:
        protected_inputs.append(args.view_config)
    for path in protected_inputs:
        resolved = path.resolve()
        if out == resolved or out in resolved.parents:
            raise ValueError('Output cannot contain model or private configuration inputs')
    if out.exists():raise FileExistsError('Refusing output overwrite: '+str(out))
    out.mkdir(parents=True)
    for folder in ['images','masks_raw','masks_class','semantic_drafts','candidate_5class','uncertainty','annotations','metadata','previews','contact_sheets','scripts','settings']:(out/folder).mkdir()
    root=Path(__file__).parent
    for name in ['run_batch.py','person_detector.py','dino_detector.py','inventory_select.py']:
        shutil.copy2(root/name,out/'scripts'/name)
    sam=SamCPU(args.sam_encoder, args.sam_decoder, args.threads)
    dino=GroundingDetector(model_path=args.dino_model, tokenizer_path=args.tokenizer, threads=args.threads)
    person=PersonDetector(model_path=args.person_model, score_threshold=.40, threads=args.threads)
    save_json(out/'settings'/'pipeline.json',{'classes':CLASSES,'unknown_id':255,'sam_assets_provided_explicitly':True,
      'dino_labels':SURFACES+['person']+OTHER_LABELS,'detector_thresholds_are_not_accuracy':True,
      'person_threshold':.40,'checked':False,'training_eligible':False,
      'object_priority':'foreground masks override surfaces; person-other overlap is unknown',
      'other_remainder':'display candidate only; canonical remains 255',
      'wall_definition':'visible installed wall/partition surface candidate; exposed framing requires review',
      'ceiling_definition':'visible ceiling/slab surface candidate; beams/ducts/services require review',
      'floor_definition':'visible floor surface candidate; protection/materials require other or unknown',
      'surface_points':'per-image detector boxes and optional local view prompt settings',
      'view_guards':'optional local configuration; not verified camera IDs or reviewed labels',
      'uncertainty_ids':{'0':'no additional flag','1':'unassigned residual, candidate other only','2':'cross-class conflict','3':'watermark ignore','4':'withheld surface claim in foreground platform polygon'}})
    records=[];errors=[];started=time.perf_counter()
    labels=SURFACES+['person']+OTHER_LABELS
    dino_info={'model_path':str(dino.model_path),'model_sha256':sha256(dino.model_path),
      'input_size':list(dino.input_size),'threshold':dino.threshold,'max_per_class':dino.max_per_class,
      'tokenizer_sha256':dino.tokenizer.sha256,'threads':args.threads,'provider':'CPUExecutionProvider',
      'labels':labels,'method':'maximum token score per supplied phrase, not free phrase decoding'}
    for index,r in enumerate(selected,1):
        began=time.perf_counter();path=Path(r['source_path']);stem=path.stem
        try:
            if sha256(path)!=r['sha256']:raise ValueError('Source hash changed after inventory')
            with Image.open(path) as im:
                if im.getexif().get(274,1) not in (None,1):raise ValueError('Orientation requires new registration')
                rgb=np.asarray(im.convert('RGB'));w,h=im.size
            dino_boxes=dino.predict(rgb,labels=labels)
            coco_boxes=person.predict_all(rgb)
            guard,manual,foreground=view_guard(r,(w,h),view_config)
            proposals=[]
            withheld=[]
            for b in dino_boxes:
                label=b['class_name'];name=label if label in CLASSES else 'other'
                if label in guard['withheld_dino_surface_classes']:
                    withheld.append({**b,'reason':'withheld_by_view_guard'});continue
                proposals.append({**b,'output_class':name,'origin':'grounding_dino','proposal_label':label})
            for b in coco_boxes:
                proposals.append({**b,'output_class':'person' if b['class_id']==0 else 'other',
                                  'origin':'deimv2_coco','proposal_label':b['class_name']})
            proposals.extend(manual)
            # Bound computation without claiming that discarded low-ranked objects are absent.
            props=[]
            for name in CLASSES:
                candidates=sorted([b for b in proposals if b['output_class']==name],key=lambda b:-(b['score'] if b['score'] is not None else 1))
                props.extend(candidates[:(12 if name=='other' else 8)])
            ignored=len(proposals)-len(props)
            dropped=[b for b in proposals if b not in props]
            features=sam.encode(rgb)
            class_masks={name:np.zeros((h,w),bool) for name in CLASSES}
            stats=[];reasons=['human_class_boundary_and_occlusion_review_required'];high=False
            if guard['mode']!='general_view':reasons.append('assistant_view_guard_requires_review');high=True
            reasons.append('framing_and_services_taxonomy_requires_review')
            if ignored:reasons.append('proposal_cap_reached');high=True
            for i,b in enumerate(props):
                x1,y1,x2,y2=b['xyxy'];box=[max(0,float(x1)),max(0,float(y1)),min(w,float(x2)),min(h,float(y2))]
                if box[2]-box[0]<2 or box[3]-box[1]<2:continue
                raw,scores,chosen=sam.predict(features,box,(w,h),b.get('points'))
                bounds=np.zeros((h,w),bool)
                bounds[int(box[1]):int(np.ceil(box[3])),int(box[0]):int(np.ceil(box[2]))]=True
                supported=raw&bounds
                spill=float((raw&~bounds).sum()/max(1,raw.sum()))
                name=b['output_class'];target=f'{name}_{i:02d}'
                Image.fromarray(raw.astype(np.uint8)*255).save(out/'masks_raw'/f'{stem}__{target}.png')
                class_masks[name]|=supported
                item={'target_id':target,'output_class':name,'proposal_label':b['proposal_label'],
                      'detector':b['origin'],'detector_score':b['score'],'xyxy':box,
                      'prompt_points':b.get('points',[]),
                      'raw_mask_path':f'masks_raw/{stem}__{target}.png',
                      'predicted_iou_scores':scores,'selected_candidate':chosen,
                      'raw_pixels':int(raw.sum()),'supported_pixels':int(supported.sum()),
                      'outside_box_fraction':spill,'postprocess':'intersect detector box; raw preserved'}
                stats.append(item)
                if max(scores)<.65 or spill>.15 or supported.sum()<30:
                    reasons.append('weak_or_spilling_mask:'+target);high=True
                if name=='person' and b['score'] is not None and b['score']<.60:reasons.append('low_person_detector_score:'+target);high=True
            ignore,ignore_pixels=ignored_rectangle((w,h),args.ignore_rectangle)
            semantic,candidate,remainder,conflict=fuse(class_masks,ignore)
            held=foreground&np.isin(semantic,[1,2,3])&~ignore
            semantic[held]=255;candidate[held]=255;remainder[held]=False
            if held.any():reasons.append('foreground_platform_surface_claim_withheld');high=True
            fractions={name:float((semantic==num).mean()) for name,num in CLASSES.items()}
            unknown=float((semantic==255).mean())
            for name in SURFACES:
                if not class_masks[name].any():reasons.append('no_surface_proposal:'+name);high=True
            if unknown>.35:reasons.append('large_unclassified_region');high=True
            if conflict.mean()>.015:reasons.append('class_conflict');high=True
            if (r.get('previous_spatial_difference_normalized') or 0)>.12:
                reasons.append('camera_occluder_or_scene_change_candidate');high=True
            if r.get('quality_flags'):reasons+=r['quality_flags'];high=True
            for name,mask in class_masks.items():
                Image.fromarray(mask.astype(np.uint8)*255).save(out/'masks_class'/f'{stem}__{name}.png')
            Image.fromarray(semantic).save(out/'semantic_drafts'/f'{stem}.png')
            Image.fromarray(candidate).save(out/'candidate_5class'/f'{stem}.png')
            qa=np.zeros((h,w),np.uint8);qa[remainder]=1;qa[conflict]=2;qa[ignore]=3;qa[held]=4
            Image.fromarray(qa).save(out/'uncertainty'/f'{stem}.png')
            preview(rgb,semantic,candidate,out/'previews'/f'{stem}.jpg')
            shutil.copy2(path,out/'images'/path.name)
            if sha256(path)!=r['sha256'] or sha256(out/'images'/path.name)!=r['sha256']:
                raise ValueError('Source changed or original copy mismatch')
            shapes=[]
            for i,(name,num) in enumerate(CLASSES.items()):shapes.extend(polygon_shapes(semantic==num,name,i*100000))
            annotation={'version':'4.1.0','imagePath':'../images/'+path.name,'imageData':None,'imageWidth':w,'imageHeight':h,
              'flags':{'draft_display_only':True},'checked':False,'training_eligible':False,'needs_review':True,
              'shapes':shapes,'semantic_mask':'../semantic_drafts/'+stem+'.png',
              'description':'UNREVIEWED; PNG canonical; exterior polygons may fill holes in app',
              'state_labels':None,'progress_ratio':None}
            save_json(out/'annotations'/f'{stem}.json',annotation)
            record={'image_id':stem,'filename':path.name,'source_path':str(path),'source_sha256':r['sha256'],
              'image_size':[w,h],'source_transform':'none','full_resolution_decode_verified':True,
              'site_id':args.site_id if args.site_id is not None else r.get('site_id'),'captured_at_candidate':r['captured_at_candidate'],
              'timestamp_source':'filename_unverified','timezone':None,'captured_at_utc':None,
              'camera_id':r.get('camera_id'),'camera_session_id':r.get('camera_session_id'),'camera_registration':'not physically verified; per-image boxes',
              'selection_reasons':r['selection_reasons'],'split':'pending','checked':False,
              'training_eligible':False,'needs_review':True,'label_origin':'model_draft_not_ground_truth',
              'priority':'high' if high else 'normal','review_reasons':list(dict.fromkeys(reasons)),
              'targets':stats,'class_fractions':fractions,'unknown_fraction':unknown,
              'dino_run':dino.last_run,'original_dino_boxes':dino_boxes,'original_coco_boxes':coco_boxes,
              'withheld_proposals':withheld,'dropped_proposals':dropped,'view_guard':guard,
              'other_remainder_candidate_fraction':float(remainder.mean()),'conflict_fraction':float(conflict.mean()),
              'person_box_count':sum(b['output_class']=='person' for b in props),'discarded_proposals':ignored,
              'global_ignore_rectangle':ignore_pixels,
              'qa_values':{'1':'unassigned remainder other display candidate','2':'class conflict','3':'watermark ignore','4':'surface claim in foreground platform region withheld'},
              'state_labels':None,'progress_ratio':None,'elapsed_seconds':time.perf_counter()-began}
            save_json(out/'metadata'/f'{stem}.json',record);records.append(record)
            with (out/'manifest.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
            print(json.dumps({'done':index,'total':len(selected),'filename':path.name,'priority':record['priority'],
                             'unknown':round(unknown,3),'boxes':len(props),'seconds':round(record['elapsed_seconds'],2)},ensure_ascii=False),flush=True)
        except Exception as e:
            errors.append({'filename':path.name,'error':repr(e)});print(json.dumps(errors[-1],ensure_ascii=False),flush=True)
    with (out/'review_queue.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['filename','captured_at_candidate','priority','unknown_fraction','person_boxes','review_reasons','checked','training_eligible','split'])
        for r in sorted(records,key=lambda r:(r['priority']!='high',r['filename'])):
            writer.writerow([r['filename'],r['captured_at_candidate'],r['priority'],round(r['unknown_fraction'],4),r['person_box_count'],';'.join(r['review_reasons']),False,False,'pending'])
    create_gallery(out,records)
    info={'attempted':len(selected),'completed':len(records),'errors':errors,'classes':CLASSES,'unknown_id':255,
      'shard_index':args.shard_index,'shard_count':args.shard_count,
      'sam':sam.info,'dino':dino_info,'person':person.model_details,
      'selection_sha256':sha256(args.selection),'code_sha256':sha256(__file__),
      'priority_counts':dict(Counter(r['priority'] for r in records)),
      'checked':False,'training_eligible':False,'state_labels_generated':False,'progress_generated':False,
      'seconds':time.perf_counter()-started,'thresholds':'unvalidated heuristics for review priority only'}
    save_json(out/'run_info.json',info)
    print(json.dumps({'completed':len(records),'errors':len(errors),'seconds':round(info['seconds'],1),'output':str(out)},ensure_ascii=False),flush=True)
    if errors:raise SystemExit(1)

def main():
    parser=argparse.ArgumentParser(description='Local five-class ONNX annotation drafts; outputs remain unreviewed.')
    parser.add_argument('--selection',type=Path,required=True,help='Private inventory selection JSON')
    parser.add_argument('--output',type=Path,required=True,help='New local output directory')
    parser.add_argument('--sam-encoder',type=Path,required=True)
    parser.add_argument('--sam-decoder',type=Path,required=True)
    parser.add_argument('--dino-model',type=Path,required=True)
    parser.add_argument('--person-model',type=Path,required=True)
    parser.add_argument('--tokenizer',type=Path,required=True)
    parser.add_argument('--view-config',type=Path,help='Optional private per-image/view prompt JSON')
    parser.add_argument('--site-id',default=None,help='Local metadata only; no built-in company identifier')
    parser.add_argument('--ignore-rectangle',type=float,nargs=4,metavar=('X1','Y1','X2','Y2'),
                        help='Optional normalized xyxy rectangle; default is no exclusion')
    parser.add_argument('--pilot-count',type=int,default=0,help='Process first N selected records; zero means all selected')
    parser.add_argument('--threads',type=int,default=4)
    parser.add_argument('--shard-index',type=int,default=0)
    parser.add_argument('--shard-count',type=int,default=1)
    args=parser.parse_args()
    if args.threads < 1:
        parser.error('threads must be positive')
    run(args)


if __name__=='__main__':
    main()
