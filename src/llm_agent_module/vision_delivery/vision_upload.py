"""주차별 비전 최종 JSON을 검증하고 운영 백엔드에 전달합니다.

환경 변수: ALO_API_BASE, ALO_WORKFLOW_VISION_TOKEN. 관리자 토큰은 사용하지 않습니다.
--contract와 --dry-run은 조회만 하며, 최종 입력의 일반 실행만 POST합니다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

MAX_JSON_BYTES=32*1024*1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


class VisionClient:
    def __init__(self,base=None,token=None,opener=None):
        self.base=(base or os.environ.get('ALO_API_BASE','http://127.0.0.1:8000')).rstrip('/')
        address=urlsplit(self.base)
        if address.scheme not in {'http','https'} or not address.netloc or address.username or address.password or address.query or address.fragment:
            raise ValueError('ALO_API_BASE must be an HTTP(S) backend URL without credentials or query parameters')
        if address.path not in ('', '/') or (address.scheme == 'http' and address.hostname not in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('Use an HTTPS API origin; HTTP is allowed only on loopback')
        self.token=token if token is not None else os.environ.get('ALO_WORKFLOW_VISION_TOKEN','')
        if not self.token:raise ValueError('ALO_WORKFLOW_VISION_TOKEN is required')
        self.opener=opener or build_opener(ProxyHandler({}),_NoRedirect())

    def request(self,path,data=None):
        if not path.startswith('/') or path.startswith('//') or any(c in path for c in '\r\n'):raise ValueError('Expected a backend API path')
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'}
        request=Request(self.base+path,data=data,headers=headers)
        try:
            with self.opener.open(request,timeout=120) as response:
                result=response.read(MAX_JSON_BYTES+1)
                if len(result)>MAX_JSON_BYTES:raise ValueError('Backend JSON exceeds the 32 MiB limit')
                return result
        except HTTPError as exc:
            message=exc.read(4096).decode(errors='replace').replace(self.token,'[redacted]')
            raise ValueError(f'Backend {exc.code}: {message}') from None

    def json(self,path,body=None):
        raw=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8') if body is not None else None
        return json.loads(self.request(path,raw))

    def publish_raw(self,path,raw):
        return json.loads(self.request(path,raw))


def _load(raw):
    if len(raw)>MAX_JSON_BYTES:raise ValueError('Input JSON exceeds the 32 MiB limit')
    def nonfinite(value):raise ValueError('JSON cannot contain non-finite numbers')
    def finite_float(value):
        number=float(value)
        if not math.isfinite(number):raise ValueError('JSON cannot contain non-finite numbers')
        return number
    def unique_object(pairs):
        obj={}
        for key,value in pairs:
            if key in obj:raise ValueError('JSON contains a duplicate object key: '+key)
            obj[key]=value
        return obj
    value=json.loads(raw.decode('utf-8-sig'),parse_constant=nonfinite,parse_float=finite_float,object_pairs_hook=unique_object)
    if not isinstance(value,dict):raise ValueError('Input JSON must be an object')
    return value


def submit(*,site,week,client,input_path=None,stored_file_id=None,dry_run=False,contract_only=False):
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',site):raise ValueError('Invalid site code')
    if isinstance(week,bool) or not isinstance(week,int) or not 1<=week<=1000:raise ValueError('week must be 1..1000')
    if sum([bool(input_path),bool(stored_file_id),bool(contract_only)])!=1:raise ValueError('Choose exactly one of --input, --stored-file-id, or --contract')
    if contract_only and dry_run:raise ValueError('--contract is already read-only; omit --dry-run')
    prefix=f'/workflow/sites/{site}/weeks/{week}'
    contract=client.json(prefix+'/vision-contract')
    if contract.get('site_code')!=site or contract.get('week')!=week:raise ValueError('Backend contract does not match the selected site/week')
    if contract_only:return contract
    fid=None
    if stored_file_id:
        try:fid=str(UUID(str(stored_file_id)))
        except (ValueError,TypeError,AttributeError):raise ValueError('--stored-file-id must be a registered backend UUID') from None
        raw=client.request('/files/'+fid+'/download')
    else:raw=Path(input_path).read_bytes()
    payload=_load(raw)
    from vision_contract import validate_delivery
    normalized=validate_delivery(payload,contract)
    if dry_run:
        results=normalized['results']
        return {'dry_run':True,'delivery':normalized['delivery'],'analysis_count':len(results),'task_ids':sorted({x['task_id'] for x in results}),'observation_count':sum(len(x['observations']) for x in results),'skipped':normalized.get('skipped',[]),'media_verification':'UUID syntax and mappings validated locally; server validates actual registered media on final submission.'}
    if fid:return client.json(prefix+'/vision-imports',{'result_file_id':fid})
    # Preserve the submitted document. The server stores it and commits the
    # normalized final result and readiness update; this client never polls.
    return client.publish_raw(prefix+'/vision-results',raw)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site',required=True);parser.add_argument('--week',required=True,type=int)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input',type=Path);group.add_argument('--stored-file-id');group.add_argument('--contract',action='store_true')
    parser.add_argument('--dry-run',action='store_true');args=parser.parse_args(argv)
    try:
        result=submit(site=args.site,week=args.week,client=VisionClient(),input_path=args.input,stored_file_id=args.stored_file_id,dry_run=args.dry_run,contract_only=args.contract)
    except (ValueError,OSError,URLError) as exc:parser.error(str(exc))
    print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))


if __name__=='__main__':main()
