"""API-only report worker. Credentials stay in environment variables.

Manual requests and Sunday requests are consumed by the same run_job function.
No database access, SQL, camera login, arbitrary JSON URL fetching, or LLM calls.
"""
import argparse
import hashlib
import io
import json
import os
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from pypdf import PdfReader
from report_renderer import render_report

CONTRACT = 'alo-report-input/2'
TEMPLATE = 'aloha-weekly-renderer-v1'
GENERATOR = 'alo-formatted-worker/20'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class ProjectClient:
    def __init__(self, base=None, token=None, transport=None):
        base = base or os.environ.get('ALO_PROJECT_API_BASE', 'http://127.0.0.1:8002')
        parsed = urlsplit(base)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('API base must be an origin without credentials or paths')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')):
            raise ValueError('Remote API requires HTTPS; SSH loopback HTTP is supported')
        token = token or os.environ.get('ALO_PROJECT_AGENT_TOKEN', '')
        if not token:
            raise ValueError('Agent token is required')
        self.http = httpx.Client(base_url=base, headers={'Authorization': 'Bearer ' + token},
            timeout=120, follow_redirects=False, trust_env=False, transport=transport)

    def request(self, method, path, **kwargs):
        if not path.startswith('/api/v1/') or path.startswith('//'):
            raise ValueError('Only scoped project API paths are supported')
        # Repeat only the exact same idempotent body/file UUID after a transport/server failure.
        for attempt in range(3):
            try:
                response = self.http.request(method, path, **kwargs)
                if response.status_code >= 500 and attempt < 2:
                    time.sleep(.5)
                    continue
                response.raise_for_status()
                return response
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(.5)

    def get(self, path):
        return self.request('GET', path).json()

    def post(self, path, body):
        return self.request('POST', path, json=body).json()

    def file(self, site, fid):
        return self.request('GET', f'/api/v1/sites/{quote(site, safe="")}/files/{UUID(fid)}').content

    def upload(self, site, fid, data):
        return self.request('POST', f'/api/v1/sites/{quote(site, safe="")}/files',
            params={'file_id': fid, 'filename': 'weekly-report.pdf', 'purpose': 'report'}, content=data,
            headers={'Content-Type': 'application/octet-stream', 'X-Content-SHA256': hashlib.sha256(data).hexdigest()}).json()


def validate_pdf(data, manifest, payload):
    reader = PdfReader(io.BytesIO(data), strict=True)
    pages = manifest['pages']
    if reader.is_encrypted or len(reader.pages) != len(pages) or not pages:
        raise ValueError('PDF page manifest mismatch')
    kinds = {p['kind'] for p in pages}
    if not {'cover', 'overview', 'schedule', 'status', 'daily_photos', 'extension_request'} <= kinds:
        raise ValueError('Required report section missing')
    detailed = {p['task_id'] for p in pages if p['kind'] == 'task_detail'}
    if detailed != {t['task_id'] for t in payload['tasks']}:
        raise ValueError('Task detail coverage mismatch')
    by_id = {t['task_id']: t for t in payload['tasks']}
    for page in pages:
        if page['kind'] in {'overlays', 'observations'} and by_id[page['task_id']]['source'] != 'vision':
            raise ValueError('Manager task leaked into analysis sections')
    if not all(page.extract_text().strip() for page in reader.pages):
        raise ValueError('Empty PDF page')
    return len(reader.pages)


