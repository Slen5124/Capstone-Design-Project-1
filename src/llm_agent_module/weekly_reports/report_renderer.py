"""Data-driven construction report with weekly views and evidence.

All asset access is delegated to a trusted file-ID resolver. JSON URLs are never
fetched. Source classification must be assigned by the authenticated backend.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import os
import re
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle, Flowable

W, H = 841.89, 595.28
INK = colors.HexColor('#222828')
GRAY = colors.HexColor('#717777')
BLUE = colors.HexColor('#247ac5')
ORANGE = colors.HexColor('#d47835')
PALETTE = [BLUE, ORANGE, colors.HexColor('#308d68'), colors.HexColor('#8058ad')]
BUNDLED_KOREAN_FONT = Path(__file__).resolve().parent / 'fonts' / 'NanumGothic-Regular.ttf'
WINDOWS_KOREAN_FONT = Path('C:/Windows/Fonts/malgun.ttf')
KOREAN_CID_FONT = 'HYGothic-Medium'


class ReportDataError(ValueError):
    pass


class ReportAssetError(ReportDataError):
    pass


class ReportGraphicsState(canvas.ExtGState):
    # PDF stroke adjustment aligns equal vector strokes to device pixels.
    # Without it, fractional date columns can look alternately dark/light.
    defaults = dict(canvas.ExtGState.defaults, SA=False)


class GanttBar(Flowable):
    """A planned-work bar inside a white, black-bordered date cell."""
    def __init__(self,width):
        super().__init__();self.width=width;self.height=12
    def draw(self):
        self.canv.setFillColor(colors.HexColor('#848c8c'))
        self.canv.rect(0,1,self.width,10,stroke=0,fill=1)


def _font(text, bold=False):
    if any(ord(ch) > 255 for ch in str(text)):
        name = 'ReportKorean'
        if name in pdfmetrics.getRegisteredFontNames():
            return name
        configured = os.environ.get('REPORT_KOREAN_FONT')
        candidates = [Path(configured)] if configured else [BUNDLED_KOREAN_FONT, WINDOWS_KOREAN_FONT]
        for candidate in candidates:
            try:
                if not candidate.is_file() or candidate.stat().st_size == 0:
                    raise OSError('Font file is missing or empty')
                font = TTFont(name, str(candidate))
                if not all(ord(ch) in font.face.charToGlyph for ch in '가힣한글'):
                    raise TTFError('Font does not contain Korean glyphs')
                pdfmetrics.registerFont(font)
                return name
            except (OSError, TTFError, ValueError) as exc:
                if configured:
                    raise ReportAssetError('REPORT_KOREAN_FONT must point to a readable, nonempty Korean TrueType font') from exc
        # ReportLab's supported name is case-sensitive. Return this name on
        # every call; registering a CID fallback does not create ReportKorean.
        if KOREAN_CID_FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(KOREAN_CID_FONT))
        return KOREAN_CID_FONT
    return 'Helvetica-Bold' if bold else 'Helvetica'


def _day(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ReportDataError(f'Invalid observation date: {value}') from exc


def _event_stamp(value):
    """Human-readable date for the evidence/review history."""
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.strftime('%d %b %Y').upper()
    except (TypeError, ValueError):
        return str(value or '')[:10]


def _percent(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100 and math.isfinite(value)


def _analyses(task, start, end):
    if task.get('source') != 'vision':
        return []
    result = []
    for analysis in task.get('analyses', []):
        region = analysis.get('region')
        if region not in {'wall', 'ceiling', 'floor'}:
            raise ReportDataError(f'Unknown vision region: {region}')
        rows, seen = [], set()
        for raw in analysis.get('observations', []):
            day = _day(raw.get('date'))
            if not start <= day <= end:
                continue
            values = {str(k): float(v) for k, v in raw.get('values', {}).items() if _percent(v)}
            if not values:
                continue
            if day in seen:
                raise ReportDataError('Duplicate vision date within task/region')
            seen.add(day)
            boxes = raw.get('boxes', {})
            for target, box in boxes.items():
                if not (isinstance(box, (list, tuple)) and len(box) == 4 and
                        all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1 for v in box)
                        and box[0] < box[2] and box[1] < box[3]):
                    raise ReportDataError(f'Invalid normalized ROI box: {target}')
            labels={str(key):re.sub(r'\s*\(as seen from camera\)', '', str(value), flags=re.I).strip()
                    for key,value in raw.get('labels',{}).items()}
            rows.append({**raw, 'date': day.isoformat(), 'values': values, 'labels': labels})
        if rows:
            result.append({'region': region, 'view_key': analysis.get('view_key'),
                           'observations': sorted(rows, key=lambda r: r['date'])})
    return result


class Renderer:
    def __init__(self, payload, output_path, resolve_asset):
        self.p = payload
        self.resolve = resolve_asset
        self.asset_cache = {}
        self.start, self.end = _day(payload['period_start']), _day(payload['period_end'])
        if self.end < self.start:
            raise ReportDataError('Report period is reversed')
        self.tasks = list(payload.get('tasks', []))
        ids = [str(t['task_id']) for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ReportDataError('Duplicate task IDs')
        self.draft = not payload.get('ready_for_final', False)
        if not self.draft and (payload.get('missing_task_ids') or any(t.get('source') == 'missing' for t in self.tasks)):
            raise ReportDataError('Final report cannot contain missing task evidence')
        self.parsed = {str(t['task_id']): _analyses(t, self.start, self.end) for t in self.tasks}
        # The task details and status table use one stable, evidence-first order.
        self.tasks.sort(key=lambda t: not bool(self.parsed[str(t['task_id'])]))
        self.photos = {}
        for task in self.tasks:
            accepted = []
            for photo in task.get('photos', []):
                source = photo.get('source', 'manager' if task.get('source') in {'manager','missing'} else 'vision')
                if source == 'manager' and (not _percent(photo.get('progress_percent')) or photo.get('report_eligible') is False):
                    continue
                accepted.append({**photo, 'source': source})
            self.photos[str(task['task_id'])] = accepted
            if not self.draft and task.get('source') == 'manager' and not accepted:
                raise ReportDataError('Final manager task requires a photo with manager-reported progress')
        self.pages = []
        self.out = Path(output_path)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.c = canvas.Canvas(str(self.out), pagesize=(W, H), pageCompression=1, invariant=1)
        self.c.setTitle(f"{payload.get('site_name', payload['site_code'])} - Week {payload['week']}")
        if payload.get('is_demo'):
            self.c.setSubject('TEST / SIMULATED DATA')
            self.c.setKeywords('TEST, SIMULATED DATA, DEMO')

    def asset(self, file_id):
        if not file_id:
            raise ReportAssetError('Required photo file ID missing')
        key = str(file_id)
        if key not in self.asset_cache:
            try:
                path = Path(self.resolve(key)).resolve()
                if not path.is_file():
                    raise FileNotFoundError(path)
                ImageReader(str(path)).getSize()
            except Exception as exc:
                raise ReportAssetError(f'Unavailable or invalid report image: {key}') from exc
            self.asset_cache[key] = path
        return self.asset_cache[key]

    def text(self, x, y, value, size=10, bold=False, color=INK):
        value = str(value)
        self.c.setFillColor(color)
        self.c.setFont(_font(value, bold), size)
        self.c.drawString(x, H-y, value)

    def rect(self, x, y, width, height, fill=None, stroke=colors.black, radius=0, line_width=.6):
        self.c.setLineWidth(line_width)
        if fill is not None:self.c.setFillColor(fill)
        if stroke is not None:self.c.setStrokeColor(stroke)
        if radius:
            self.c.roundRect(x,H-y-height,width,height,radius,stroke=int(stroke is not None),fill=int(fill is not None))
        else:self.c.rect(x,H-y-height,width,height,stroke=int(stroke is not None),fill=int(fill is not None))


    def paragraph(self, value, width, size=10, bold=False, color=INK, align=0):
        return Paragraph(escape(str(value)).replace('\n','<br/>'), ParagraphStyle('p',fontName=_font(value,bold),fontSize=size,leading=size*1.35,textColor=color,alignment=align))


    def centered_text(self, x, y, width, height, value, size=10, bold=False, color=INK):
        self.c.setFillColor(color)
        self.c.setFont(_font(value, bold), size)
        self.c.drawCentredString(x+width/2, H-(y+height/2+size*.35), str(value))


    def page(self, title, kind, task=None):
        if self.pages:
            self.finish_page()
            self.c.showPage()
        self.pages.append({'page':len(self.pages)+1,'kind':kind,'task_id':str(task['task_id']) if task else None,'title':title})
        self.text(38,24,f"alo  /  {self.p.get('display_site_name',self.p['site_code']).upper()}",9.5,True)
        self.text(641,24,'WEEKLY CONSTRUCTION OBSERVATION',8,color=GRAY)
        self.c.setStrokeColor(INK);self.c.setLineWidth(.45);self.c.line(38,H-33,804,H-33)
        size=min(24,610/max(1,pdfmetrics.stringWidth(title,_font(title,True),1)))
        self.text(38,65,title,size,True)
        self.c.setFillColor(GRAY);self.c.setFont('Helvetica',7)
        self.c.drawRightString(804,H-59,f"WEEK {int(self.p['week']):02d}  /  {self.start:%d}-{self.end:%d %b %Y}".upper())

    def finish_page(self):
        # Provenance remains in the input/PDF metadata; the approved page design
        # has no test banner. Draft status is independent of the source metadata.
        pass

    def image(self, file_id, rect, crop=False):
        x,y,width,height=rect
        path=self.asset(file_id);image=ImageReader(str(path));iw,ih=image.getSize()
        scale=max(width/iw,height/ih) if crop else min(width/iw,height/ih)
        ww,hh=iw*scale,ih*scale;xx=x+(width-ww)/2;yy=y+(height-hh)/2
        self.c.saveState()
        clip=self.c.beginPath();clip.rect(x,H-y-height,width,height);self.c.clipPath(clip,stroke=0,fill=0)
        self.c.drawImage(image,xx,H-yy-hh,ww,hh,mask='auto')
        self.c.restoreState()
        return xx,yy,ww,hh

    def table_pages(self,title,kind,headers,rows,widths,task=None,split_rows=False,font_size=11.5,vertical_padding=8):
        # Variable row heights protect long task names and Korean descriptions.
        cells=[[self.paragraph(v,w-12,font_size,True,colors.white,align=1) for v,w in zip(headers,widths)]]
        cells += [[v if isinstance(v,Flowable) else self.paragraph(v,w-12,font_size,align=1) for v,w in zip(row,widths)] for row in rows]
        table=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT',splitInRow=int(split_rows))
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),INK),('GRID',(0,0),(-1,-1),.35,colors.black),('BOX',(0,0),(-1,-1),.8,colors.black),('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),vertical_padding),('BOTTOMPADDING',(0,0),(-1,-1),vertical_padding)]))
        pending=[table]
        while pending:
            chunk=pending.pop(0)
            _,full_height=chunk.wrap(766,440)
            parts=[chunk] if full_height<=440 else chunk.split(766,440)
            if not parts:raise ReportDataError('Table row exceeds one page')
            self.page(title,kind,task);part=parts[0];_,height=part.wrap(766,440);part.drawOn(self.c,38,H-94-height)
            pending=parts[1:]+pending


    def detail_heading(self,task,title):
        self.page(title,'task_detail',task)
        for x,width,label,value in [(38,110,'TASK ID',task.get('source_task_id',task['task_id'])),(148,284,'PLANNED DATES',f"{_day(task['planned_start']):%d %b} - {_day(task['planned_finish']):%d %b / %Y}".upper()),(432,372,'REPORTING PERIOD',f'{self.start:%d %b} - {self.end:%d %b %Y}'.upper())]:
            self.rect(x,87,width,24,INK,stroke=None);self.centered_text(x,87,width,24,label,11,True,colors.white)
            self.rect(x,111,width,29,stroke=None);self.centered_text(x,111,width,29,value,12)
        self.c.saveState();self.c.setStrokeColor(colors.black);self.c.setLineWidth(.35)
        self.c.line(38,H-111,804,H-111)
        for x in [148,432]:self.c.line(x,H-87,x,H-140)
        self.c.restoreState();self.rect(38,87,766,53,line_width=.8)


    def short_description(self,value,x,y,width,height=24):
        if not value:return
        paragraph=self.paragraph(value,width,8)
        parts=paragraph.split(width,height)
        if parts:
            part=parts[0];_,hh=part.wrap(width,height);part.drawOn(self.c,x,H-y-hh)

    def manager_report_text(self, task, photo):
        report=(photo.get('description') or '').strip()
        if self.p.get('is_demo') and re.match(r'^(TEST|MOCK DATA)\b', report, re.I):
            value=photo.get('progress_percent')
            ratio=f'{value:g}%' if _percent(value) else 'the recorded level'
            return (f'Illustrative entry: {task["task_name"]} is recorded at {ratio}. '
                    'Confirm the work and task-specific photo before approval.')
        return report

    def manager_detail(self,task,title,photos):
        # Exactly one primary detail page per manager task. The backend retains
        # all source evidence even when the PDF summarizes its latest records.
        selected=photos[-4:]
        self.detail_heading(task,title)
        if len(selected)==1:
            photo=selected[0]
            self.text(38,156,'SITE PHOTO / '+photo.get('date',''),9,True,BLUE)
            self.text(551,156,'PROGRESS',9,True,BLUE)
            # Keep every manager task on one consistent page: the site photo
            # remains prominent, while the space below it is reserved for the
            # complete submission/review log (including three correction
            # cycles and the final decision).  Never add a separate history
            # page, because the evidence story belongs with its task photo.
            self.image(photo['file_id'],(38,170,495,236))
            baseline=photo.get('baseline_progress_percent')
            delta=(f"{photo['progress_percent']-baseline:+g} pp / since {_day(photo['baseline_date']):%d %b}"
                   if _percent(baseline) else 'Manager input / '+photo.get('date',''))
            self.progress_card(551,167,247,132,'MANAGER-REPORTED PROGRESS',photo['progress_percent'],delta,BLUE)
            self.manager_notes(task,photo,551,311,247,145)
        else:
            placements=([(38,164,372,284),(432,164,372,284)] if len(selected)==2 else
                        [(38,154,372,140),(432,154,372,140),(38,355,372,140),(432,355,372,140)])
            for photo,(x,y,width,height) in zip(selected,placements):
                self.text(x,y-9,photo.get('date',''),9,True,GRAY)
                self.image(photo['file_id'],(x,y,width,height))
                self.text(x,y+height+19,f"{photo['progress_percent']:g}%  /  Manager-reported progress",11,True,BLUE)
                self.short_description(self.manager_report_text(task,photo),x,y+height+26,width)
        if len(photos)>4:
            self.text(38,562,f'Showing latest 4 of {len(photos)} manager photos; all evidence retained',8,color=GRAY)
        elif len(selected)>1 and task.get('description'):
            self.short_description(task['description'],38,548,766,15)
        if len(selected)==1:
            self.submission_history(task,38,416,766,151)

    def _submission_history_entries(self,task):
        """Normalize raw workflow actions without changing the audit record."""
        supported={'submission','review_reject','resubmission','review_accept','completion_confirm'}
        raw=[entry for entry in task.get('submission_history',[]) if entry.get('type') in supported]
        if not raw:return []

        def sort_key(entry):
            return str(entry.get('recorded_at') or entry.get('date') or '')
        raw=sorted(raw,key=sort_key)

        # Present review acceptance and the subsequent completion confirmation
        # as one final report milestone.  They remain separate events in the
        # backend audit trail, so neither business action is discarded.
        entries=[];index=0
        while index<len(raw):
            entry=dict(raw[index])
            following=raw[index+1] if index+1<len(raw) else None
            if entry['type']=='review_accept' and following and following.get('type')=='completion_confirm':
                entry.update(type='accepted_complete',recorded_at=following.get('recorded_at',following.get('date')),
                             date=following.get('date',entry.get('date')),
                             body=following.get('body') or entry.get('body') or '')
                index+=2
            elif entry['type']=='completion_confirm':
                entry['type']='accepted_complete';index+=1
            else:
                index+=1
            entries.append(entry)

        def ordinal(number):
            suffix='th' if 10<number%100<14 else {1:'st',2:'nd',3:'rd'}.get(number%10,'th')
            return f'{number}{suffix}'

        labels={'submission':'REPORTED','review_reject':'CORRECTION REQUESTED',
                'resubmission':'RESUBMITTED','review_accept':'EVIDENCE ACCEPTED',
                'accepted_complete':'ACCEPTED & COMPLETED'}
        correction_number=0
        for entry in entries:
            kind=entry['type']
            if kind=='review_reject':
                correction_number+=1
                entry['_history_label']=f'{ordinal(correction_number)} CORRECTION REQUESTED'
            elif kind=='resubmission':
                entry['_history_label']=f'RESUBMITTED{f" / ROUND {correction_number}" if correction_number else ""}'
            else:
                entry['_history_label']=labels[kind]
        return entries

    def _submission_history_body(self,entry):
        kind=entry['type']
        fallback={'submission':'Photo and progress reported',
                  'resubmission':'Updated photo and progress reported',
                  'review_reject':'Updated evidence requested',
                  'review_accept':'Evidence accepted',
                  'accepted_complete':'Evidence accepted and task completion confirmed'}[kind]
        body=entry.get('body') or fallback
        # Test imports sometimes carry TEST-prefixed prose.  The visual report
        # must remain suitable for an operating workflow, while raw text stays
        # in the underlying event record.
        if self.p.get('is_demo') and re.match(r'^(TEST|MOCK DATA|TEST ONLY)\b',str(body),re.I):
            return fallback
        return str(body)

    def _history_owner(self,entry):
        return 'Head office' if entry['type'] in {'review_reject','review_accept','accepted_complete'} else 'Site manager'

    def _shorten_history(self,value,font,size,limit):
        value=' '.join(str(value or '').split())
        if not value or pdfmetrics.stringWidth(value,font,size)<=limit:return value
        suffix='...'
        while value and pdfmetrics.stringWidth(value+suffix,font,size)>limit:
            value=value[:-1]
        return value.rstrip()+suffix

    def submission_history(self,task,x,y,width,height):
        """Draw one complete evidence log below the task photo.

        The report intentionally has no separate evidence-history pages.  A
        manager task uses this same panel whether it has one submission or a
        full three-cycle correction/re-submission sequence.
        """
        entries=self._submission_history_entries(task)
        if not entries:
            # Older imported manager evidence can have a valid photo,
            # progress value and report text before the matching task_event
            # history is available.  It is still a submitted manager report;
            # never contradict the visible SITE MANAGER REPORT card by calling
            # the task "NOT SUBMITTED".  New submissions carry the explicit
            # task_event and take the normal branch above.
            manager_photos=task.get('photos') or []
            if manager_photos:
                latest=manager_photos[-1]
                entries=[{'type':'submission','_history_label':'REPORTED',
                          'date':latest.get('date',''),
                          'recorded_at':latest.get('date',''),
                          'body':'Photo, progress and site manager report received.'}]
            else:
                entries=[{'type':'submission','_history_label':'NOT SUBMITTED',
                          'date':'','body':'No site manager report has been received for this task.'}]

        # A typical three-correction workflow normalizes to eight rows:
        # submission, three correction requests, three resubmissions and one
        # combined accepted/completed milestone.  Every row stays in this
        # panel; no event is hidden or moved to a second page.
        display=entries

        green=colors.HexColor('#13763b')
        pale_green=colors.HexColor('#eaf6eb')
        slate=colors.HexColor('#49625a')
        gray_border=colors.HexColor('#d5ddd8')
        current=entries[-1]
        current_label=current['_history_label']
        status_text='CURRENT: '+current_label

        self.rect(x,y,width,height,colors.white,gray_border,radius=5,line_width=.55)
        self.rect(x+12,y+8,4,12,BLUE,stroke=None,radius=1)
        self.text(x+23,y+18,'EVIDENCE & REVIEW HISTORY',8.1,True,INK)
        badge_width=min(178,max(80,pdfmetrics.stringWidth(status_text,'Helvetica-Bold',6.6)+18))
        self.rect(x+width-badge_width-12,y+6,badge_width,16,pale_green,stroke=None,radius=8)
        self.centered_text(x+width-badge_width-12,y+6,badge_width,16,status_text,6.4,True,green)
        self.c.saveState();self.c.setStrokeColor(colors.HexColor('#dfe5e3'));self.c.setLineWidth(.4)
        self.c.line(x+12,H-y-25,x+width-12,H-y-25);self.c.restoreState()

        line_top=y+34
        row_height=(height-39)/max(1,len(display))
        dot_x=x+23;line_x=x+41;text_x=x+52;max_width=width-67
        self.c.saveState();self.c.setStrokeColor(colors.HexColor('#c9d7d0'));self.c.setLineWidth(.7)
        self.c.line(line_x,H-line_top+2,line_x,H-(line_top+row_height*(len(display)-1)+2));self.c.restoreState()

        for number,entry in enumerate(display):
            yy=line_top+number*row_height
            is_current=number==len(display)-1
            colour=green if is_current else BLUE
            font_size=6.8 if len(display)<=5 else (6.25 if len(display)<=8 else 5.5)
            self.c.saveState();self.c.setFillColor(colour);self.c.circle(dot_x,H-yy+2,3.2,fill=1,stroke=0);self.c.restoreState()
            stamp=_event_stamp(entry.get('recorded_at') or entry.get('date')) or str(entry.get('date') or '')
            prefix=f'{entry["_history_label"]}  /  {stamp}  /  {self._history_owner(entry)}'
            prefix_width=pdfmetrics.stringWidth(prefix,'Helvetica-Bold',font_size)
            room=max(80,max_width-prefix_width-9)
            message=self._shorten_history(self._submission_history_body(entry),'Helvetica',font_size,room)
            self.text(text_x,yy+3,prefix,font_size,True,colour)
            self.text(text_x+prefix_width+9,yy+3,message,font_size,color=slate)
        if len(display)==1:
            self.text(text_x,line_top+20,
                      ('No correction or approval action has been recorded for this reporting period.'
                       if current['_history_label']!='NOT SUBMITTED' else ''),6.8,color=GRAY)
        return False

    def submission_history_continuation(self,task,title):
        """Add readable history pages for more than four workflow actions."""
        entries=self._submission_history_entries(task)
        if len(entries)<=4:return
        green=colors.HexColor('#13763b')
        slate=colors.HexColor('#49625a')
        border=colors.HexColor('#d5ddd8')
        for offset in range(0,len(entries),8):
            subset=entries[offset:offset+8]
            suffix=' / Evidence & review history'+(f' ({offset//8+1})' if len(entries)>8 else '')
            self.page(title+suffix,'evidence_history',task)
            top=112;row_height=47;panel_height=35+row_height*len(subset)+12
            self.rect(38,top,766,panel_height,colors.white,border,radius=5,line_width=.55)
            self.rect(50,top+11,4,13,BLUE,stroke=None,radius=1)
            self.text(61,top+22,'EVIDENCE & REVIEW HISTORY',9,True,INK)
            if offset+len(subset)==len(entries):
                status='CURRENT: '+entries[-1]['_history_label']
                badge_width=min(178,max(80,pdfmetrics.stringWidth(status,'Helvetica-Bold',7)+18))
                self.rect(804-badge_width-12,top+8,badge_width,17,colors.HexColor('#eaf6eb'),stroke=None,radius=8)
                self.centered_text(804-badge_width-12,top+8,badge_width,17,status,7,True,green)
            self.c.saveState();self.c.setStrokeColor(colors.HexColor('#dfe5e3'));self.c.setLineWidth(.4)
            self.c.line(50,H-top-32,792,H-top-32);self.c.restoreState()
            dot_x=72;line_x=94;text_x=112;line_top=top+53
            self.c.saveState();self.c.setStrokeColor(colors.HexColor('#c9d7d0'));self.c.setLineWidth(.8)
            self.c.line(line_x,H-line_top+2,line_x,H-(line_top+row_height*(len(subset)-1)+2));self.c.restoreState()
            for number,entry in enumerate(subset):
                yy=line_top+number*row_height
                is_current=offset+number==len(entries)-1
                colour=green if is_current else BLUE
                self.c.saveState();self.c.setFillColor(colour);self.c.circle(dot_x,H-yy+2,4.2,fill=1,stroke=0);self.c.restoreState()
                stamp=_event_stamp(entry.get('recorded_at') or entry.get('date')) or str(entry.get('date') or '')
                self.text(text_x,yy+4,entry['_history_label'],9,True,colour)
                meta=f'{stamp} / {self._history_owner(entry)}'
                self.text(text_x,yy+17,meta,7.2,color=GRAY)
                body=self._shorten_history(self._submission_history_body(entry),'Helvetica',8,650)
                self.text(text_x,yy+31,body,8,color=slate)

    def manager_notes(self,task,photo,x,y,width,height):
        report=self.manager_report_text(task,photo)
        changes=[r for r in self.p.get('extension_requests',[]) if str(r.get('task_id'))==str(task['task_id'])]
        followup=None
        if task.get('review_note'):
            followup=('CORRECTION REQUEST',task['review_note'],colors.HexColor('#fff4dd'),colors.HexColor('#8a5a12'))
        elif changes:
            last=changes[-1];status=last.get('status','pending').upper()
            message=last.get('reason') or last.get('proposed_work') or 'Schedule or work change requested'
            followup=(f'CHANGE REQUEST / {status}',message,colors.HexColor('#eef4f8'),colors.HexColor('#35637a'))

        green=colors.HexColor('#185c3b')
        self.rect(x,y,width,height,colors.white,stroke=colors.HexColor('#d5ddd8'),radius=5,line_width=.55)
        self.rect(x,y,width,3,green,stroke=None,radius=2)
        self.text(x+11,y+21,'SITE MANAGER REPORT',9,True,green)
        badge='SUBMITTED' if report else 'NOT SUBMITTED'
        badge_width=58 if report else 74
        badge_fill=colors.HexColor('#e2f0e6') if report else colors.HexColor('#ecefee')
        badge_text=colors.HexColor('#2f7047') if report else colors.HexColor('#727a78')
        self.rect(x+width-badge_width-10,y+10,badge_width,16,badge_fill,stroke=None,radius=8)
        self.centered_text(x+width-badge_width-10,y+10,badge_width,16,badge,7.2,True,badge_text)
        self.c.saveState();self.c.setStrokeColor(colors.HexColor('#e2e7e4'));self.c.setLineWidth(.45)
        self.c.line(x+11,H-y-34,x+width-11,H-y-34);self.c.restoreState()

        content_y=y+50
        followup_height=51 if followup else 0
        body_height=max(18,y+height-followup_height-content_y-4)
        if report:
            paragraph=self.paragraph(report,width-22,9)
            parts=paragraph.split(width-22,body_height)
            if parts:
                part=parts[0];_,hh=part.wrap(width-22,body_height)
                part.drawOn(self.c,x+11,H-content_y-hh)
                meta_y=min(y+height-followup_height-11,content_y+hh+16)
                stamp=photo.get('date')
                if stamp:self.text(x+11,meta_y,f'Reported {stamp} / Site manager',8,color=GRAY)
        else:
            self.text(x+11,content_y,'No site manager report submitted.',9,True,GRAY)
            note=self.paragraph('Photo and progress were submitted without a written update.',width-22,7.2,color=GRAY)
            parts=note.split(width-22,max(20,body_height-24))
            if parts:
                part=parts[0];_,hh=part.wrap(width-22,max(20,body_height-24))
                part.drawOn(self.c,x+11,H-content_y-15-hh)

        if followup:
            label,body,fill,colour=followup
            panel_y=y+height-followup_height
            self.rect(x+8,panel_y,width-16,followup_height-8,fill,stroke=None,radius=4)
            self.text(x+16,panel_y+14,label,7.2,True,colour)
            paragraph=self.paragraph(body,width-32,6.3,color=colour)
            parts=paragraph.split(width-32,24)
            if parts:
                part=parts[0];_,hh=part.wrap(width-32,24);part.drawOn(self.c,x+16,H-panel_y-19-hh)

    def progress_card(self,x,y,width,height,label,value,delta,col):
        self.rect(x,y,width,height,colors.HexColor('#eff6ff') if col==BLUE else colors.HexColor('#fcf4ed'),stroke=None)
        self.text(x+13,y+23,label[:43],9,True,col)
        self.text(x+13,y+65,f'{value:g}%',33,True)
        self.text(x+13,y+87,delta,8,color=GRAY)
        self.rect(x+13,y+height-17,width-26,4,colors.HexColor('#dfe4e8'),stroke=None)
        self.rect(x+13,y+height-17,(width-26)*value/100,4,col,stroke=None)

    def detail(self,task,index,selected_analysis=None,subsection=1,section=5):
        stem=f"{section:02d}.{index}"
        analyses=self.parsed[str(task['task_id'])]
        region_note=f" ({selected_analysis['region']})" if selected_analysis and len(analyses)>1 else ''
        title=(f"{stem}.{subsection}  {task['task_name']} / Basic observation{region_note}" if analyses
               else f"{stem}  {task['task_name']}")
        photos=sorted(self.photos[str(task['task_id'])],key=lambda p:p.get('date',''))
        if task.get('source')=='manager' and photos:
            self.manager_detail(task,title,photos)
            return
        if task.get('source')=='vision' and not analyses and not self.draft:
            raise ReportDataError('Final vision task lacks valid observations')
        groups=[selected_analysis] if selected_analysis is not None else analyses or [None]
        for analysis in groups:
            rows=analysis['observations'] if analysis else []
            latest=rows[-1] if rows else None
            chosen=latest.get('photo_file_id') if latest else None
            if not chosen and photos:chosen=photos[-1]['file_id']
            has_task_update=not latest and (task.get('review_note') or task.get('description') or any(
                str(request.get('task_id'))==str(task['task_id'])
                for request in self.p.get('extension_requests',[])))
            self.detail_heading(task,title)
            self.text(38,162,'SITE PHOTO'+(' / '+latest['date'] if latest else ''),9,True,BLUE)
            if latest:self.text(560,162,'OBSERVED INSTALLATION RATIO',9,True,BLUE)
            elif has_task_update:self.text(560,162,'TASK UPDATE',9,True,BLUE)
            if chosen:
                imgrect=(38,184,501 if latest or has_task_update else 766,282)
                actual=self.image(chosen,imgrect)
                if latest:
                    x,y,ww,hh=actual
                    for j,(target,box) in enumerate(latest.get('boxes',{}).items()):
                        col=PALETTE[j%len(PALETTE)];a,b,c,d=box
                        self.rect(x+a*ww,y+b*hh,(c-a)*ww,(d-b)*hh,stroke=col)
                        self.rect(x+a*ww,y+b*hh,42,16,col,stroke=None)
                        self.text(x+a*ww+4,y+b*hh+12,target,8,True,colors.white)
            else:
                if not self.draft:raise ReportAssetError('Final task requires a site photo')
                self.rect(38,184,501 if has_task_update else 766,282,colors.HexColor('#f4f5f5'),stroke=None)
                self.text(60,205,'현장 사진 미수신',14,True,GRAY)
            if has_task_update:self.manager_notes(task,{'description':''},560,184,244,282)
            if latest:
                vals=list(latest['values'].items())
                for j,(target,value) in enumerate(vals[:4]):
                    count=min(4,len(vals));ch=min(131,(282-20*(count-1))/count)
                    yy=184+j*(ch+20);col=PALETTE[j%len(PALETTE)]
                    label=target+' / '+latest.get('labels',{}).get(target,target)
                    baseline=next((r for r in rows if target in r['values']),None)
                    delta_text='Observed on '+latest['date']
                    if baseline and baseline['date']!=latest['date']:
                        delta=value-baseline['values'][target]
                        delta_text=f"{delta:+g} pp / since {_day(baseline['date']):%d %b}"
                    if count<=2:self.progress_card(560,yy,244,ch,label,value,delta_text,col)
                    else:
                        self.rect(560,yy,244,ch,colors.HexColor('#eff6ff'),stroke=None)
                        self.text(570,yy+12,label[:40],8,True,col)
                        self.text(570,yy+33,f'{value:g}%',20,True)
                        self.text(640,yy+33,delta_text,7,color=GRAY)
            descriptions=[]
            if task.get('description'):descriptions.append(task['description'])
            for photo in photos:
                if photo.get('description'):
                    description=f"{photo.get('date','')}  {photo['description']}".strip()
                    if description not in descriptions:descriptions.append(description)
            desc='\n'.join(descriptions)
            if desc:
                para=self.paragraph(desc,766,10);parts=para.split(766,90)
                if parts:
                    first=parts[0];_,height=first.wrap(766,90);first.drawOn(self.c,38,H-466-height)
                    pending=parts[1:]
                    while pending:
                        self.page(title+' / Description','task_detail',task)
                        current=pending.pop(0);split=current.split(766,440)
                        if not split:raise ReportDataError('Description cannot fit report page')
                        part=split[0];_,height=part.wrap(766,440);part.drawOn(self.c,38,H-94-height);pending=split[1:]+pending
            if latest and len(latest['values'])>4:
                self.table_pages(title+' / Observed targets','task_detail', ['Target','Installation ratio'],[[latest.get('labels',{}).get(k,k),f'{v:g}%'] for k,v in latest['values'].items()],[570,196],task)
            # Preserve manager photos, including supplements on a vision task.
            remaining=[p for p in photos if p.get('file_id')!=chosen or p.get('source')=='manager']
            if analysis:
                observed_ids={row.get('photo_file_id') for a in analyses for row in a['observations']}
                remaining=[photo for photo in remaining if photo.get('file_id') not in observed_ids or photo.get('source')=='manager']
            for offset in range(0,len(remaining),4):
                self.page(title+' / Site photos','task_detail',task)
                for photo,(x,y) in zip(remaining[offset:offset+4],[(38,113),(432,113),(38,337),(432,337)]):
                    caption=photo.get('date','')
                    if photo.get('source')=='manager':caption+=f"  /  {photo['progress_percent']:g}% - Manager-reported progress"
                    self.text(x,y-10,caption,9,True)
                    self.image(photo['file_id'],(x,y,372,180))
                    if photo.get('description'):
                        p=self.paragraph(photo['description'],372,8);_,hh=p.wrap(372,30)
                        if hh<=30:p.drawOn(self.c,x,H-y-184-hh)

    def overlay_pages(self,task,analysis,number):
        rows=[]
        for row in analysis['observations']:
            ids=row.get('overlay_file_ids') or ([row['overlay_file_id']] if row.get('overlay_file_id') else [])
            rows.extend({**row,'overlay_file_id':fid} for fid in dict.fromkeys(ids))
        title=f"{number}  {task['task_name']} / Segmentation overlays"
        if len(self.parsed[str(task['task_id'])])>1:title+=f" ({analysis['region']})"
        for offset in range(0,len(rows),4):
            subset=rows[offset:offset+4];self.page(title,'overlays',task)
            compact=len(subset)>2
            placements=([(38,116,372,210),(432,116,372,210)] if not compact else
                        [(38,116,372,145),(432,116,372,145),(38,326,372,145),(432,326,372,145)])
            for row,rr in zip(subset,placements):
                x,y,ww,hh=rr
                self.text(x,y-11,_day(row['date']).strftime('%d %b %Y').upper(),9.5,True,GRAY)
                self.image(row['overlay_file_id'],rr,crop=True)
                ratio='    /    '.join(f'{k}  {v:g}%' for k,v in row['values'].items())
                self.text(x,y+hh+20,ratio,12.5,True)
            legend_y=516 if compact else 382
            if analysis['region']=='wall':
                descriptions=('Mostly overlaps board-covered surfaces','Mostly overlaps exposed framing')
            else:
                descriptions=('Detected installed surface','Detected uninstalled or exposed surface')
            for x,width,label,description,fill,colour in [
                (38,372,'Green',descriptions[0],colors.HexColor('#edf7f0'),colors.HexColor('#24855e')),
                (432,372,'Red',descriptions[1],colors.HexColor('#fbefef'),colors.HexColor('#c34d43'))]:
                self.rect(x,legend_y,width,47,fill,stroke=None)
                self.text(x+9,legend_y+18,label,10,True,colour)
                self.text(x+9,legend_y+36,description,8.5)
        return bool(rows)

    def observation_pages(self,task,analysis,number):
        title=f"{number}  {task['task_name']} / Daily installation observations"
        if len(self.parsed[str(task['task_id'])])>1:title+=f" ({analysis['region']})"
        rows=analysis['observations'];targets=list(dict.fromkeys(k for r in rows for k in r['values']))
        for off in range(0,len(targets),2):
            keys=targets[off:off+2]
            for chunk in range(0,len(rows),7):
                window=rows[chunk:chunk+7];self.page(title,'observations',task)
                for j,key in enumerate(keys):
                    x=38+j*394;col=PALETTE[j];self.text(x+25,102,window[-1].get('labels',{}).get(key,key),11,True,col)
                    for tick in [0,25,50,75,100]:
                        y=256-1.37*tick;self.text(x,y+3,f'{tick}%',7,color=GRAY);self.c.setStrokeColor(colors.HexColor('#e7e9e9'));self.c.line(x+30,H-y,x+370,H-y)
                    previous=None
                    for n,row in enumerate(window):
                        xx=x+40+n*320/max(1,len(window)-1);self.text(xx-14,274,row['date'][5:],7,color=GRAY)
                        value=row['values'].get(key)
                        if value is None:previous=None;continue
                        yy=256-value*1.37;self.c.setStrokeColor(col);self.c.setFillColor(col)
                        if previous:self.c.line(previous[0],H-previous[1],xx,H-yy)
                        self.c.circle(xx,H-yy,2,fill=1,stroke=0);self.text(xx-9,yy-7,f'{value:g}%',8,True);previous=(xx,yy)
                widths=[100]+[666/(len(keys)*2)]*(len(keys)*2)
                headers=['Date']+[v for key in keys for v in [key,'Change since prior']]
                cells=[headers]
                for row in window:
                    line=[row['date']];idx=rows.index(row);prior=rows[idx-1] if idx else None
                    for key in keys:
                        value=row['values'].get(key);old=prior['values'].get(key) if prior else None
                        line += ['-' if value is None else f'{value:.2f}%','-' if value is None or old is None else f'{value-old:+.2f} pp']
                    cells.append(line)
                table=Table(cells,colWidths=widths,rowHeights=29)
                table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),11.5),('BACKGROUND',(0,0),(-1,0),INK),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),.35,colors.black),('BOX',(0,0),(-1,-1),.8,colors.black),('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
                _,hh=table.wrap(766,250);table.drawOn(self.c,38,H-303-hh)


    def daily(self,number):
        # Camera records exist independently of vision completion or task evidence.
        days={}
        explicit=self.p.get('daily_site_photos')
        for row in explicit or []:
            day=_day(row['date'])
            if self.start<=day<=self.end:days.setdefault(day.isoformat(),row['file_id'])
        for task in self.tasks:
            if explicit is not None:break
            if task.get('source')!='vision':continue
            for a in self.parsed[str(task['task_id'])]:
                for row in a['observations']:
                    if row.get('photo_file_id'):days.setdefault(row['date'],row['photo_file_id'])
        rows=[((self.start+timedelta(days=i)).isoformat(),days.get((self.start+timedelta(days=i)).isoformat())) for i in range((self.end-self.start).days+1)]
        for offset in range(0,len(rows),5):
            batch=rows[offset:offset+5];self.page(f'{number:02d}  Daily site photos','daily_photos')
            featured=next((row for row in reversed(batch) if row[1]),batch[-1])
            arranged=[featured]+[row for row in batch if row is not featured]
            cards=[(474,122,343,408),(28,122,209,198),(251,122,209,198),
                   (28,334,209,196),(251,334,209,196)]
            for (day,file_id),(x,y,ww,hh) in zip(arranged,cards):
                self.rect(x,y,ww,hh,colors.HexColor('#fafafa'),colors.HexColor('#bebfc0'),3)
                index=(date.fromisoformat(day)-self.start).days+1
                self.text(x+9,y+23,f'{index:02d}  /  {_day(day):%d %b %Y}'.upper(),12,True)
                if file_id:self.image(file_id,(x+6,y+35,ww-12,hh-42),crop=True)
                else:
                    self.text(x+12,y+hh/2,'No camera photo available',11,True,GRAY)
                    self.text(x+12,y+hh/2+20,day,10,color=GRAY)
            missing=[day for day,fid in batch if not fid]
            if missing:
                archive_start=self.p.get('camera_available_start')
                note=(f'Camera archive begins {_day(archive_start):%d %b %Y}; no photos for this reporting period.'
                      if archive_start and _day(archive_start)>self.end else
                      'Missing camera photos: '+', '.join(_day(day).strftime('%d %b') for day in missing))
                self.text(38,549,note,8,color=GRAY)
        return True

    def extension_form(self,number):
        title=f'{number:02d}  Schedule extension request'
        requests=self.p.get('extension_requests') or []
        if not isinstance(requests,list):raise ReportDataError('extension_requests must be a list')
        if not requests:
            self.page(title,'extension_request')
            top_end=self.form_table([['Site',self.p.get('report_site_name',self.p.get('site_name',self.p['site_code'])),'Reporting period',f'{self.start} - {self.end}'],
                ['Affected task / ID','None','Schedule version',self.p['schedule_version']],
                ['Request status','No change requests','Schedule change','None']],90,[34,34,34])
            middle_end=self.form_table([['Reason for request','No schedule or work change requests received for this reporting period.'],
                ['Mitigation / action plan','Not applicable']],max(204,top_end+12),[56,56],widths=[110,656])
            self.text(38,middle_end+22,'REVIEW & APPROVAL',10,True)
            self.form_table([['Review decision','Not applicable','Schedule application','No changes applied'],
                ['Approved task dates','None','Applicable week','None']],middle_end+34,[35,35])
            return
        rows=[]
        decisions={'pending':'[ ] Approved\nPending review','accepted':'[x] Approved','rejected':'[ ] Approved\nRejected'}
        kinds={'extension':'Schedule extension','schedule':'Schedule change','work':'Work change'}
        def period(item,prefix):
            start=item.get(prefix+'_start');finish=item.get(prefix+'_finish')
            if not start or not finish:return 'Not provided'
            return f'{_day(start)} - {_day(finish)}'
        for request in requests:
            if not isinstance(request,dict):raise ReportDataError('Extension request must be an object')
            status=request.get('status')
            if not isinstance(status,str) or status not in decisions:
                raise ReportDataError('Unknown extension request decision status')
            task=request.get('task_name') or request.get('task_id')
            if not task:raise ReportDataError('Extension request task identity is missing')
            work=request.get('request_type')=='work'
            task_text=str(task)+'\n'+kinds.get(request.get('request_type','extension'),'Change request')
            if request.get('request_id'):task_text+='\nRequest: '+str(request['request_id'])
            if request.get('requested_week') is not None:task_text+='\nRequested in week '+str(request['requested_week'])
            dates='Original\n'+period(request,'original')+'\nCurrent\n'+period(request,'current')+'\nRequested\n'+period(request,'proposed')
            if work:dates='Current work\n'+str(request.get('current_work') or 'Original task scope')+'\nProposed work\n'+str(request.get('proposed_work') or 'Not provided')+'\nSchedule dates unchanged'
            decision=decisions[status]
            if request.get('decision_reason'):decision+='\n'+str(request['decision_reason'])
            application='[ ] Applied\nNot applied'
            applied=request.get('applied_at')
            effective_week=request.get('applies_from_week')
            if applied:
                if status!='accepted':raise ReportDataError('Only an accepted extension request can be applied')
                application=('[x] Applied to work' if work else '[x] Applied to schedule')+'\n'+str(applied).replace('T',' ')
                if effective_week is not None:
                    if type(effective_week) is not int or effective_week<1:
                        raise ReportDataError('Extension effective week must be a positive integer')
                    application+=f'\nEffective from week {effective_week}'
                    if effective_week>int(self.p['week']):application+='\nFuture reporting week'
                else:application+='\nEffective week not provided'
            elif status=='accepted':
                application+='\nAwaiting '+('work' if work else 'schedule')+' update'
                if effective_week is not None:application+=f'\nRequested effective week {effective_week}'
            rows.append([task_text,dates,request.get('reason') or 'No reason provided',decision,application])
        for request,row in zip(requests,rows):
            work=request.get('request_type')=='work'
            if (work and max(len(str(request.get('proposed_work') or '')),len(str(request.get('current_work') or '')))>80) or sum(len(str(request.get(key) or '')) for key in ['reason','decision_reason','mitigation_plan','task_name','proposed_work','current_work'])>400:
                self.table_pages(title,'extension_request',
                    ['TASK / REQUEST','DATES / WORK','REASON','REVIEW DECISION','APPLICATION'],
                    [row],[170,185,190,95,126],split_rows=True)
                continue
            self.page(title,'extension_request')
            top_end=self.form_table([['Site',self.p.get('report_site_name',self.p.get('site_name',self.p['site_code'])),'Reporting period',f'{self.start} - {self.end}'],
                ['Affected task / ID',request.get('task_name') or request.get('task_id'),'Request / type',kinds.get(request.get('request_type','extension'),'Change request')+'\n'+str(request.get('request_id',''))],
                ['Original task dates',period(request,'original'),'Current task dates',period(request,'current')],
                ['Proposed work' if work else 'Proposed revised dates',str(request.get('proposed_work') or 'Not provided') if work else period(request,'proposed'),'Requested in week',str(request.get('requested_week',self.p['week']))]],90,[30,40,35,35])
            middle_end=self.form_table([['Reason for request',row[2]],['Mitigation / action plan',request.get('mitigation_plan') or 'Not provided']],max(242,top_end+12),[65,48],widths=[110,656])
            self.text(38,middle_end+22,'REVIEW & APPROVAL',10,True)
            self.form_table([['Review decision' if work else 'Head office approval',row[3],'Work application' if work else 'Schedule application',row[4]],
                ['Approved work' if work else 'Approved task dates',(request.get('proposed_work') if work else period(request,'proposed')) if request['status']=='accepted' else 'None',
                 'Applicable week',str(request.get('applies_from_week') or 'Not applied')]],middle_end+34,[76,38])

    def form_table(self,rows,y,heights,widths=None):
        widths=widths or [110,270,123,263]
        cells=[[self.paragraph(value,width-12,11.5,n%2==0,align=1) for n,(value,width) in enumerate(zip(row,widths))] for row in rows]
        heights=[max(h,max(cell.wrap(w-12,1000)[1] for cell,w in zip(row,widths))+12) for row,h in zip(cells,heights)]
        table=Table(cells,colWidths=widths,rowHeights=heights)
        style=[('GRID',(0,0),(-1,-1),.35,colors.black),('BOX',(0,0),(-1,-1),.8,colors.black),('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE')]
        for n in range(0,len(widths),2):style.append(('BACKGROUND',(n,0),(n,-1),colors.HexColor('#f1efeb')))
        table.setStyle(TableStyle(style));table.wrap(766,500);table.drawOn(self.c,38,H-y-sum(heights))
        return y+sum(heights)


    def overview(self):
        starting=[t['task_name'] for t in self.tasks if _day(t['planned_start'])>=self.start]
        continuing=[t['task_name'] for t in self.tasks if _day(t['planned_start'])<self.start]
        analyzed=[t['task_name'] for t in self.tasks if self.parsed[str(t['task_id'])]]
        project_start=self.p.get('project_start',self.start.isoformat())
        project_finish=self.p.get('project_finish')
        project_period=f'{_day(project_start):%d %B %Y}'+(f' - {_day(project_finish):%d %B %Y}' if project_finish else '')
        requests=[request for request in self.p.get('extension_requests',[]) if request.get('request_type','extension')=='extension']
        if not requests:
            extension_summary='No schedule extension requests this reporting period'
        else:
            states={'pending':'Pending Head Office review','accepted':'Approved','rejected':'Not approved'}
            summaries=[]
            for request in requests[:3]:
                task_name=str(request.get('task_name') or request.get('task_id') or 'Unnamed task')
                summaries.append(f'{task_name} - {states.get(request.get("status"),"Status pending")}')
            extension_summary='  |  '.join(summaries)
            if len(requests)>3:extension_summary+=f'  |  +{len(requests)-3} more'
        rows=[('Site',self.p.get('report_site_name',self.p.get('site_name',self.p['site_code']))+'\n'+self.p.get('site_address','')),
              ('Reporting period',f"Week {self.p['week']} / {self.start:%d} - {self.end:%d %B %Y}"),
               ('Project schedule',project_period),('Schedule extension requests',extension_summary),
              ('Starting this week','  |  '.join(starting) or 'None'),('Continuing work','  |  '.join(continuing) or 'None'),
               ('Vision-analyzed tasks','  |  '.join(analyzed) or 'No vision analysis available for this reporting period')]
        cells=[[self.paragraph('PROJECT INFORMATION',150,12.5,True,colors.white,align=1),self.paragraph('DETAILS',574,12.5,True,colors.white,align=1)]]
        cells += [[self.paragraph(a,150,12.5,True,align=1),self.paragraph(b,574,12.5,align=1)] for a,b in rows]
        header_height=max(34,max(p.wrap(w-22,1000)[1] for p,w in zip(cells[0],[166,600]))+12)
        heights=[header_height]+[max(42,max(p.wrap(w-22,1000)[1] for p,w in zip(row,[166,600]))+22) for row in cells[1:]]
        spare=max(0,427-sum(heights));heights=[heights[0]]+[h+spare/len(rows) for h in heights[1:]]
        table=Table(cells,colWidths=[166,600],rowHeights=heights)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),INK),('GRID',(0,0),(-1,-1),.35,colors.black),('BOX',(0,0),(-1,-1),.8,colors.black),('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),11),('RIGHTPADDING',(0,0),(-1,-1),11)]))
        if sum(heights)>444:
            self.table_pages('01  Project overview','overview',['PROJECT INFORMATION','DETAILS'],rows,[166,600],split_rows=True)
        else:
            self.page('01  Project overview','overview');table.wrap(766,444);table.drawOn(self.c,38,H-103-sum(heights))


    def gantt(self,title,kind,tasks,start,end):
        prior_changes=self.p.get('previous_week_schedule_changes',[]) if kind=='schedule' else []
        note_height=39 if prior_changes else 0
        horizon=max([_day(t['planned_finish']) for t in tasks]+[end])
        count=min(21,max(7,(horizon-start).days+1));dw=460/count
        days=[start+timedelta(days=i) for i in range(count)]
        tasks=sorted(tasks,key=lambda t:(_day(t['planned_start'])<start,
            not bool(self.parsed.get(str(t['task_id'])))))
        widths=[30,174,51,51]+[dw]*count
        boundaries=[38]
        for width in widths:boundaries.append(boundaries[-1]+width)
        # Keep every task row at one shared height, including continuation pages.
        # Measure the longest wrapped label without reducing the readable type.
        measured=[]
        for task in tasks:
            values=[task.get('source_task_id',task['task_id']),task['task_name'],_day(task['planned_start']).strftime('%m.%d'),_day(task['planned_finish']).strftime('%m.%d')]
            cells=[self.paragraph(v,w-10,11,align=1) for v,w in zip(values,widths[:4])]
            for cell in cells:cell.style.leading=12.5
            height=max(32,max(p.wrap(w-10,1000)[1] for p,w in zip(cells,widths[:4]))+7)
            if height>380:raise ReportDataError('Gantt task name exceeds a page')
            measured.append((task,cells,height))
        row_height=max((item[2] for item in measured),default=32)
        measured=[(task,cells,row_height) for task,cells,_ in measured]
        chunks=[];chunk=[];used=36;continuing=False
        for item in measured:
            is_continuing=_day(item[0]['planned_start'])<start
            separator=22 if is_continuing and not continuing else 0
            if chunk and used+item[2]+separator>455-note_height:
                chunks.append(chunk);chunk=[];used=36;continuing=False
                separator=22 if is_continuing else 0
            chunk.append(item);used+=item[2]+separator;continuing|=is_continuing
        chunks.append(chunk)
        for chunk in chunks:
            self.page(title,kind);y=90+note_height;header_height=36
            if prior_changes:
                self.rect(38,89,766,31,colors.HexColor('#f2f6f4'),stroke=None,radius=3)
                summary='; '.join(
                    f"{r.get('task_name','Task')} / {str(r.get('original_finish') or '')[:10]} -> {str(r.get('current_finish') or r.get('proposed_finish') or '')[:10]}"
                    for r in prior_changes[:3])
                if len(prior_changes)>3:summary+=f' (+{len(prior_changes)-3} more)'
                self.text(48,101,'PREVIOUS WEEK SCHEDULE UPDATE',7.7,True,colors.HexColor('#34705e'))
                self.text(48,113,summary[:145],8.3)
            labels=['ID','Planned task','Start','Finish']+[(d.strftime('%m')+'\n'+d.strftime('%d')) if count>14 else d.strftime('%m.%d') for d in days]
            x=38
            for i,(label,width) in enumerate(zip(labels,widths)):
                fill=INK if i<4 else (colors.HexColor('#fff2cc') if days[i-4]<=end else colors.HexColor('#f0efef'))
                self.rect(x,y,width,header_height,fill,stroke=None)
                size=11 if i<4 else 10
                p=self.paragraph(label,width-4,size,i==1,colors.white if i<4 else INK,align=1)
                _,hh=p.wrap(width-4,header_height);p.drawOn(self.c,x+2,H-y-(header_height+hh)/2);x+=width
            horizontal=[y+header_height];y+=header_height
            breaks=[];shown_continuing=False
            for task,cells,height in chunk:
                if _day(task['planned_start'])<start and not shown_continuing:
                    self.rect(38,y,766,22,colors.HexColor('#f1efeb'),stroke=None)
                    self.centered_text(38,y,766,22,'CONTINUING FROM PREVIOUS WEEKS',10.5,True,GRAY)
                    breaks.append((y,y+22));y+=22;horizontal.append(y);shown_continuing=True
                x=38
                for p,width in zip(cells,widths[:4]):
                    self.rect(x,y,width,height,colors.white,stroke=None)
                    _,hh=p.wrap(width-10,height-7);p.drawOn(self.c,x+5,H-y-(height+hh)/2);x+=width
                for day in days:
                    self.rect(x,y,dw,height,colors.HexColor('#fff2cc') if day<=end else colors.white,stroke=None)
                    if _day(task['planned_start'])<=day<=_day(task['planned_finish']):self.rect(x,y+height/2-6,dw,12,colors.HexColor('#7c8482'),stroke=None)
                    x+=dw
                y+=height;horizontal.append(y)
            # Stroke each interior edge once, after every background/work bar.
            # Continuous vertical paths avoid darker repeated segment joins.
            self.c.saveState();self.c.setStrokeColor(colors.HexColor('#b8bcbc'));self.c.setLineWidth(.4);self.c.setLineCap(0)
            state = ReportGraphicsState()
            state._d = self.c._extgstate._d.copy()
            state._c = self.c._extgstate._c
            self.c._extgstate = state
            self.c._extgstate.set(self.c, 'SA', True)
            grid=self.c.beginPath()
            for yy in sorted(set(horizontal)):
                if yy<y:grid.moveTo(38,H-yy);grid.lineTo(804,H-yy)
            for xx in boundaries[1:-1]:
                top=90+note_height
                for before,after in breaks:
                    grid.moveTo(xx,H-top);grid.lineTo(xx,H-before);top=after
                if top<y:grid.moveTo(xx,H-top);grid.lineTo(xx,H-y)
            self.c.drawPath(grid,stroke=1,fill=0);self.c.restoreState()
            self.rect(38,90+note_height,766,y-90-note_height,stroke=colors.black,line_width=.8)


    def status(self):
        rows=[]
        for task in self.tasks:
            analyses=self.parsed[str(task['task_id'])]
            source=task.get('source','missing')
            if analyses:
                values=[v for a in analyses for v in a['observations'][-1]['values'].values()]
                result=' / '.join(f'{v:g}%' for v in values)
                method='Image analysis data'
            elif source=='manager' and self.photos[str(task['task_id'])]:
                values=[self.photos[str(task['task_id'])][-1]['progress_percent']]
                result=f'{values[0]:g}%'
                method='Manager data received'
            else:
                values=[];result='-';method='No analysis or submission'
            if task.get('completion_confirmed') or task.get('schedule_status')=='Head office completion confirmed':
                state='Completed'
            elif not values:
                state='Field submission needed'
            elif all(value>=100 for value in values):
                state='HQ confirmation needed'
            elif _day(task['planned_finish'])<=self.end:
                state='Delayed'
            else:
                state='Incomplete'
            rows.append([task['task_name']+'\nID '+str(task.get('source_task_id',task['task_id'])),
                f"{_day(task['planned_start']):%m.%d} - {_day(task['planned_finish']):%m.%d}",
                method,result,state])
        self.table_pages('03  Planned work & observation status','status',
            ['Task / Source ID','Planned dates','Verification method','Verification result','Schedule status'],rows,[178,90,135,200,163],font_size=11.5,vertical_padding=6)

    def build(self):
        self.page('Weekly Construction Observation Report','cover')
        cover_photo=self.p.get('cover_photo_file_id')
        if cover_photo:
            self.c.drawImage(ImageReader(str(self.asset(cover_photo))),0,0,width=W,height=H,mask='auto')
            self.c.saveState()
            self.c.setFillColor(colors.black)
            for step in range(600):
                x=step*W/600;t=max(0,min(1,(.66-x/W)/.66))
                self.c.setFillAlpha(.68*t*t*(3-2*t))
                self.c.rect(x,0,W/600+.1,H,stroke=0,fill=1)
            self.c.restoreState()
            self.text(54,H-507,'alo',48,True,colors.white)
            self.text(54,H-477,'RETAIL CONSTRUCTION / '+self.p.get('display_site_name',self.p['site_code']).upper(),10,color=colors.white)
            self.text(54,H-363,'Weekly Construction',17,color=colors.white)
            self.text(54,H-340,'Observation Report',17,color=colors.white)
            self.text(54,H-252,self.p.get('brand_name','ALO YOGA'),31,True,colors.white)
            name=self.p.get('store_title',self.p.get('site_name',self.p['site_code']))
            size=min(37,510/max(1,pdfmetrics.stringWidth(name,_font(name,True),1)))
            self.text(54,H-210,name,size,True,colors.white)
            self.text(54,H-179,self.p.get('site_address',''),18,color=colors.white)
            period=(f'{self.start:%d}-{self.end:%d %B %Y}' if self.start.month==self.end.month else f'{self.start:%d %b} - {self.end:%d %b %Y}').upper()
            self.text(54,H-126,period,25,True,colors.white)
            self.text(54,H-99,f"WEEK {int(self.p['week']):02d}"+(' / DRAFT' if self.draft else ''),15,True,colors.white)
            if self.p.get('project_start') and self.p.get('project_finish'):
                self.text(54,H-54,f"PROJECT SCHEDULE   {_day(self.p['project_start']):%d %b} - {_day(self.p['project_finish']):%d %b %Y}".upper(),10,color=colors.white)
        else:
            self.rect(0,92,W,H-92,INK,stroke=None)
            self.text(48,198,self.p.get('site_name',self.p['site_code']),32,True,colors.white)
            self.text(48,250,f"WEEK {int(self.p['week']):02d}",26,True,colors.white)
            self.text(48,295,f'{self.start} - {self.end}',25,True,colors.white)
            self.text(48,350,'DRAFT' if self.draft else 'SUBMISSION REPORT',15,True,colors.white)
            self.text(48,390,f"Schedule version: {self.p['schedule_version']}",11,color=colors.white)
        self.overview()
        self.gantt("02  This week's schedule",'schedule',self.tasks,self.start,self.end)
        self.status()
        number=4
        self.extension_form(number);number+=1
        self.daily(number);number+=1
        detail_section=number
        for index,task in enumerate(self.tasks,1):
            analyses=self.parsed[str(task['task_id'])]
            if not analyses:self.detail(task,index,section=detail_section)
            subsection=1
            for analysis in analyses:
                self.detail(task,index,analysis,subsection,detail_section)
                base=f'{detail_section:02d}.{index}'
                has_overlay=any(row.get('overlay_file_id') or row.get('overlay_file_ids') for row in analysis['observations'])
                if has_overlay:
                    subsection+=1
                    self.overlay_pages(task,analysis,base+f'.{subsection}')
                subsection+=1
                self.observation_pages(task,analysis,base+f'.{subsection}')
                subsection+=1
        number=detail_section+1
        following=self.p.get('next_week_tasks',[])
        if following:
            next_monday=self.start+timedelta(days=7)
            self.gantt(f"{number:02d}  Next week's plan",'next_week',following,next_monday,next_monday+timedelta(days=4))
        self.finish_page()
        self.c.save()
        return {'path':str(self.out.resolve()),'page_count':len(self.pages),'pages':self.pages,'draft':self.draft,'material_digest':self.p.get('material_digest'),'asset_ids':list(self.asset_cache)}


def render_report(payload: dict, output_path, resolve_asset: Callable[[str], Path]) -> dict:
    """Write a PDF and return a page manifest. Asset failures abort, never fabricate."""
    destination=Path(output_path)
    temporary=destination.with_name(destination.name+'.rendering')
    try:
        result=Renderer(payload,temporary,resolve_asset).build()
        temporary.replace(destination)
        result['path']=str(destination.resolve())
        return result
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