def run_job(client, site, job, cache):
    report_id = str(UUID(job['id']))
    root = f'/api/v1/weekly/sites/{quote(site, safe="")}/report-jobs/{report_id}'
    claim = str(uuid4())
    client.post(root + '/claim', {'id': claim})
    payload = client.get(root + '/input')
    expected = payload.pop('input_digest')
    if digest(payload) != expected:
        raise ValueError('Frozen input digest mismatch')
    if (payload.get('schema_version'), payload.get('template_version'), payload.get('generator_version')) != (CONTRACT, TEMPLATE, GENERATOR):
        raise ValueError('Unsupported report contract or renderer version')
    directory = Path(cache) / report_id
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    last_heartbeat = time.monotonic()
    try:
        for fid, metadata in sorted(payload['asset_manifest'].items()):
            fid = str(UUID(fid))
            path = directory / fid
            data = path.read_bytes() if path.exists() else client.file(site, fid)
            if hashlib.sha256(data).hexdigest() != metadata['sha256'] or len(data) != metadata['size_bytes']:
                raise ValueError('Report asset integrity mismatch')
            if not path.exists():
                path.write_bytes(data)
            paths[fid] = path
            if time.monotonic() - last_heartbeat > 60:
                client.post(root + '/heartbeat', {'id': claim}); last_heartbeat = time.monotonic()
        render_input = {**payload, 'material_digest': expected,
                        'ready_for_final': payload['generation_mode'] == 'final' and payload['ready_for_final']}
        manifest = render_report(render_input, directory / 'report.pdf', lambda fid: paths[fid])
        data = (directory / 'report.pdf').read_bytes()
        count = validate_pdf(data, manifest, payload)
        client.post(root + '/heartbeat', {'id': claim})
        file_id = str(uuid5(NAMESPACE_URL, 'alo-report:' + report_id + ':' + expected))
        uploaded = client.upload(site, file_id, data)
        if str(uploaded['id']) != file_id:
            raise ValueError('Uploaded report identity mismatch')
        validation = {'schema_version': CONTRACT, 'template_version': TEMPLATE, 'generator_version': GENERATOR,
            'pdf_sha256': hashlib.sha256(data).hexdigest(), 'page_count': count,
            'verified_asset_ids': sorted(paths), 'structure_checks_passed': True}
        result = client.post(root + '/result', {'claim_id': claim, 'status': 'validated',
            'input_digest': expected, 'report_file_id': file_id, 'validation': validation})
        (directory / 'verification.json').write_text(json.dumps({'report_id': report_id, 'input_digest': expected,
            **validation, 'pages': manifest['pages']}, indent=2, ensure_ascii=False), encoding='utf-8')
        return result
    except (ValueError, OSError, KeyError) as exc:
        # Detailed local paths or remote credentials are never copied into a user-visible error.
        client.post(root + '/result', {'claim_id': claim, 'status': 'failed', 'input_digest': expected,
                    'error': 'Report generation validation failed: ' + type(exc).__name__})
        raise


def tick(client, cache, *, schedule=False):
    results = []
    projects = client.get('/api/v1/weekly/agent-projects')['items']
    sites = set()
    for project in projects:
        site = project['site_code']; sites.add(site)
        if not schedule:
            continue
        root = f'/api/v1/weekly/sites/{quote(site, safe="")}/schedules/{project["schedule_id"]}'
        start = project['due_week_start']
        try:
            data = client.get(root + '/report-input?week_start=' + start)
            if not data['ready_for_final']:
                results.append({'site': site, 'week_start': start, 'status': 'awaiting_materials'})
                continue
            request_id = str(uuid5(NAMESPACE_URL, 'alo-auto:' + site + ':' + project['schedule_id'] + ':' + start + ':' + data['input_digest']))
            results.append(client.post(root + '/reports/auto', {'id': request_id, 'week_start': start, 'mode': 'final'}))
        except httpx.HTTPStatusError as exc:
            results.append({'site': site, 'status': 'request_failed', 'http_status': exc.response.status_code})
    for site in sorted(sites):
        jobs = client.get(f'/api/v1/weekly/sites/{quote(site, safe="")}/report-jobs')['items']
        for job in jobs:
            try:
                results.append(run_job(client, site, job, cache))
            except Exception as exc:
                results.append({'site': site, 'report_id': job['id'], 'status': 'worker_error', 'error_type': type(exc).__name__})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--schedule', action='store_true', help='Also request eligible Sunday reports')
    parser.add_argument('--cache', default=os.environ.get('ALO_PROJECT_REPORT_CACHE', './report-cache'))
    args = parser.parse_args()
    client = ProjectClient()
    while True:
        for result in tick(client, args.cache, schedule=args.schedule):
            print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.once:
            break
        time.sleep(60)
